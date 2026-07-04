import logging
import datetime
import os
import uuid
import hashlib
import pathlib
from typing import List, Optional, Any, Dict
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from .v2_engine.models import SpecNode, ExecutionState, SpecStatus, ExecutionStage, FailureClassification, Evidence, NodeLevel, SurfaceType, Contract, Metric
from .v2_engine.database import get_all_spec_nodes, get_execution_state, get_spec_node, save_spec_node, save_execution_state, get_impacted_subgraph
from .v2_engine.handlers import detect_graph_gaps, _all_nodes_for_repo, cascade_blast_radius, recalculate_confidence, _now

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("turingmind_api")

app = FastAPI(title="TuringMind V2 Constraint Graph API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5103", "http://localhost:5101", "http://127.0.0.1:5103", "http://127.0.0.1:5101"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Health Check ────────────────────────────────────────────────────────────
@app.get("/api/v2/health")
def health_check():
    """Simple liveness probe for the daemon supervisor."""
    return {"status": "ok"}

# ── Phase 2.5a: Security Scanning ───────────────────────────────────────────
from .v2_engine.security_scanner import SecurityScanner

# Cache scanner instances per workspace to persist dedup hashes across cycles
_scanner_cache: dict[str, SecurityScanner] = {}

class SecurityCycleRequest(BaseModel):
    repo: str
    workspace_dir: str = ""

@app.post("/api/v2/security/cycle")
def run_security_cycle(request: SecurityCycleRequest):
    """Daemon calls this every poll cycle. Runs incremental OpenGrep scan
    on changed files and returns new security findings.
    
    The scanner deduplicates findings across calls via in-memory hash set,
    so calling this repeatedly will not produce duplicate gaps.
    """
    if not request.repo:
        raise HTTPException(status_code=400, detail="repo is required")
    
    workspace = request.workspace_dir
    if not workspace:
        # Derive workspace from common repo layout
        workspace = str(pathlib.Path.cwd())
    
    try:
        # Get or create scanner for this workspace
        if workspace not in _scanner_cache:
            _scanner_cache[workspace] = SecurityScanner(workspace)
        scanner = _scanner_cache[workspace]
        
        result = scanner.run_security_cycle(request.repo)
        
        return {
            "scan_ok": result.scan_ok,
            "findings_total": result.findings_total,
            "findings_new": result.findings_new,
            "findings_duplicate": result.findings_duplicate,
            "blindspots": result.blindspots,
            "gaps_injected": result.gaps_injected,
            "error_message": result.error_message,
        }
    except Exception as e:
        logger.error(f"Security cycle failed: {e}")
        return {
            "scan_ok": False,
            "findings_total": 0,
            "findings_new": 0,
            "error_message": str(e),
        }

@app.get("/api/v2/security/rules")
def list_security_rules(workspace_dir: str = ""):
    """List all OpenGrep rules (active + quarantined) with status metadata."""
    workspace = workspace_dir or str(pathlib.Path.cwd())
    
    if workspace not in _scanner_cache:
        _scanner_cache[workspace] = SecurityScanner(workspace)
    scanner = _scanner_cache[workspace]
    
    rules = scanner.list_rules()
    active = sum(1 for r in rules if r["status"] == "active")
    quarantined = sum(1 for r in rules if r["status"] == "quarantined")
    
    return {
        "rules": rules,
        "total": len(rules),
        "active": active,
        "quarantined": quarantined,
    }


class QuarantineRequest(BaseModel):
    rule_id: str
    reason: str = ""
    workspace_dir: str = ""

@app.post("/api/v2/security/quarantine")
def quarantine_rule(request: QuarantineRequest):
    """Move a rule from .opengrep/rules/ to .opengrep/archive/."""
    workspace = request.workspace_dir or str(pathlib.Path.cwd())
    rules_dir = pathlib.Path(workspace) / ".opengrep" / "rules"
    archive_dir = pathlib.Path(workspace) / ".opengrep" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    import shutil

    rule_file = rules_dir / request.rule_id
    if not rule_file.exists():
        rule_file = rules_dir / f"{request.rule_id}.yml"

    if not rule_file.exists():
        raise HTTPException(status_code=404, detail=f"Rule not found: {request.rule_id}")

    dest = archive_dir / rule_file.name
    shutil.move(str(rule_file), str(dest))

    return {
        "quarantined": rule_file.name,
        "moved_to": str(dest),
        "reason": request.reason,
        "active": False,
    }


@app.post("/api/v2/security/validate")
def validate_rules(workspace_dir: str = ""):
    """Run self-tests on all rules to detect broken ones."""
    workspace = workspace_dir or str(pathlib.Path.cwd())
    
    if workspace not in _scanner_cache:
        _scanner_cache[workspace] = SecurityScanner(workspace)
    scanner = _scanner_cache[workspace]
    
    results = scanner.self_test_rules()
    passed = sum(1 for r in results if r["status"] == "passed")
    broken = sum(1 for r in results if r["status"] == "broken")
    no_fixtures = sum(1 for r in results if r["status"] == "no_fixtures")
    
    return {
        "results": results,
        "total": len(results),
        "passed": passed,
        "broken": broken,
        "no_fixtures": no_fixtures,
    }


@app.post("/api/v2/security/prune")
def prune_rules(workspace_dir: str = ""):
    """Run rule pruning: detect broken/dormant/dead rules and inject gaps."""
    workspace = workspace_dir or str(pathlib.Path.cwd())
    
    if workspace not in _scanner_cache:
        _scanner_cache[workspace] = SecurityScanner(workspace)
    scanner = _scanner_cache[workspace]
    
    gaps = scanner.prune_rules()
    return {
        "gaps_injected": gaps,
        "pruning_actions": len(gaps),
    }



# ── Stage 4: Decision Queue ─────────────────────────────────────────────────
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# Cache the latest security gaps so they merge into the decision queue
_security_gaps_cache: list[dict] = []

# X-3: TTL cache for prune gaps — avoids running opengrep self-tests on every DQ poll.
# Tuple of (timestamp_seconds, gaps_list).  Re-run prune_rules() only when stale.
_prune_cache: tuple[float, list[dict]] = (0.0, [])
_PRUNE_TTL_SECONDS = 300  # 5 minutes

@app.get("/api/v2/decision-queue")
def get_decision_queue(repo: str, limit: int = 20):
    """Return prioritized action items derived from graph gap analysis + security findings."""
    global _prune_cache

    if not repo:
        raise HTTPException(status_code=400, detail="repo is required")
    try:
        gaps = detect_graph_gaps(repo)
        
        # Merge in any security gaps from the latest scan cycle
        for scanner in _scanner_cache.values():
            last_result = scanner.run_security_cycle(repo)
            if last_result.gaps_injected:
                gaps.extend(last_result.gaps_injected)

        # X-3: Merge in rule health gaps from prune_rules() with TTL cache
        import time as _time
        now = _time.time()
        if now - _prune_cache[0] > _PRUNE_TTL_SECONDS:
            prune_gaps: list[dict] = []
            for scanner in _scanner_cache.values():
                prune_gaps.extend(scanner.prune_rules())
            _prune_cache = (now, prune_gaps)
        gaps.extend(_prune_cache[1])
        
        # Sort by severity (critical first)
        gaps.sort(key=lambda g: SEVERITY_ORDER.get(g.get("severity", "low"), 99))
        return {
            "queue": gaps[:limit],
            "total": len(gaps),
            "repo": repo,
        }
    except Exception as e:
        logger.error(f"Error building decision queue: {e}")
        return {"queue": [], "total": 0, "repo": repo}


class ClusterMeta(BaseModel):
    type: str = "unknown"            # refactor_burst, cross_module, targeted_fix, non_code, development
    severity: str = "low"            # low, medium, high
    description: str = ""
    duration_ms: int = 0
    edit_counts: dict[str, int] = {} # file → edit count within the cluster

class SyncPayload(BaseModel):
    repo: str
    files: list[str]
    cluster: ClusterMeta | None = None

class CreateNodePayload(BaseModel):
    repo: str
    title: str
    level: str                          # L0_SYSTEM, L1_FILE, L2_EXTERNAL, L3_API, L6_PHASE, L7_PROJECT
    surface_type: str = "internal"      # api_endpoint, internal, job, hardware_bridge
    contract: dict = {}                 # {invariants: [], metrics: [], inputs: {}, outputs: {}}
    dependencies: list[str] = []
    priority: str = "medium"
    governance_tier: Optional[str] = None
    effort_days: Optional[float] = None
    complexity: Optional[str] = None
    intent_justification: Optional[str] = None

class UpdateNodePayload(BaseModel):
    contract: dict = {}
    dependencies: list[str] = []
    surface_type: Optional[str] = None
    priority: Optional[str] = None
    effort_days: Optional[float] = None
    complexity: Optional[str] = None
    intent_justification: Optional[str] = None

class IntentRecord(BaseModel):
    text: str               # Raw text of the intent item (e.g. checklist line)
    kind: str = "task"      # "task" | "plan_section" | "goal"
    node_id: Optional[str] = None  # Link to an existing SpecNode if known

class IntentPayload(BaseModel):
    repo: str
    source_file: str        # Which plan file this came from (e.g. "task.md")
    records: list[IntentRecord]
    agent: str = "antigravity"

@app.post("/api/v2/intent")
def capture_intent(payload: IntentPayload):
    """Store planning intent records before code is written.
    Called automatically by `turingmind plan` when task.md or implementation_plan.md changes."""
    if not payload.repo or not payload.records:
        raise HTTPException(status_code=400, detail="repo and records are required")

    import os as _os, json as _json

    # Use TURINGMIND_DATA_DIR if set, otherwise fall back to CWD
    data_root = pathlib.Path(_os.environ.get("TURINGMIND_DATA_DIR", "."))
    log_dir = data_root / ".turingmind"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "intent_log.json"

    existing: list = []
    if log_path.exists():
        try:
            existing = _json.loads(log_path.read_text())
        except Exception:
            existing = []

    # Deduplication: hash the record texts to skip identical re-submissions
    record_dicts = [r.model_dump() for r in payload.records]
    content_hash = hashlib.sha256(
        _json.dumps(record_dicts, sort_keys=True).encode()
    ).hexdigest()[:12]

    if existing and existing[-1].get("content_hash") == content_hash:
        logger.info(f"Intent duplicate skipped for {payload.source_file} (hash={content_hash})")
        return {
            "status": "duplicate_skipped",
            "records": len(payload.records),
            "repo": payload.repo,
            "source_file": payload.source_file,
            "content_hash": content_hash,
        }

    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    entry = {
        "timestamp": timestamp,
        "repo": payload.repo,
        "source_file": payload.source_file,
        "agent": payload.agent,
        "content_hash": content_hash,
        "records": record_dicts,
    }
    existing.append(entry)

    # Cap at 200 entries to prevent unbounded growth
    MAX_INTENT_LOG = 200
    if len(existing) > MAX_INTENT_LOG:
        existing = existing[-MAX_INTENT_LOG:]

    log_path.write_text(_json.dumps(existing, indent=2))

    # Captured intent doubles as ephemeral memory: agents recalling context for
    # this repo should see what was recently planned, without reading log files.
    try:
        _memory_manager().create_session_context(
            repo=payload.repo,
            content=f"Plan intent from {payload.source_file}: "
            + "; ".join(r.text for r in payload.records[:20]),
            scope="repo",
            evidence=[{"type": "intent", "content": payload.source_file}],
        )
    except Exception as e:
        logger.warning(f"Intent memory write failed (non-fatal): {e}")

    logger.info(f"Captured {len(payload.records)} intent records from {payload.source_file} for {payload.repo}")
    return {
        "status": "captured",
        "records": len(payload.records),
        "repo": payload.repo,
        "source_file": payload.source_file,
        "timestamp": timestamp,
        "content_hash": content_hash,
    }


# ── Memory endpoints — REST surface over the legacy memory store ─────────────
# Lets hooks (Cursor afterFileEdit, Antigravity pre-push) and the CLI write and
# recall memories without speaking MCP stdio.

_memory_db_instance = None
_memory_manager_instance = None


def _memory_db():
    global _memory_db_instance
    if _memory_db_instance is None:
        from .database import MemoryDatabase
        _memory_db_instance = MemoryDatabase()
    return _memory_db_instance


def _memory_manager():
    global _memory_manager_instance
    if _memory_manager_instance is None:
        from .memory_manager import MemoryManager
        _memory_manager_instance = MemoryManager(_memory_db())
    return _memory_manager_instance


class MemorySavePayload(BaseModel):
    repo: str
    type: str                       # learned_pattern | session_context | explicit_rule
    content: str
    scope: str = "repo"
    confidence: float = 0.7
    node_id: Optional[str] = None   # optional SpecNode link
    evidence: list[dict] = []
    ttl_hours: Optional[int] = None # session_context expiry (default 24h)


@app.post("/api/v2/memory")
def save_memory(payload: MemorySavePayload):
    """Save a memory entry. learned_pattern saves are reinforcing: an existing
    pattern with the same content/scope gains confidence instead of duplicating."""
    if not payload.repo or not payload.content or not payload.type:
        raise HTTPException(status_code=400, detail="repo, type, and content are required")

    db = _memory_db()
    manager = _memory_manager()
    reason = "; ".join(
        str(e.get("content", "")) for e in payload.evidence if e.get("content")
    ) or None

    try:
        if payload.type == "learned_pattern":
            memory_id = manager.learn_pattern_from_feedback(
                repo=payload.repo,
                pattern=payload.content,
                file_path=None if payload.scope == "repo" else payload.scope,
                reason=reason,
            )
        elif payload.type == "session_context":
            memory_id = manager.create_session_context(
                repo=payload.repo,
                content=payload.content,
                scope=payload.scope,
                evidence=payload.evidence,
                expires_in_hours=payload.ttl_hours or 24,
            )
        else:
            memory_id = db.create_memory_entry(
                repo=payload.repo,
                memory_type=payload.type,
                content=payload.content,
                scope=payload.scope,
                confidence=payload.confidence,
                node_id=payload.node_id,
            )
            for ev in payload.evidence:
                db.add_evidence(
                    memory_id=memory_id,
                    evidence_type=ev.get("type", "manual"),
                    content=ev.get("content", ""),
                    file_path=ev.get("file"),
                    line_number=ev.get("line"),
                )
    except Exception as e:
        logger.exception("Memory save failed")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    if payload.node_id and payload.type in ("learned_pattern", "session_context"):
        # Manager paths don't take node_id — link after the fact.
        try:
            with db.transaction() as cursor:
                cursor.execute(
                    "UPDATE memory_entries SET node_id = ? WHERE memory_id = ?",
                    (payload.node_id, memory_id),
                )
        except Exception as e:
            logger.warning(f"Memory node link failed (non-fatal): {e}")

    return {"status": "saved", "memory_id": memory_id, "type": payload.type, "repo": payload.repo}


@app.get("/api/v2/memory")
def list_memory(
    repo: str,
    search: Optional[str] = None,
    type: Optional[str] = None,
    status: str = "active",
    scope: Optional[str] = None,
    limit: int = 20,
    page: int = 1,
):
    """Recall memories, ranked by FTS relevance x confidence when `search` is given."""
    if not repo:
        raise HTTPException(status_code=400, detail="repo is required")
    try:
        entries = _memory_db().list_memory_entries(
            repo=repo,
            memory_type=type,
            status=status,
            scope=scope,
            page=page,
            limit=limit,
            search=search,
        )
    except Exception as e:
        logger.exception("Memory list failed")
        # `type` is shadowed by the query parameter
        raise HTTPException(status_code=500, detail=f"{e.__class__.__name__}: {e}")

    return {
        "repo": repo,
        "total": len(entries),
        "entries": [
            {
                "memory_id": e["memory_id"],
                "type": e["type"],
                "status": e["status"],
                "content": e["content"],
                "scope": e["scope"],
                "confidence": e["confidence"],
                "node_id": e.get("node_id"),
                "created_at": e.get("created_at"),
                "expires_at": e.get("expires_at"),
            }
            for e in entries
        ],
    }


# ── Observation endpoints — draft beliefs awaiting reconciliation ────────────
# Hooks post observations (single or batched, e.g. a spool replay). They stay
# out of memory recall until a reconciliation pass or explicit accept.

class ObservationRecord(BaseModel):
    event_type: str                  # edit_cluster | blocked_push | intent | ...
    content: str
    source: Optional[str] = None     # cursor-hook | antigravity-hook | cli
    confidence: float = 0.3
    evidence: list[dict] = []
    node_id: Optional[str] = None
    observed_at: Optional[str] = None  # client-side timestamp (spooled events
                                       # arrive long after they happened)


class ObservationPayload(BaseModel):
    repo: str
    observations: list[ObservationRecord]


@app.post("/api/v2/observations")
def save_observations(payload: ObservationPayload):
    """Record draft observations. Batch-friendly so offline spools replay in one call."""
    if not payload.repo or not payload.observations:
        raise HTTPException(status_code=400, detail="repo and observations are required")

    db = _memory_db()
    ids = []
    try:
        for obs in payload.observations:
            ids.append(db.create_observation(
                repo=payload.repo,
                event_type=obs.event_type,
                content=obs.content,
                source=obs.source,
                confidence=obs.confidence,
                evidence=obs.evidence or None,
                node_id=obs.node_id,
                observed_at=obs.observed_at,
            ))
    except Exception as e:
        logger.exception("Observation save failed")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    logger.info(f"Recorded {len(ids)} observation(s) for {payload.repo}")
    return {"status": "recorded", "repo": payload.repo, "observation_ids": ids}


@app.get("/api/v2/observations")
def list_observations(
    repo: str,
    status: str = "pending",
    event_type: Optional[str] = None,
    limit: int = 100,
):
    """List observations, defaulting to those awaiting reconciliation."""
    if not repo:
        raise HTTPException(status_code=400, detail="repo is required")
    try:
        rows = _memory_db().list_observations(
            repo=repo, status=status, event_type=event_type, limit=limit
        )
    except Exception as e:
        logger.exception("Observation list failed")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    return {"repo": repo, "total": len(rows), "observations": rows}


@app.post("/api/v2/graph/nodes")
def create_node(payload: CreateNodePayload):
    """REST endpoint to create a new SpecNode — mirrors MCP handle_create_spec_node."""
    if not payload.repo or not payload.title:
        raise HTTPException(status_code=400, detail="repo and title are required")

    try:
        level = NodeLevel(payload.level)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid level: {payload.level}. Must be one of: {[e.value for e in NodeLevel]}")

    try:
        surface = SurfaceType(payload.surface_type)
    except ValueError:
        surface = SurfaceType.INTERNAL

    contract = Contract(
        inputs=payload.contract.get("inputs", {}),
        outputs=payload.contract.get("outputs", {}),
        invariants=payload.contract.get("invariants", []),
        metrics=payload.contract.get("metrics", []),
    )

    node_id = str(uuid.uuid4())
    
    # Parse governance tier if provided
    try:
        from .v2_engine.models import GovernanceTier
        tier = GovernanceTier(payload.governance_tier) if payload.governance_tier else GovernanceTier.GOVERNED
    except ValueError:
        tier = GovernanceTier.GOVERNED

    node = SpecNode(
        id=node_id,
        repo=payload.repo,
        title=payload.title,
        level=level,
        surface_type=surface,
        governance_tier=tier,
        contract=contract,
        dependencies=payload.dependencies,
        effort_days=payload.effort_days,
        complexity=payload.complexity,
        intent_justification=payload.intent_justification,
    )

    save_spec_node(node)
    logger.info(f"Created node {node_id}: {payload.title} [{level.value}]")

    return {
        "status": "created",
        "node_id": node_id,
        "repo": payload.repo,
        "title": payload.title,
        "level": level.value,
        "surface_type": surface.value,
    }

@app.put("/api/v2/graph/nodes/{node_id}")
def update_node(node_id: str, payload: UpdateNodePayload):
    """REST endpoint to incrementally update a SpecNode (edges, contracts, surface)."""
    node = get_spec_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

    changed = False

    if payload.dependencies:
        # Merge dependencies
        merged = list(set(node.dependencies + payload.dependencies))
        if set(node.dependencies) != set(merged):
            node.dependencies = merged
            changed = True

    if payload.surface_type:
        try:
            node.surface_type = SurfaceType(payload.surface_type)
            changed = True
        except ValueError:
            pass

    if payload.effort_days is not None:
        node.effort_days = payload.effort_days
        changed = True

    if payload.complexity is not None:
        node.complexity = payload.complexity
        changed = True

    if payload.intent_justification is not None:
        node.intent_justification = payload.intent_justification
        changed = True

    if payload.contract:
        c = payload.contract
        new_invariants = list(set(node.contract.invariants + c.get("invariants", [])))
        new_metrics_data = c.get("metrics", [])
        
        # Merge metrics by name
        existing_metrics = {m.name: m for m in node.contract.metrics}
        for md in new_metrics_data:
            if isinstance(md, dict) and "name" in md and "threshold" in md:
                existing_metrics[md["name"]] = Metric(**md)
        
        new_metrics = list(existing_metrics.values())

        if new_invariants != node.contract.invariants or len(new_metrics) != len(node.contract.metrics):
            node.contract = Contract(
                inputs=c.get("inputs", node.contract.inputs),
                outputs=c.get("outputs", node.contract.outputs),
                invariants=new_invariants,
                metrics=new_metrics,
            )
            changed = True

    if changed:
        node.updated_at = _now()
        save_spec_node(node)
        logger.info(f"Updated node {node_id} via REST")

    return {"status": "updated" if changed else "unchanged", "node_id": node_id}

@app.post("/api/v2/sync")
def sync_codebase(payload: SyncPayload):
    """REST endpoint for sync_codebase — invalidate nodes containing changed files and cascade."""
    if not payload.repo:
        raise HTTPException(status_code=400, detail="repo is required")
    if not payload.files:
        raise HTTPException(status_code=400, detail="files list is required")

    cluster_label = ""
    if payload.cluster:
        cluster_label = f" [{payload.cluster.type}/{payload.cluster.severity}]"
        logger.info(f"Sync cluster{cluster_label}: {payload.cluster.description}")

    try:
        all_nodes = _all_nodes_for_repo(payload.repo)
        changed_set = set(payload.files)
        impacted_nodes = []

        for node in all_nodes:
            node_files = set(node.implementation.files)
            overlap = changed_set.intersection(node_files)
            if overlap:
                old_conf = node.state.confidence
                new_score = float(round(old_conf * 0.9, 4)) if old_conf > 0 else 0.0

                detail = f"Files modified: {', '.join(sorted(overlap))}"
                if cluster_label:
                    detail += cluster_label

                node.state.evidence.append(Evidence(
                    kind="code_change",
                    score=new_score,
                    detail=detail,
                    source="git_hook",
                    origin_id=f"sync_{node.id}",
                ))
                node.state.confidence = recalculate_confidence(node)
                node.state.status = SpecStatus.IN_PROGRESS if node.state.status == SpecStatus.VERIFIED else node.state.status
                node.updated_at = _now()

                save_spec_node(node)
                impacted_nodes.append(node.id)

        cascades = []
        for nid in impacted_nodes:
            res = cascade_blast_radius(nid, payload.repo)
            if res.get("impacted_count", 0) > 0:
                cascades.append(res)

        result = {
            "status": "synced",
            "repo": payload.repo,
            "direct_impact_count": len(impacted_nodes),
            "direct_impact_nodes": impacted_nodes,
            "cascades_triggered": len(cascades),
        }
        if payload.cluster:
            result["cluster"] = payload.cluster.model_dump()
        return result
    except Exception as e:
        logger.error(f"Error syncing codebase: {e}")
        return {"status": "error", "detail": str(e)}

@app.get("/api/v2/graph/nodes")
def get_graph_nodes(repo: str, governance_tier: Optional[str] = None):
    if not repo:
        raise HTTPException(status_code=400, detail="repo is required")
    try:
        nodes = get_all_spec_nodes(repo)
        if governance_tier:
            nodes = [n for n in nodes if getattr(n, 'governance_tier', 'governed') == governance_tier]
            
        ui_nodes = []
        for n in nodes:
            ui_nodes.append({
                "node_id": n.id,
                "title": n.title,
                "level": n.level.value,
                "surface_type": n.surface_type.value,
                "stage": n.state.stage.value,
                "status": n.state.status.value,
                "confidence": n.state.confidence,
                "review_depth": n.state.review_depth,
                "failure_classification": n.state.failure_classification.value if n.state.failure_classification else None,
                "failure_trace": getattr(n.state, "failure_trace", None),
                "priority": n.priority.value if hasattr(n, "priority") and n.priority else None,
                "dependencies": n.dependencies,
                "evidence": n.state.evidence,
                "contract": n.contract.model_dump() if n.contract else {},
                "governance_tier": getattr(n, 'governance_tier', 'governed'),
                "effort_days": getattr(n, 'effort_days', None),
                "complexity": getattr(n, 'complexity', None),
                "intent_justification": getattr(n, 'intent_justification', None),
                "updated_at": n.updated_at
            })
        return {"nodes": ui_nodes, "count": len(ui_nodes)}
    except Exception as e:
        logger.error(f"Error fetching nodes: {e}")
        return {"nodes": [], "count": 0, "note": "Internal server error"}

@app.get("/api/v2/graph/roadmap")
def get_roadmap(repo: str):
    """Return an ordered hierarchy of Project and Phase nodes for the Gantt view."""
    if not repo:
        raise HTTPException(status_code=400, detail="repo is required")
    try:
        nodes = get_all_spec_nodes(repo)
        
        projects = []
        phases = []
        features = {}

        for n in nodes:
            serialized_node = {
                "node_id": n.id,
                "title": n.title,
                "level": n.level.value,
                "stage": n.state.stage.value,
                "status": n.state.status.value,
                "confidence": n.state.confidence,
                "dependencies": n.dependencies,
                "effort_days": getattr(n, 'effort_days', None),
                "complexity": getattr(n, 'complexity', None),
                "intent_justification": getattr(n, 'intent_justification', None),
                "updated_at": n.updated_at,
            }

            if n.level == NodeLevel.L7_PROJECT:
                projects.append(serialized_node)
            elif n.level == NodeLevel.L6_PHASE:
                phases.append(serialized_node)
            else:
                features[str(n.id)] = serialized_node

        # Build nest mapping
        # In a real system, you'd trace edges from Phase -> child nodes
        # Here we'll return raw collections and let UI assemble, or if dependencies point upward
        return {
            "projects": projects,
            "phases": phases,
            "features": features, # Passing all features for UI to map via dependencies
            "count": len(nodes)
        }
    except Exception as e:
        logger.error(f"Error fetching roadmap: {e}")
        return {"projects": [], "phases": [], "features": {}, "count": 0}

@app.post("/api/v2/graph/nodes/{node_id}/promote")
def promote_node(node_id: str):
    """Promote a node from observed → proposed → governed with a skeleton contract."""
    from .v2_engine.models import GovernanceTier, Contract, Metric
    node = get_spec_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

    current_tier = getattr(node, 'governance_tier', 'governed')
    if current_tier == 'observed':
        node.governance_tier = GovernanceTier.PROPOSED
    elif current_tier == 'proposed':
        node.governance_tier = GovernanceTier.GOVERNED
        # Apply skeleton contract on final promotion
        if not node.contract.invariants:
            if node.surface_type.value == 'api_endpoint':
                node.contract.invariants = ['returns valid HTTP status']
            else:
                node.contract.invariants = ['implements declared interface']
        if not node.contract.metrics:
            node.contract.metrics = [Metric(name='test_coverage', threshold=0, unit='percent', direction='above')]
    elif current_tier == 'governed':
        return {"status": "already_governed", "node_id": node_id}
    else:
        return {"status": "unknown_tier", "node_id": node_id, "current": current_tier}

    node.updated_at = _now()
    save_spec_node(node)
    logger.info(f"Promoted node {node_id}: {current_tier} → {node.governance_tier.value}")
    return {"status": "promoted", "node_id": node_id, "from": current_tier, "to": node.governance_tier.value}


@app.get("/api/v2/inventory")
def get_inventory(repo: str):
    """Return all nodes grouped by surface_type for the Asset Inventory tab."""
    if not repo:
        raise HTTPException(status_code=400, detail="repo is required")
    try:
        all_nodes = get_all_spec_nodes(repo)
        # Show all nodes in the inventory so it doesn't appear empty after Day 1 Hydration
        inventory_nodes = all_nodes

        def _serialize(n):
            functions = getattr(n.implementation, 'functions', []) or []
            version = next((f.replace('version:', '') for f in functions if f.startswith('version:')), None)
            source = next((f.replace('source:', '') for f in functions if f.startswith('source:')), None)
            # governance_tier may be a GovernanceTier enum or a plain string — always coerce to str
            tier = getattr(n, 'governance_tier', 'observed')
            tier_str = tier.value if hasattr(tier, 'value') else str(tier)
            return {
                "node_id": n.id,
                "title": n.title,
                "level": n.level.value,
                "surface_type": n.surface_type.value,
                "governance_tier": tier_str,
                "version": version,
                "source": source,
                "files": getattr(n.implementation, 'files', []),
                "confidence": n.state.confidence,
            }


        result = {
            "repo": repo,
            "libs": [_serialize(n) for n in inventory_nodes if n.surface_type.value == "third_party_lib"],
            "services": [_serialize(n) for n in inventory_nodes if n.surface_type.value == "external_service"],
            "infra": [_serialize(n) for n in inventory_nodes if n.surface_type.value == "infrastructure"],
            "api_endpoints": [_serialize(n) for n in inventory_nodes if n.surface_type.value == "api_endpoint"],
            "features": [_serialize(n) for n in inventory_nodes if n.level.value in ("L4_FEATURE", "L5_BUSINESS_GOAL") or n.surface_type.value == "internal"],
        }
        result["total"] = sum(len(v) for v in result.values() if isinstance(v, list))
        return result
    except Exception as e:
        logger.error(f"Error fetching inventory: {e}")
        return {"libs": [], "services": [], "infra": [], "api_endpoints": [], "features": [], "total": 0}


@app.get("/api/v2/graph/state")
def get_graph_state(repo: str):
    if not repo:
        raise HTTPException(status_code=400, detail="repo is required")
    try:
        state = get_execution_state(repo)
        return {
            "ready_queue": state.ready_queue,
            "blocked_queue": state.blocked_queue,
            "failed_nodes": state.failed_nodes,
            "metrics": {
                "system_confidence": state.metrics.system_confidence,
                "coverage": state.metrics.coverage,
                "pass_rate": state.metrics.pass_rate
            }
        }
    except Exception as e:
        logger.error(f"Error fetching state: {e}")
        return {
            "ready_queue": [], "blocked_queue": [], "failed_nodes": [], 
            "metrics": {"system_confidence": 0, "coverage": 0, "pass_rate": 0}
        }

@app.get("/api/v2/graph/impact/{node_id}")
def get_graph_impact(node_id: str):
    try:
        impacted_ids = get_impacted_subgraph(node_id)
        details = []
        for d_id in impacted_ids:
            n = get_spec_node(d_id)
            if n:
                details.append({"node_id": n.id, "title": n.title, "stage": n.state.stage.value})
        
        return {
            "origin_node": node_id,
            "blast_radius": len(impacted_ids),
            "impacted_nodes": details
        }
    except Exception as e:
        logger.error(f"Error fetching impact: {e}")
        return {"blast_radius": 0, "impacted_nodes": []}

class SignalPayload(BaseModel):
    repo: str
    node_id: str
    signal_type: str
    value: float
    threshold: Optional[float] = None
    source: str = "api"
    detail: str = ""

@app.post("/api/v2/graph/signal")
def ingest_signal(payload: SignalPayload):
    node = get_spec_node(payload.node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"SpecNode '{payload.node_id}' not found")

    is_regression = payload.signal_type == 'regression'
    is_code_review = payload.signal_type == 'code_review'
    old_confidence = node.state.confidence

    # ── CODE_REVIEW: iterative review tracking ────────────────────────────────
    if is_code_review:
        import math
        findings_count = int(payload.value)
        if findings_count == 0:
            node.state.review_depth += 1
            boost = min(0.1, 0.02 * math.log2(node.state.review_depth + 1))
            new_confidence = min(1.0, old_confidence + boost)
            action = "review_pass_clean"
            ev_detail = payload.detail or f"Clean review pass #{node.state.review_depth} — no findings"
        else:
            node.state.review_depth = 0
            penalty = min(0.3, 0.05 * findings_count)
            new_confidence = max(0.0, old_confidence - penalty)
            action = "review_findings"
            ev_detail = payload.detail or f"Review found {findings_count} issue(s) — review depth reset"

        ev = Evidence(kind="runtime_sample", score=new_confidence, detail=ev_detail, source=payload.source)
        node.state.evidence.append(ev)
        node.state.confidence = new_confidence
        node.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save_spec_node(node)

        if findings_count > 0 and new_confidence < 0.6:
            node.state.status = SpecStatus.FAILED
            save_spec_node(node)
            state = get_execution_state(payload.repo)
            if payload.node_id not in state.failed_nodes:
                state.failed_nodes.append(payload.node_id)
                save_execution_state(payload.repo, state)

        return {
            "status": action,
            "node_id": payload.node_id,
            "signal_type": payload.signal_type,
            "findings_count": findings_count,
            "review_depth": node.state.review_depth,
            "old_confidence": old_confidence,
            "new_confidence": new_confidence
        }

    # ── Standard signal processing ────────────────────────────────────────────
    num_threshold = payload.threshold
    metric_direction = 'below'

    if num_threshold is None and node.contract and node.contract.metrics:
        # Match metric by name
        for m in node.contract.metrics:
            if m.name == payload.signal_type or m.name == payload.signal_type.replace('_', ' '):
                num_threshold = m.threshold
                metric_direction = m.direction.value if hasattr(m.direction, 'value') else str(m.direction)
                break
                
    breached = False
    if is_regression:
        breached = True
    elif num_threshold is not None:
        if metric_direction == 'above':
            breached = payload.value < num_threshold
        else:  # 'below' = value must stay under threshold
            breached = payload.value > num_threshold

    new_confidence = old_confidence

    if is_regression:
        new_confidence = 0.0
    elif breached and num_threshold:
        overshoot = (payload.value - num_threshold) / num_threshold if num_threshold else 0.0
        new_confidence = max(0.0, old_confidence - min(0.5, overshoot))
    else:
        new_confidence = min(1.0, old_confidence + 0.02)
        
    ev = Evidence(
        kind="runtime_sample",
        score=new_confidence,
        detail=payload.detail or f"{payload.signal_type}={payload.value} {'BREACH' if breached else 'OK'}",
        source=payload.source
    )
    
    node.state.evidence.append(ev)
    node.state.confidence = new_confidence
    
    if is_regression:
        node.state.stage = ExecutionStage.SPEC_DEFINED
        node.state.status = SpecStatus.FAILED
        node.state.failure_classification = FailureClassification.IMPLEMENTATION_BUG
        node.state.failure_trace = f"Regression from {payload.source}: {ev.detail}"
        # wipe verification traces
        node.verification.unit_tests = []
        node.verification.property_tests = []
        node.verification.fuzz_tests = []
    elif breached and new_confidence < 0.6:
        node.state.status = SpecStatus.FAILED

    node.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_spec_node(node)
    
    # Update execution state 
    if breached:
        state = get_execution_state(payload.repo)
        if payload.node_id not in state.failed_nodes:
            state.failed_nodes.append(payload.node_id)
            save_execution_state(payload.repo, state)

    return {
        "status": "node_invalidated_regression" if is_regression else "confidence_degraded" if breached else "signal_recorded",
        "node_id": payload.node_id,
        "signal_type": payload.signal_type,
        "value": payload.value,
        "threshold": num_threshold,
        "breached": breached,
        "old_confidence": old_confidence,
        "new_confidence": new_confidence
    }


# ── Verification endpoint (moved outside ingest_signal) ─────────────────────

class VerifyPayload(BaseModel):
    test_dir: Optional[str] = None
    python_bin: str = "python"
    
@app.post("/api/v2/graph/nodes/{node_id}/verify")
def verify_node(node_id: str, payload: VerifyPayload):
    from turingmind_mcp.v2_engine.handlers import handle_run_verification
    import asyncio
    
    # We create a dummy ToolContext (not used by the handler directly)
    class DummyContext: pass
    ctx = DummyContext()
    
    args = {
        "node_id": node_id,
        "test_dir": payload.test_dir,
        "python_bin": payload.python_bin
    }
    
    # Run the async handler synchronously for the REST Request
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    result = loop.run_until_complete(handle_run_verification(args, ctx))
    
    if result[0].type == "text" and result[0].text.startswith("Error"):
        raise HTTPException(status_code=400, detail=result[0].text)
        
    import json
    try:
        data = json.loads(result[0].text)
        return data
    except json.JSONDecodeError:
        return {"status": "success", "raw": result[0].text}
@app.get("/api/v2/graph/blueprint/{node_id}")
def get_node_blueprint(node_id: str):
    from .v2_engine.database import get_blueprint
    payload = get_blueprint(node_id)
    if not payload:
        raise HTTPException(status_code=404, detail=f"Blueprint for node '{node_id}' not found")
    
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=payload)


if __name__ == "__main__":
    # 8477 = "TM" in ASCII (T=84, M=77). Deliberately uncommon so the server
    # never collides with dev servers, Docker forwards, or the RepoChat
    # backend that owns 8000 on this machine.
    port = int(os.environ.get("TURINGMIND_API_PORT", "8477"))
    uvicorn.run("turingmind_mcp.api_server:app", host="127.0.0.1", port=port, reload=True)
