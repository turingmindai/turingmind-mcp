import logging
import datetime
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

# ── Stage 4: Decision Queue ─────────────────────────────────────────────────
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

@app.get("/api/v2/decision-queue")
def get_decision_queue(repo: str, limit: int = 20):
    """Return prioritized action items derived from graph gap analysis."""
    if not repo:
        raise HTTPException(status_code=400, detail="repo is required")
    try:
        gaps = detect_graph_gaps(repo)
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
    level: str                          # L0_SYSTEM, L1_FILE, L2_EXTERNAL, L3_API
    surface_type: str = "internal"      # api_endpoint, internal, job, hardware_bridge
    contract: dict = {}                 # {invariants: [], metrics: [], inputs: {}, outputs: {}}
    dependencies: list[str] = []
    priority: str = "medium"

class UpdateNodePayload(BaseModel):
    contract: dict = {}
    dependencies: list[str] = []
    surface_type: Optional[str] = None
    priority: Optional[str] = None

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

    logger.info(f"Captured {len(payload.records)} intent records from {payload.source_file} for {payload.repo}")
    return {
        "status": "captured",
        "records": len(payload.records),
        "repo": payload.repo,
        "source_file": payload.source_file,
        "timestamp": timestamp,
        "content_hash": content_hash,
    }


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
    node = SpecNode(
        id=node_id,
        repo=payload.repo,
        title=payload.title,
        level=level,
        surface_type=surface,
        contract=contract,
        dependencies=payload.dependencies,
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
def get_graph_nodes(repo: str):
    if not repo:
        raise HTTPException(status_code=400, detail="repo is required")
    try:
        nodes = get_all_spec_nodes(repo)
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
                "updated_at": n.updated_at
            })
        return {"nodes": ui_nodes, "count": len(ui_nodes)}
    except Exception as e:
        logger.error(f"Error fetching nodes: {e}")
        return {"nodes": [], "count": 0, "note": "Internal server error"}

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

if __name__ == "__main__":
    uvicorn.run("turingmind_mcp.api_server:app", host="127.0.0.1", port=8000, reload=True)
