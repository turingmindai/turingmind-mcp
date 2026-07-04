"""
TuringMind v2 Engine — Tool Handlers
Implements the 14 new constraint-graph MCP tools. Each function follows the
(arguments: dict, context: ToolContext) -> list[TextContent] contract.

These handlers are pure v2: they write exclusively to v2_memory.db via the
v2_engine.database layer. They do not touch the legacy database.
"""

from __future__ import annotations

import datetime
import json
import re
import uuid
from pathlib import Path
from typing import Any

from mcp.types import TextContent

from ..tools.context import ToolContext
from .database import (
    get_all_spec_nodes,
    get_execution_state,
    get_impacted_subgraph,
    get_impacted_subgraph_with_depth,
    get_nodes_by_stage,
    get_spec_node,
    save_blueprint,
    save_execution_state,
    save_spec_node,
    save_spec_nodes,
)
from .models import (
    Contract,
    Evidence,
    ExecutionStage,
    ExecutionState,
    FailureClassification,
    GlobalMetrics,
    GovernanceTier,
    Implementation,
    Metric,
    NodeLevel,
    NodeState,
    Priority,
    SignalType,
    SpecNode,
    SpecStatus,
    SurfaceType,
)


def _ok(data: dict[str, Any]) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, default=str))]


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"error": msg}))]


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# Lazy singleton for the legacy memory store — used to persist failure
# knowledge and enrich spec status with recalled memories.
_memory_db_instance = None


def _get_memory_db():
    global _memory_db_instance
    if _memory_db_instance is None:
        from turingmind_mcp.database import MemoryDatabase
        _memory_db_instance = MemoryDatabase()
    return _memory_db_instance


# =============================================================================
# CLOUD BACKUP
# =============================================================================

async def handle_sync_cloud(args: dict, ctx: ToolContext) -> list[TextContent]:
    repo = args.get("repo")
    if not repo:
        return _err("repo is required")

    try:
        from .database import get_all_spec_nodes, get_execution_state
        from .postgres import sync_cloud_state
        
        nodes = get_all_spec_nodes(repo)
        state = get_execution_state(repo)
        
        # Derive edges from node.dependencies (authoritative source of truth)
        # Note: SQLite edge_graph table has no 'repo' column, so we cannot query by repo.
        edges = []
        for node in nodes:
            for dep_id in node.dependencies:
                edges.append((dep_id, node.id))
        
        success = sync_cloud_state(repo, nodes, edges, state)
        if success:
            return _ok({
                "repo": repo,
                "nodes_synced": len(nodes),
                "edges_synced": len(edges),
                "message": "Successfully synchronized local state to TuringMind Cloud Postgres."
            })
        else:
            return _err("Failed to sync to Cloud Database. See logs for Postgres adapter errors.")
    except Exception as e:
        return _err(f"Sync error: {e}")


# =============================================================================
# CORE GRAPH
# =============================================================================

async def handle_create_spec_node(args: dict, ctx: ToolContext) -> list[TextContent]:
    node_id = args.get("node_id", str(uuid.uuid4()))
    repo = args.get("repo")
    title = args.get("title")
    level_raw = args.get("level")

    if not repo or not title or not level_raw:
        return _err("repo, title, and level are required")

    try:
        level = NodeLevel(level_raw)
    except ValueError:
        return _err(f"Invalid level: {level_raw}. Must be L0-L3.")

    surface_raw = args.get("surface_type", "internal")
    try:
        surface = SurfaceType(surface_raw)
    except ValueError:
        surface = SurfaceType.INTERNAL

    contract_raw = args.get("contract", {})
    contract = Contract(
        inputs=contract_raw.get("inputs", {}),
        outputs=contract_raw.get("outputs", {}),
        invariants=contract_raw.get("invariants", []),
        metrics=contract_raw.get("metrics", []),
    )

    priority_raw = args.get("priority", "medium")
    try:
        Priority(priority_raw)
    except ValueError:
        priority_raw = "medium"

    node = SpecNode(
        id=node_id,
        repo=repo,
        title=title,
        level=level,
        surface_type=surface,
        contract=contract,
        dependencies=args.get("dependencies", []),
        effort_days=args.get("effort_days"),
        complexity=args.get("complexity"),
        intent_justification=args.get("intent_justification"),
    )

    save_spec_node(node)
    response = {
        "status": "created",
        "node_id": node_id,
        "repo": repo,
        "level": level.value,
        "surface_type": surface.value,
        "stage": node.state.stage.value,
        "message": f"SpecNode '{title}' created. Next: turingmind_generate_verification",
    }
    return _ok(_append_gap_hints(response, repo))


async def handle_update_spec_node(args: dict, ctx: ToolContext) -> list[TextContent]:
    node_id = args.get("node_id")
    if not node_id:
        return _err("node_id is required")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

    if "contract" in args:
        c = args["contract"]
        node.contract = Contract(
            inputs=c.get("inputs", node.contract.inputs),
            outputs=c.get("outputs", node.contract.outputs),
            invariants=c.get("invariants", node.contract.invariants),
            metrics=c.get("metrics", node.contract.metrics),
        )

    if "surface_type" in args:
        try:
            node.surface_type = SurfaceType(args["surface_type"])
        except ValueError:
            pass

    if "dependencies" in args:
        merged = list(set(node.dependencies + args["dependencies"]))
        node.dependencies = merged

    if "effort_days" in args:
        node.effort_days = args["effort_days"]
        
    if "complexity" in args:
        node.complexity = args["complexity"]
        
    if "intent_justification" in args:
        node.intent_justification = args["intent_justification"]

    node.updated_at = _now()
    save_spec_node(node)
    response = {"status": "updated", "node_id": node_id}
    return _ok(_append_gap_hints(response, node.repo))


async def handle_save_architecture_diagram(args: dict, ctx: ToolContext) -> list[TextContent]:
    node_id = args.get("node_id")
    blueprint_payload = args.get("blueprint_payload")

    if not node_id or not blueprint_payload:
        return _err("node_id and blueprint_payload are required")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

    save_blueprint(node_id, blueprint_payload)
    
    node.has_blueprint = True
    node.updated_at = _now()
    save_spec_node(node)

    return _ok({
        "status": "blueprint_saved",
        "node_id": node_id,
        "payload_length": len(blueprint_payload),
        "message": f"Architecture diagram securely stored out-of-band for {node_id}.",
    })



async def handle_get_spec_status(args: dict, ctx: ToolContext) -> list[TextContent]:
    node_id = args.get("node_id")
    if not node_id:
        return _err("node_id is required")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

    # Recall memories linked to this node (failure history, decisions) plus
    # top relevant memories matched against the node title.
    related_memories: list[dict] = []
    try:
        mem_db = _get_memory_db()
        seen: set[str] = set()
        with mem_db.transaction() as cursor:
            cursor.execute(
                "SELECT memory_id, type, content, confidence FROM memory_entries "
                "WHERE node_id = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 5",
                (node_id,),
            )
            for row in cursor.fetchall():
                seen.add(row[0])
                related_memories.append(
                    {"memory_id": row[0], "type": row[1], "content": row[2],
                     "confidence": row[3], "link": "node"}
                )
        for e in mem_db.list_memory_entries(node.repo, status="active", search=node.title, limit=3):
            if e["memory_id"] not in seen:
                related_memories.append(
                    {"memory_id": e["memory_id"], "type": e["type"], "content": e["content"],
                     "confidence": e["confidence"], "link": "relevance"}
                )
    except Exception:
        pass  # memory recall is best-effort; spec status must not fail on it

    return _ok({
        "node_id": node_id,
        "title": node.title,
        "level": node.level.value,
        "surface_type": node.surface_type.value,
        "status": node.state.status.value,
        "stage": node.state.stage.value,
        "confidence": node.state.confidence,
        "failure_classification": node.state.failure_classification.value if node.state.failure_classification else None,
        "dependencies": node.dependencies,
        "dependents": node.dependents,
        "related_memories": related_memories,
    })


async def handle_list_spec_nodes(args: dict, ctx: ToolContext) -> list[TextContent]:
    repo = args.get("repo")
    if not repo:
        return _err("repo is required")

    stage_filter = args.get("stage", "all")
    surface_filter = args.get("surface_type", "all")
    level_filter = args.get("level", "all")

    if stage_filter == "all":
        stages = list(ExecutionStage)
    else:
        try:
            stages = [ExecutionStage(stage_filter)]
        except ValueError:
            return _err(f"Invalid stage: {stage_filter}")

    nodes = []
    for stage in stages:
        nodes.extend(get_nodes_by_stage(repo, stage))

    if surface_filter != "all":
        nodes = [n for n in nodes if n.surface_type.value == surface_filter]
    if level_filter != "all":
        nodes = [n for n in nodes if n.level.value.startswith(level_filter)]

    return _ok({
        "repo": repo,
        "count": len(nodes),
        "nodes": [
            {
                "node_id": n.id,
                "title": n.title,
                "level": n.level.value,
                "surface_type": n.surface_type.value,
                "stage": n.state.stage.value,
                "confidence": n.state.confidence,
                "review_depth": n.state.review_depth,
            }
            for n in nodes
        ],
    })


async def handle_get_ready_nodes(args: dict, ctx: ToolContext) -> list[TextContent]:
    repo = args.get("repo")
    if not repo:
        return _err("repo is required")

    state = get_execution_state(repo)
    ready = [get_spec_node(nid) for nid in state.ready_queue]
    ready = [n for n in ready if n is not None]

    return _ok({
        "repo": repo,
        "ready_count": len(ready),
        "ready_nodes": [{"node_id": n.id, "title": n.title, "level": n.level.value} for n in ready],
    })


# =============================================================================
# EXECUTION & VERIFICATION
# =============================================================================

async def handle_generate_verification(args: dict, ctx: ToolContext) -> list[TextContent]:
    node_id = args.get("node_id")
    test_dir = args.get("test_dir")  # optional: where to write stub files
    if not node_id:
        return _err("node_id is required")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

    # Gate: contract must have at least one invariant or metric before verification
    if not node.contract.invariants and not node.contract.metrics and not node.contract.inputs:
        return _err(
            f"SpecNode '{node_id}' has an empty contract (no invariants, metrics, or inputs). "
            "Call turingmind_update_spec_node to define the contract before generating verification."
        )

    # Tester Mode: auto-generate stub verification from the contract invariants
    requested = set(args.get("verification_types") or [
        "unit_tests", "property_tests", "fuzz_tests",
        "static_checks", "security_checks", "performance_checks"
    ])

    # Clear existing stubs to prevent accumulation on re-call
    node.verification.unit_tests = []
    node.verification.property_tests = []
    node.verification.fuzz_tests = []
    node.verification.security_checks = []
    node.verification.performance_checks = []

    if "unit_tests" in requested:
        for inp in node.contract.inputs:
            node.verification.unit_tests.append(f"test_{node_id}_{inp}_valid_input")
            node.verification.unit_tests.append(f"test_{node_id}_{inp}_invalid_input")

    if "property_tests" in requested:
        for invariant in node.contract.invariants:
            slug = invariant.lower().replace(" ", "_").replace(".", "")[:40]
            node.verification.property_tests.append(f"prop_{node_id}_{slug}")

    if "fuzz_tests" in requested and node.surface_type == SurfaceType.API_ENDPOINT:
        node.verification.fuzz_tests.append(f"fuzz_{node_id}_boundary_inputs")
        node.verification.fuzz_tests.append(f"fuzz_{node_id}_malformed_payloads")

    if "security_checks" in requested and node.surface_type == SurfaceType.API_ENDPOINT:
        node.verification.security_checks.extend([
            f"security_{node_id}_injection_resistance",
            f"security_{node_id}_auth_bypass",
        ])

    if "performance_checks" in requested:
        for metric in node.contract.metrics:
            slug = metric.name.lower().replace(" ", "_")[:40]
            node.verification.performance_checks.append(f"perf_{node_id}_{slug}_within_threshold")

    node.state.stage = ExecutionStage.VERIFICATION_GENERATED
    node.updated_at = _now()
    save_spec_node(node)

    # ── Write stub files to disk ─────────────────────────────────────────────────
    files_written = []
    if test_dir:
        import pathlib, os
        test_path = pathlib.Path(test_dir).resolve()
        # SECURITY: test_dir must be under user's home directory
        home = pathlib.Path(os.path.expanduser('~'))
        if not str(test_path).startswith(str(home)):
            return _err(f"test_dir must be under the user's home directory. Got: {test_dir}")
        test_path.mkdir(parents=True, exist_ok=True)
        stub_file = test_path / f"test_{node_id}.py"

        all_stubs = (
            node.verification.unit_tests +
            node.verification.property_tests +
            node.verification.security_checks +
            node.verification.performance_checks
        )
        lines = [
            f'"""Auto-generated verification stubs for SpecNode: {node_id} — {node.title}"""',
            "import pytest",
            "",
        ]
        for stub in all_stubs:
            lines += [
                f"def {stub}():",
                f"    \"\"\"TODO: implement this verification stub.\"\"\"",
                f"    pytest.fail('Stub not yet implemented — fill in assertion for {stub}')",
                "",
            ]
        stub_file.write_text("\n".join(lines))
        files_written.append(str(stub_file))

        # Store test dir on the node implementation footprint
        if str(stub_file) not in node.implementation.files:
            node.implementation.files.append(str(stub_file))
        save_spec_node(node)

    return _ok({
        "status": "verification_generated",
        "node_id": node_id,
        "unit_tests": node.verification.unit_tests,
        "property_tests": node.verification.property_tests,
        "fuzz_tests": node.verification.fuzz_tests,
        "security_checks": node.verification.security_checks,
        "performance_checks": node.verification.performance_checks,
        "files_written": files_written,
        "message": (
            f"Verification stubs written to {files_written[0]}. " if files_written
            else "Stubs generated (pass test_dir to write to disk). "
        ) + "Implement each stub, then call turingmind_run_verification.",
    })


async def handle_run_verification(args: dict, ctx: ToolContext) -> list[TextContent]:
    node_id = args.get("node_id")
    test_dir = args.get("test_dir")   # directory to run pytest against
    python_bin = args.get("python_bin", "python")  # e.g. '/path/to/.venv/bin/python'
    if not node_id:
        return _err("node_id is required")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

    # Advance to auditing immediately
    node.state.stage = ExecutionStage.AUDITING
    node.updated_at = _now()
    save_spec_node(node)

    # ── If test_dir provided, actually run pytest ────────────────────────────────────
    import subprocess, re
    import pathlib

    # Auto-discover test file if not given a dir but we have one on node.implementation.files
    if not test_dir and node.implementation.files:
        for f in node.implementation.files:
            if "test_" in f and f.endswith(".py"):
                test_dir = str(pathlib.Path(f).parent)
                break

    if test_dir and pathlib.Path(test_dir).exists():
        # SECURITY: validate test_dir is under home directory
        import os
        home_dir = pathlib.Path(os.path.expanduser('~'))
        resolved_test_dir = pathlib.Path(test_dir).resolve()
        if not str(resolved_test_dir).startswith(str(home_dir)):
            return _err(f"test_dir must be under the user's home directory. Got: {test_dir}")

        # SECURITY: validate python_bin is a real Python interpreter
        resolved_bin = pathlib.Path(python_bin).resolve() if '/' in python_bin else python_bin
        if isinstance(resolved_bin, pathlib.Path):
            if not resolved_bin.is_file():
                return _err(f"python_bin does not exist: {python_bin}")
            if 'python' not in resolved_bin.name:
                return _err(f"python_bin does not look like a Python interpreter: {python_bin}")

        try:
            result = subprocess.run(
                [str(resolved_bin), "-m", "pytest", str(resolved_test_dir),
                 "-q", "--tb=short", "--no-header"],
                capture_output=True, text=True, timeout=120
            )
            output = result.stdout + result.stderr

            # Parse pytest summary line only — avoids double-counting verbose PASSED lines
            # Pytest summary format: "3 passed, 1 failed in 0.42s" or "2 failed in 0.42s"
            passed_match = re.search(r'(\d+) passed', output)
            failed_match = re.search(r'(\d+) failed', output)
            
            passed = int(passed_match.group(1)) if passed_match else 0
            failed = int(failed_match.group(1)) if failed_match else 0
            
            if passed == 0 and failed == 0:
                # Fallback: no summary line means collection error or empty suite
                failed = 1 if result.returncode != 0 else 0
            total = passed + failed

            confidence = round(passed / total, 2) if total > 0 else 0.0
            success    = failed == 0 and total > 0

            detail = f"{passed} passed, {failed} failed" + (f" of {total}" if total else " (no tests found)")

            # Record machine-verified evidence with a tamper-evident run_id
            run_id = str(uuid.uuid4())
            ev = Evidence(
                kind="test_run",
                score=confidence,
                detail=detail,
                source="pytest",
                run_id=run_id,
            )
            node.state.evidence.append(ev)
            node.state.confidence = confidence
            node.state.last_verified_run_id = run_id  # stored for enforcement

            if success:
                node.state.stage  = ExecutionStage.VERIFIED
                node.state.status = SpecStatus.VERIFIED
            else:
                node.state.status = SpecStatus.FAILED
                node.state.failure_classification = FailureClassification.TEST_GAP
                node.state.failure_trace = output[-1000:]  # last 1000 chars of output

            node.updated_at = _now()
            save_spec_node(node)

            return _ok({
                "status": "verified" if success else "failed",
                "node_id": node_id,
                "passed": passed,
                "failed": failed,
                "confidence": confidence,
                "evidence_count": len(node.state.evidence),
                "detail": detail,
                "output": output[-2000:],  # last 2000 chars for context
                "message": f"pytest ran: {detail}. {'Node verified.' if success else 'Call turingmind_classify_failure and turingmind_apply_fix.'}",
            })
        except subprocess.TimeoutExpired:
            return _err("pytest timed out after 120 seconds.")
        except FileNotFoundError:
            return _err(f"Python binary not found: '{python_bin}'. Pass python_bin='/path/to/venv/bin/python'.")

    # ── No test_dir: advisory mode (original behaviour) ─────────────────────────
    return _ok({
        "status": "auditing",
        "node_id": node_id,
        "message": (
            "Stage set to auditing. Pass test_dir (and optionally python_bin) to run pytest "
            "automatically. Otherwise run your test suite manually, then call "
            "turingmind_record_execution_stage with results."
        ),
        "tests_to_run": {
            "unit_tests": node.verification.unit_tests,
            "property_tests": node.verification.property_tests,
            "fuzz_tests": node.verification.fuzz_tests,
            "security_checks": node.verification.security_checks,
        },
    })


async def handle_record_execution_stage(args: dict, ctx: ToolContext) -> list[TextContent]:
    node_id = args.get("node_id")
    stage_raw = args.get("stage")
    status_raw = args.get("status")
    confidence = args.get("confidence")
    evidence_raw = args.get("evidence")  # optional dict

    if not node_id or not stage_raw or not status_raw:
        return _err("node_id, stage, and status are required")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

    try:
        target_stage = ExecutionStage(stage_raw)
    except ValueError:
        return _err(f"Invalid stage: {stage_raw}")

    try:
        target_status = SpecStatus(status_raw)
    except ValueError:
        return _err(f"Invalid status: {status_raw}")

    # Gate: stage='verified' requires confidence > 0.7 (regardless of status)
    effective_confidence = float(confidence) if confidence is not None else node.state.confidence
    if target_stage == ExecutionStage.VERIFIED:
        if effective_confidence <= 0.7:
            return _err(
                f"Cannot advance to 'verified' stage: confidence is {effective_confidence:.2f} (must be > 0.7). "
                "Call turingmind_run_verification first to get machine-verified evidence."
            )

    node.state.stage = target_stage
    node.state.status = target_status

    if confidence is not None:
        requested_confidence = max(0.0, min(1.0, float(confidence)))

        # ── Evidence enforcement ─────────────────────────────────────────
        # Confidence > 0.7 requires a valid run_id matching
        # node.state.last_verified_run_id (set by run_verification).
        # No other bypass is allowed — source strings are not trusted.
        if requested_confidence > 0.7:
            ev_run_id = evidence_raw.get("run_id") if evidence_raw else None
            last_run_id = node.state.last_verified_run_id

            has_valid_run_id = (
                ev_run_id is not None
                and last_run_id is not None
                and ev_run_id == last_run_id
            )

            if not has_valid_run_id:
                # Cap at 0.7 and record the enforcement event
                requested_confidence = 0.7
                node.state.evidence.append(Evidence(
                    kind="test_run",
                    score=0.7,
                    detail=(
                        f"Confidence capped at 0.7: no valid run_id provided. "
                        f"Call turingmind_run_verification first to get a verified run_id "
                        f"(last_verified_run_id={last_run_id!r})."
                    ),
                    source="enforcement",
                ))

        node.state.confidence = requested_confidence

    # Record evidence — confidence must have a receipt
    if evidence_raw:
        try:
            ev = Evidence(
                kind=evidence_raw.get("kind", "test_run"),
                score=float(evidence_raw.get("score", confidence or node.state.confidence)),
                detail=evidence_raw.get("detail", ""),
                source=evidence_raw.get("source", "unknown"),
            )
            node.state.evidence.append(ev)
        except Exception as ev_err:
            # Evidence is best-effort — don't fail the whole call
            pass
    elif confidence is not None:
        # Called without evidence — record a minimal assertion so the trail isn't empty
        node.state.evidence.append(Evidence(
            kind="test_run",
            score=float(confidence),
            detail="Confidence set without explicit evidence (assertion).",
            source="unknown",
        ))

    node.updated_at = _now()
    save_spec_node(node)

    # If node is fully verified, update execution state queues
    if node.state.stage == ExecutionStage.VERIFIED:
        state = get_execution_state(node.repo)
        state.ready_queue = [n for n in state.ready_queue if n != node_id]
        if node_id in state.failed_nodes:
            state.failed_nodes.remove(node_id)
        save_execution_state(node.repo, state)

    return _ok(_append_gap_hints({
        "status": "recorded",
        "node_id": node_id,
        "stage": node.state.stage.value,
        "confidence": node.state.confidence,
        "evidence_count": len(node.state.evidence),
    }, node.repo))


# =============================================================================
# REPAIR LOOP
# =============================================================================

async def handle_classify_failure(args: dict, ctx: ToolContext) -> list[TextContent]:
    node_id = args.get("node_id")
    classification_raw = args.get("classification")
    failure_trace = args.get("failure_trace", "")

    if not node_id or not classification_raw:
        return _err("node_id and classification are required")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

    try:
        classification = FailureClassification(classification_raw)
    except ValueError:
        return _err(f"Invalid classification: {classification_raw}")

    # ── Stage 3.1: Guard — only cascade if node wasn't already FAILED.
    # If it was already FAILED (e.g. auto-set by ingest_runtime_signal which
    # already ran cascade_blast_radius), a second cascade would double-penalize
    # all downstream nodes.
    was_already_failed = node.state.status == SpecStatus.FAILED

    node.state.status = SpecStatus.FAILED
    node.state.failure_classification = classification
    node.state.failure_trace = failure_trace
    node.updated_at = _now()
    save_spec_node(node)  # Persist origin FIRST before cascading

    # Register in execution state failed_nodes
    state = get_execution_state(node.repo)
    if node_id not in state.failed_nodes:
        state.failed_nodes.append(node_id)
    save_execution_state(node.repo, state)

    escalation_map = {
        FailureClassification.SPEC_GAP: "Escalate to Architect Mode: refine the contract invariants",
        FailureClassification.TEST_GAP: "Escalate to Tester Mode: expand verification coverage",
        FailureClassification.IMPLEMENTATION_BUG: "Escalate to Builder Mode: patch the implementation",
        FailureClassification.DEPENDENCY_FAILURE: "Block this node: upstream dependency failed",
    }

    response = {
        "status": "failure_classified",
        "node_id": node_id,
        "classification": classification.value,
        "escalation_action": escalation_map[classification],
    }

    # Failure becomes durable memory: future agents touching these files or
    # querying this node recall what broke and how it was classified.
    try:
        scope = node.implementation.files[0] if node.implementation.files else "repo"
        memory_id = _get_memory_db().create_memory_entry(
            repo=node.repo,
            memory_type="learned_pattern",
            content=(
                f"Verification failure on '{node.title}' classified as "
                f"{classification.value}. {failure_trace[:400] if failure_trace else 'No trace provided.'}"
            ),
            scope=scope,
            confidence=0.7,
            node_id=node_id,
        )
        response["memory_id"] = memory_id
    except Exception:
        pass  # memory persistence is best-effort; classification must not fail

    # Only cascade if this is a fresh failure (not already in FAILED state)
    if not was_already_failed:
        cascade_report = cascade_blast_radius(node_id, node.repo)
        if cascade_report["impacted_count"] > 0:
            response["blast_radius_cascade"] = cascade_report

    return _ok(response)


async def handle_apply_fix(args: dict, ctx: ToolContext) -> list[TextContent]:
    node_id = args.get("node_id")
    fix_type = args.get("fix_type")
    description = args.get("description", "")

    if not node_id or not fix_type:
        return _err("node_id and fix_type are required")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

    # Reset node so the execution loop re-verifies it
    node.state.status = SpecStatus.IN_PROGRESS
    node.state.failure_classification = None
    node.state.failure_trace = None
    # Reset stage based on fix type
    stage_map = {
        "refine_spec": ExecutionStage.SPEC_DEFINED,
        "expand_tests": ExecutionStage.VERIFICATION_GENERATED,
        "patch_code": ExecutionStage.IMPLEMENTING,
        "unblock_dependency": ExecutionStage.SPEC_DEFINED,
    }
    assert fix_type is not None  # guarded by early return above
    node.state.stage = stage_map.get(fix_type, ExecutionStage.SPEC_DEFINED)
    node.updated_at = _now()
    save_spec_node(node)

    # Remove from failed_nodes
    state = get_execution_state(node.repo)
    state.failed_nodes = [n for n in state.failed_nodes if n != node_id]
    save_execution_state(node.repo, state)

    return _ok({
        "status": "fix_applied",
        "node_id": node_id,
        "fix_type": fix_type,
        "new_stage": node.state.stage.value,
        "message": f"Node reset to '{node.state.stage.value}'. Re-run the execution loop.",
    })


# =============================================================================
# SAFE CHANGE ENGINE
# =============================================================================

async def handle_apply_spec_delta(args: dict, ctx: ToolContext) -> list[TextContent]:
    node_id = args.get("node_id")
    delta = args.get("delta", {})
    reason = args.get("reason", "")

    if not node_id:
        return _err("node_id is required")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

    # Apply delta to the contract
    added = delta.get("invariants_added", [])
    removed = set(delta.get("invariants_removed", []))
    node.contract.invariants = [i for i in node.contract.invariants if i not in removed] + added

    if "inputs_changed" in delta:
        node.contract.inputs.update(delta["inputs_changed"])
    if "outputs_changed" in delta:
        node.contract.outputs.update(delta["outputs_changed"])

    node.updated_at = _now()
    save_spec_node(node)

    # Compute and invalidate downstream subgraph
    impacted = get_impacted_subgraph(node_id)
    invalidated = []
    for imp_id in impacted:
        imp_node = get_spec_node(imp_id)
        if imp_node:
            imp_node.state.stage = ExecutionStage.SPEC_DEFINED
            imp_node.state.status = SpecStatus.PENDING
            imp_node.state.confidence = 0.0
            imp_node.state.failure_classification = None
            imp_node.verification.unit_tests = []
            imp_node.verification.property_tests = []
            imp_node.verification.fuzz_tests = []
            imp_node.verification.security_checks = []
            imp_node.updated_at = _now()
            save_spec_node(imp_node)
            invalidated.append(imp_id)

    return _ok({
        "status": "delta_applied",
        "node_id": node_id,
        "reason": reason,
        "impacted_node_count": len(invalidated),
        "invalidated_nodes": invalidated,
        "message": (
            f"Contract updated. {len(invalidated)} downstream nodes invalidated "
            f"and reset to 'spec_defined'. The execution loop will auto-rebuild them."
        ),
    })


async def handle_get_impacted_nodes(args: dict, ctx: ToolContext) -> list[TextContent]:
    node_id = args.get("node_id")
    if not node_id:
        return _err("node_id is required")

    impacted = get_impacted_subgraph(node_id)
    details = []
    for imp_id in impacted:
        n = get_spec_node(imp_id)
        if n:
            details.append({"node_id": n.id, "title": n.title, "stage": n.state.stage.value})

    return _ok({
        "origin_node": node_id,
        "blast_radius": len(impacted),
        "impacted_nodes": details,
        "message": (
            f"If '{node_id}' changes, {len(impacted)} downstream nodes will be invalidated."
            if impacted else
            f"No downstream nodes depend on '{node_id}'."
        ),
    })


# =============================================================================
# RUNTIME SIGNAL INGESTION
# =============================================================================

async def handle_ingest_runtime_signal(args: dict, ctx: ToolContext) -> list[TextContent]:
    """
    Ingest a live runtime signal (error rate, latency, regression, etc.) into
    the constraint graph. Automatically:
      - Checks the signal value against the relevant Metric threshold on the node
      - Drops confidence proportionally if threshold is breached
      - Adds the node to failed_nodes if confidence falls below 0.6
      - Auto-invalidates the node back to spec_defined on a regression signal
      - Appends Evidence so every confidence change has a receipt
    """
    repo = args.get("repo")
    node_id = args.get("node_id")
    signal_type_raw = args.get("signal_type")
    value = args.get("value")
    threshold = args.get("threshold")
    source = args.get("source", "unknown")
    detail = args.get("detail", "")

    if not repo or not node_id or not signal_type_raw or value is None:
        return _err("repo, node_id, signal_type, and value are required")

    try:
        signal_type = SignalType(signal_type_raw)
    except ValueError:
        return _err(f"Invalid signal_type: {signal_type_raw}. Must be one of: {[s.value for s in SignalType]}")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

    assert value is not None   # guarded by early return above
    assert signal_type_raw is not None  # guarded by early return above
    value = float(value)
    threshold = float(threshold) if threshold is not None else None

    # ── Determine breach ──────────────────────────────────────────────────────
    breached = False
    is_regression = signal_type == SignalType.REGRESSION
    is_code_review = signal_type == SignalType.CODE_REVIEW

    if is_code_review:
        # CODE_REVIEW signal: value = number of findings in this review pass
        # 0 findings = clean pass, >0 = issues found
        import math
        findings_count = int(value)
        if findings_count == 0:
            # Clean review — increment depth, apply logarithmic confidence boost
            node.state.review_depth += 1
            boost = min(0.1, 0.02 * math.log2(node.state.review_depth + 1))
            new_confidence = min(1.0, old_confidence + boost)
            action_taken = "review_pass_clean"
            ev_detail = detail or f"Clean review pass #{node.state.review_depth} — no findings"
        else:
            # Review found issues — reset depth, degrade confidence
            node.state.review_depth = 0
            penalty = min(0.3, 0.05 * findings_count)
            new_confidence = max(0.0, old_confidence - penalty)
            breached = True
            action_taken = "review_findings"
            ev_detail = detail or f"Review found {findings_count} issue(s) — review depth reset"

        ev = Evidence(
            kind="runtime_sample",
            score=new_confidence,
            detail=ev_detail,
            source=source,
        )
        node.state.evidence.append(ev)
        node.state.confidence = new_confidence
        node.updated_at = _now()
        save_spec_node(node)

        # If findings degraded confidence below threshold, mark as failed
        if breached and new_confidence < 0.6:
            node.state.status = SpecStatus.FAILED
            node.state.failure_classification = FailureClassification.IMPLEMENTATION_BUG
            node.state.failure_trace = f"Code review: {ev_detail}"
            save_spec_node(node)

            state = get_execution_state(repo)
            if node_id not in state.failed_nodes:
                state.failed_nodes.append(node_id)
            save_execution_state(repo, state)

        return _ok({
            "status": action_taken,
            "node_id": node_id,
            "signal_type": signal_type_raw,
            "findings_count": int(value),
            "review_depth": node.state.review_depth,
            "old_confidence": old_confidence,
            "new_confidence": new_confidence,
            "message": (
                f"Clean review pass #{node.state.review_depth}. Confidence: {old_confidence:.2f} → {new_confidence:.2f}"
                if int(value) == 0
                else f"Review found {int(value)} issue(s). Review depth reset. Confidence: {old_confidence:.2f} → {new_confidence:.2f}"
            ),
        })

    if is_regression:
        # A regression signal always counts as a breach
        breached = True
    elif threshold is not None:
        # Check against caller-supplied threshold
        breached = value > threshold
    else:
        # Fall back: check against matching Metric thresholds on the node contract
        for metric in node.contract.metrics:
            if metric.name.lower() in signal_type_raw.lower():  # type: ignore[union-attr]
                breached = metric.is_breached(value)
                if threshold is None:
                    threshold = metric.threshold
                break

    # ── Compute new confidence ────────────────────────────────────────────────
    old_confidence = node.state.confidence
    if is_regression:
        new_confidence = 0.0
    elif breached and threshold and threshold > 0:
        # Proportional decay: how far over threshold are we?
        overshoot = (value - threshold) / threshold
        penalty = min(0.5, overshoot)  # cap single-signal penalty at 50%
        new_confidence = max(0.0, old_confidence - penalty)
    else:
        # Signal within bounds — slight confidence boost (evidence of health)
        new_confidence = min(1.0, old_confidence + 0.02)

    # ── Record evidence ───────────────────────────────────────────────────────
    ev_detail = detail or (
        f"{signal_type_raw}={value} "
        f"({'BREACH' if breached else 'OK'})"
        + (f" threshold={threshold}" if threshold is not None else "")
    )
    ev = Evidence(
        kind="runtime_sample",
        score=new_confidence,
        detail=ev_detail,
        source=source,
    )
    node.state.evidence.append(ev)
    node.state.confidence = new_confidence

    # ── Handle breach consequences ────────────────────────────────────────────
    action_taken = "signal_recorded"
    if is_regression:
        # Full invalidation — reset node to start of the execution loop
        node.state.stage = ExecutionStage.SPEC_DEFINED
        node.state.status = SpecStatus.FAILED
        node.state.failure_classification = FailureClassification.IMPLEMENTATION_BUG
        node.state.failure_trace = f"Regression signal from {source}: {ev_detail}"
        # Clear stale verification stubs — they didn't catch this regression
        node.verification.unit_tests = []
        node.verification.property_tests = []
        node.verification.fuzz_tests = []
        action_taken = "node_invalidated_regression"
    elif breached and new_confidence < 0.6:
        node.state.status = SpecStatus.FAILED
        node.state.failure_classification = FailureClassification.IMPLEMENTATION_BUG
        node.state.failure_trace = f"Threshold breach: {ev_detail}"
        action_taken = "node_marked_failed"
    elif breached:
        # Degraded but not yet critical
        action_taken = "confidence_degraded"

    node.updated_at = _now()
    save_spec_node(node)  # Persist origin node BEFORE cascading to downstream

    # Stage 3.1: cascade blast radius AFTER origin is saved, only on regression
    if is_regression:
        cascade_blast_radius(node_id, repo)

    # ── Update execution control plane ────────────────────────────────────────
    if breached or is_regression:
        state = get_execution_state(repo)
        if node_id not in state.failed_nodes:
            state.failed_nodes.append(node_id)
        save_execution_state(repo, state)

    return _ok({
        "status": action_taken,
        "node_id": node_id,
        "signal_type": signal_type_raw,
        "value": value,
        "threshold": threshold,
        "breached": breached,
        "old_confidence": old_confidence,
        "new_confidence": new_confidence,
        "evidence_count": len(node.state.evidence),
        "message": (
            f"Node invalidated — regression detected from '{source}'." if is_regression
            else f"Confidence updated: {old_confidence:.2f} → {new_confidence:.2f} ({'breach' if breached else 'healthy'})."
        ),
    })


# =============================================================================
# HUMAN GATE
# =============================================================================

async def handle_request_approval(args: dict, ctx: ToolContext) -> list[TextContent]:
    node_id = args.get("node_id")
    reason = args.get("reason")
    context_text = args.get("context", "")

    if not node_id or not reason:
        return _err("node_id and reason are required")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

    return _ok({
        "status": "approval_requested",
        "node_id": node_id,
        "title": node.title,
        "reason": reason,
        "context": context_text,
        "stage": node.state.stage.value,
        "confidence": node.state.confidence,
        "surface_type": node.surface_type.value,
        "message": "Human approval required. The execution loop is paused for this node.",
    })


# =============================================================================
# EXECUTION STATE
# =============================================================================

import time
_METRICS_CACHE = {}  # repo -> (timestamp, all_nodes)
_METRICS_CACHE_TTL = 5.0  # 5 seconds to dedup duplicate poll bursts

async def handle_get_execution_state(args: dict, ctx: ToolContext) -> list[TextContent]:
    repo = args.get("repo")
    if not repo:
        return _err("repo is required")

    state = get_execution_state(repo)

    # Compute GlobalMetrics from real node data instead of static zeroes
    # Use a short TTL cache to prevent deserializing SQLite DB 4x on every UI poll interval
    from .database import get_all_spec_nodes
    now = time.time()
    all_nodes = None
    
    if repo in _METRICS_CACHE:
        cached_ts, cached_nodes = _METRICS_CACHE[repo]
        if now - cached_ts < _METRICS_CACHE_TTL:
            all_nodes = cached_nodes

    if all_nodes is None:
        all_nodes = get_all_spec_nodes(repo)
        _METRICS_CACHE[repo] = (now, all_nodes)

    # Filter out observed/proposed nodes for formal metrics
    governed_nodes = [n for n in (all_nodes or []) if getattr(n, 'governance_tier', 'governed') == 'governed' or getattr(n, 'governance_tier', None) == GovernanceTier.GOVERNED]

    if governed_nodes:
        total = len(governed_nodes)
        verified_count = sum(1 for n in governed_nodes if n.state.stage == ExecutionStage.VERIFIED)
        failed_count = sum(1 for n in governed_nodes if n.state.status == SpecStatus.FAILED)
        passed_count = verified_count
        tested_count = passed_count + failed_count

        avg_confidence = sum(n.state.confidence for n in governed_nodes) / total
        pass_rate = (passed_count / tested_count) if tested_count > 0 else 0.0
        coverage = verified_count / total

        state.metrics.system_confidence = round(avg_confidence, 3)
        state.metrics.pass_rate = round(pass_rate, 3)
        state.metrics.coverage = round(coverage, 3)

        actual_failed = [n.id for n in governed_nodes if n.state.status == SpecStatus.FAILED]
        state.failed_nodes = actual_failed

    return _ok({
        "repo": repo,
        "ready_queue": state.ready_queue,
        "blocked_queue": state.blocked_queue,
        "failed_nodes": state.failed_nodes,
        "total_nodes": len(governed_nodes) if governed_nodes else 0,
        "verified_nodes": sum(1 for n in governed_nodes if n.state.stage == ExecutionStage.VERIFIED) if governed_nodes else 0,
        "metrics": {
            "coverage": state.metrics.coverage,
            "pass_rate": state.metrics.pass_rate,
            "system_confidence": state.metrics.system_confidence,
        },
    })


# =============================================================================
# BOOTSTRAP
# =============================================================================

async def handle_bootstrap_codebase(args: dict, ctx: ToolContext) -> list[TextContent]:
    """
    Scan an existing project and auto-create L2 SpecNodes for each module group.
    Nodes are created with blank contracts — the agent fills them in progressively.
    """
    import pathlib

    repo         = args.get("repo")
    project_path = args.get("project_path")
    patterns     = args.get("include_patterns") or ["*.py", "*.ts", "*.js", "*.jsx", "*.tsx", "*.swift", "*.c", "*.cpp", "*.h", "*.m", "*.plist", "*.entitlements"]
    exclude_dirs = set(args.get("exclude_dirs") or [
        "node_modules", ".venv", "venv", "__pycache__", ".git", "dist", "build", ".next", ".mypy_cache",
        "tests", "test", "__tests__", "scripts", "fixtures", "migrations", "mocks", "e2e",
    ])
    dry_run      = args.get("dry_run", False)

    if not repo or not project_path:
        return _err("repo and project_path are required")

    root = pathlib.Path(project_path)
    if not root.exists():
        return _err(f"project_path does not exist: {project_path}")

    # ── Collect files grouped by immediate parent directory ────────────────────
    module_groups: dict[str, list[str]] = {}
    for pattern in patterns:
        for f in root.rglob(pattern):
            # Skip excluded directories anywhere in the path
            if any(part in exclude_dirs for part in f.parts):
                continue
            rel_dir = str(f.parent.relative_to(root))
            if rel_dir not in module_groups:
                module_groups[rel_dir] = []
            module_groups[rel_dir].append(str(f.relative_to(root)))

    if not module_groups:
        return _ok({
            "status": "no_files_found",
            "message": f"No files matched {patterns} under {project_path} (after exclusions).",
            "nodes_created": 0,
        })

    # ── Create one SpecNode per module group ─────────────────────────────────
    created = []
    skipped = []

    # Fetch all existing nodes ONCE (not inside the loop — avoids N+1 queries)
    all_existing = _all_nodes_for_repo(repo) if not dry_run else []
    existing_titles = {n.title for n in all_existing if n.title}

    for module_dir, files in sorted(module_groups.items()):
        title = module_dir if module_dir != "." else "(root)"
        node_id = f"bootstrap-{repo.replace('/', '-')}-{title.replace('/', '-').replace('.', '')}-{str(uuid.uuid4())[:8]}"

        if dry_run:
            created.append({"node_id": node_id, "title": title, "files": files})
            continue

        # Check for an existing node covering this path to avoid duplication
        if title in existing_titles:
            match = next((n for n in all_existing if n.title == title), None)
            skipped.append({"title": title, "reason": "node already exists", "existing_id": match.id if match else "?"})
            continue

        node = SpecNode(
            id=node_id,
            repo=repo,
            title=title,
            level=NodeLevel.L2_EXTERNAL,
            surface_type=SurfaceType.INTERNAL,
            implementation=Implementation(files=files),
        )
        save_spec_node(node)
        created.append({"node_id": node_id, "title": title, "files": files})

    # ── Deep Scan: auto-inventory L3 APIs and L4 Features as OBSERVED ────────
    scan_depth = args.get("scan_depth", "shallow")
    observed_created = []

    if scan_depth == "deep" and not dry_run:
        import re as _re

        # Regex patterns for common API route declarations
        route_patterns = [
            _re.compile(r'''app\.(get|post|put|delete|patch)\(['"]([^'"]+)['"]''', _re.IGNORECASE),
            _re.compile(r'''router\.(get|post|put|delete|patch)\(['"]([^'"]+)['"]''', _re.IGNORECASE),
            _re.compile(r'''@app\.(route|get|post|put|delete|patch)\(['"]([^'"]+)['"]''', _re.IGNORECASE),
        ]

        # Scan all source files for API routes
        discovered_routes: list[dict] = []
        for _files in module_groups.values():
            for rel_file in _files:
                full_path = root / rel_file
                if not full_path.exists():
                    continue
                try:
                    content = full_path.read_text(errors="ignore")
                except Exception:
                    continue
                for pat in route_patterns:
                    for match in pat.finditer(content):
                        method = match.group(1).upper()
                        path = match.group(2)
                        discovered_routes.append({
                            "method": method,
                            "path": path,
                            "source_file": rel_file,
                        })

        # Create L3 API nodes (observed tier)
        for route in discovered_routes:
            api_title = f"{route['method']} {route['path']}"
            if api_title in existing_titles:
                continue
            api_id = f"api-{repo.replace('/', '-')}-{str(uuid.uuid4())[:8]}"
            api_node = SpecNode(
                id=api_id,
                repo=repo,
                title=api_title,
                level=NodeLevel.L3_API,
                surface_type=SurfaceType.API_ENDPOINT,
                governance_tier=GovernanceTier.OBSERVED,
                implementation=Implementation(files=[route["source_file"]]),
            )
            save_spec_node(api_node)
            existing_titles.add(api_title)
            observed_created.append({"node_id": api_id, "title": api_title, "level": "L3_API", "tier": "observed"})

        # Cluster L3 APIs into L4 Features by URL prefix
        prefix_groups: dict[str, list[str]] = {}
        for route in discovered_routes:
            parts = route["path"].strip("/").split("/")
            # Use first 2 path segments as feature key (e.g., '/api/v2/graph' -> 'api/v2/graph')
            prefix = "/".join(parts[:3]) if len(parts) >= 3 else "/".join(parts[:2]) if len(parts) >= 2 else parts[0] if parts else "misc"
            if prefix not in prefix_groups:
                prefix_groups[prefix] = []
            prefix_groups[prefix].append(f"{route['method']} {route['path']}")

        for prefix, api_titles in prefix_groups.items():
            # Convert prefix to human-readable feature name
            feature_name = prefix.replace("/", " ").replace("api ", "").replace("v2 ", "").strip().title() or "Core"
            if feature_name in existing_titles:
                continue
            feat_id = f"feature-{repo.replace('/', '-')}-{str(uuid.uuid4())[:8]}"
            feat_node = SpecNode(
                id=feat_id,
                repo=repo,
                title=feature_name,
                level=NodeLevel.L4_FEATURE,
                surface_type=SurfaceType.INTERNAL,
                governance_tier=GovernanceTier.OBSERVED,
            )
            save_spec_node(feat_node)
            existing_titles.add(feature_name)
            observed_created.append({"node_id": feat_id, "title": feature_name, "level": "L4_FEATURE", "tier": "observed", "apis": api_titles})

    # ── Manifest Scan: 3rd party libs and external services as OBSERVED ──────
    if scan_depth == "deep" and not dry_run:
        import json as _json
        import re as _re2

        # ── Known external service SDK fingerprints ──────────────────────────
        EXTERNAL_SERVICE_PATTERNS = {
            "redis":       SurfaceType.EXTERNAL_SERVICE,
            "rq":          SurfaceType.EXTERNAL_SERVICE,
            "celery":      SurfaceType.EXTERNAL_SERVICE,
            "kafka":       SurfaceType.EXTERNAL_SERVICE,
            "pika":        SurfaceType.EXTERNAL_SERVICE,   # RabbitMQ
            "pg":          SurfaceType.EXTERNAL_SERVICE,   # node-postgres
            "psycopg2":    SurfaceType.EXTERNAL_SERVICE,
            "asyncpg":     SurfaceType.EXTERNAL_SERVICE,
            "pymongo":     SurfaceType.EXTERNAL_SERVICE,
            "motor":       SurfaceType.EXTERNAL_SERVICE,   # async mongo
            "elasticsearch": SurfaceType.EXTERNAL_SERVICE,
            "boto3":       SurfaceType.EXTERNAL_SERVICE,   # AWS
            "aiobotocore": SurfaceType.EXTERNAL_SERVICE,
            "stripe":      SurfaceType.EXTERNAL_SERVICE,
            "twilio":      SurfaceType.EXTERNAL_SERVICE,
            "sendgrid":    SurfaceType.EXTERNAL_SERVICE,
            "httpx":       SurfaceType.EXTERNAL_SERVICE,
            "aiohttp":     SurfaceType.EXTERNAL_SERVICE,
            "firebase_admin": SurfaceType.EXTERNAL_SERVICE,
            "openai":      SurfaceType.EXTERNAL_SERVICE,
            "anthropic":   SurfaceType.EXTERNAL_SERVICE,
        }

        # ── Manifest file parsers ─────────────────────────────────────────────
        manifest_files = [
            root / "package.json",
            root / "requirements.txt",
            root / "Pipfile",
            root / "pyproject.toml",
            root / "Package.swift",
            root / "Podfile",
        ]

        for manifest_path in manifest_files:
            if not manifest_path.exists():
                continue
            try:
                text = manifest_path.read_text(errors="ignore")
                manifest_name = manifest_path.name
                packages: dict[str, str] = {}  # name -> version

                if manifest_name == "package.json":
                    try:
                        data = _json.loads(text)
                        for key in ("dependencies", "devDependencies"):
                            for pkg, ver in (data.get(key) or {}).items():
                                packages[pkg] = str(ver)
                    except Exception:
                        pass

                elif manifest_name == "requirements.txt":
                    for line in text.splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        m = _re2.match(r"^([A-Za-z0-9_\-\.]+)([>=<!\s].*)?$", line)
                        if m:
                            pkg = m.group(1).lower()
                            ver = (m.group(2) or "").strip() or "unpinned"
                            packages[pkg] = ver

                elif manifest_name in ("Pipfile", "pyproject.toml"):
                    # Best-effort: look for `package = "*"` or `package = "^1.2.3"`
                    for m in _re2.finditer(r'^([a-zA-Z0-9_\-]+)\s*=\s*["\']([^"\']+)["\']', text, _re2.MULTILINE):
                        packages[m.group(1).lower()] = m.group(2)

                elif manifest_name == "Package.swift":
                    # Best-effort: extract repo basename and version from .package(url: ".../Alamofire.git", from: "5.0.0")
                    for m in _re2.finditer(r'\.package\(\s*url:\s*["\'](?:.*?/)([^/]+?)(?:\.git)?["\'].*?from:\s*["\']([^"\']+)["\']', text):
                        packages[m.group(1).lower()] = m.group(2)

                for pkg_name, pkg_ver in packages.items():
                    if pkg_name in existing_titles:
                        continue
                    stype = EXTERNAL_SERVICE_PATTERNS.get(pkg_name, SurfaceType.THIRD_PARTY_LIB)
                    pkg_id = f"dep-{repo.replace('/', '-')}-{str(uuid.uuid4())[:8]}"
                    pkg_node = SpecNode(
                        id=pkg_id,
                        repo=repo,
                        title=pkg_name,
                        level=NodeLevel.L2_EXTERNAL,
                        surface_type=stype,
                        governance_tier=GovernanceTier.OBSERVED,
                        implementation=Implementation(
                            files=[str(manifest_path.relative_to(root))],
                            functions=[f"version:{pkg_ver}", f"source:{manifest_name}"],
                        ),
                    )
                    save_spec_node(pkg_node)
                    existing_titles.add(pkg_name)
                    observed_created.append({
                        "node_id": pkg_id,
                        "title": pkg_name,
                        "level": "L2_EXTERNAL",
                        "tier": "observed",
                        "surface_type": stype.value,
                        "version": pkg_ver,
                        "source": manifest_name,
                    })
            except Exception:
                continue

        # ── Infrastructure inventory: Docker / k8s / CI config files ──────────
        # Note: pathlib.glob() skips hidden dirs (.github etc) on Python <3.11
        # so we cannot use patterns like '.github/workflows/*.yml'. Instead we
        # check explicit well-known paths and then glob simple top-level patterns.
        infra_entries: list[tuple[pathlib.Path, str]] = []

        # Explicit well-known files in hidden dirs
        for _explicit in [
            root / ".github" / "workflows",
            root / ".circleci",
        ]:
            if _explicit.is_dir():
                for _f in _explicit.iterdir():
                    if _f.suffix in (".yml", ".yaml", ".json") and _f.is_file():
                        infra_entries.append((_f, _f.name))

        # Simple top-level globs (no hidden dirs involved)
        INFRA_TOP_GLOBS = [
            "Dockerfile", "Dockerfile.*", "docker-compose.yml", "docker-compose.yaml",
            "docker-compose.*.yml", "*.tf", "*.tfvars", "*.k8s.yaml", "*.k8s.yml",
            "*-deployment.yaml", "*-deployment.yml", "Jenkinsfile",
        ]
        for _pat in INFRA_TOP_GLOBS:
            for _f in root.glob(_pat):
                if _f.is_file():
                    infra_entries.append((_f, _f.name))

        for infra_file, infra_name in infra_entries:
            try:
                rel = str(infra_file.relative_to(root))
            except ValueError:
                rel = infra_name
            title = f"infra:{rel}"
            if title in existing_titles:
                continue
            infra_id = f"infra-{repo.replace('/', '-')}-{str(uuid.uuid4())[:8]}"
            infra_node = SpecNode(
                id=infra_id,
                repo=repo,
                title=title,
                level=NodeLevel.L0_INFRA,
                surface_type=SurfaceType.INFRASTRUCTURE,
                governance_tier=GovernanceTier.OBSERVED,
                implementation=Implementation(files=[rel]),
            )
            save_spec_node(infra_node)
            existing_titles.add(title)
            observed_created.append({
                "node_id": infra_id,
                "title": title,
                "level": "L0_INFRA",
                "tier": "observed",
                "surface_type": "infrastructure",
                "source": rel,
            })


    total_files = sum(len(v) for v in module_groups.values())
    return _ok({
        "status": "bootstrapped" if not dry_run else "dry_run",
        "repo": repo,
        "nodes_created": len(created) if not dry_run else 0,
        "nodes_dry_run": len(created) if dry_run else 0,
        "nodes_skipped": len(skipped),
        "observed_created": observed_created,
        "created": created,
        "skipped": skipped,
        "message": (
            f"{'Would create' if dry_run else 'Created'} {len(created)} L2 SpecNodes from {total_files} files. "
            + (f"Deep scan: {len(observed_created)} observed L3/L4 nodes auto-inventoried. " if observed_created else "")
            + "Contracts are blank — fill in invariants and metrics progressively."
        ),
    })



def _all_nodes_for_repo(repo: str) -> list[SpecNode]:
    """Helper: fetch all nodes for a repo for bootstrap dedup check."""
    from .database import get_all_spec_nodes
    try:
        return get_all_spec_nodes(repo)
    except Exception:
        return []


# =============================================================================
# STAGE 3: COGNITIVE OFFLOADING ENGINE
# =============================================================================
# Three pure deterministic functions that shift reasoning work from the
# IDE Agent to the MCP backend.  No LLM calls — pure graph logic.


def recalculate_confidence(node: SpecNode, decay: float = 0.85) -> float:
    """Compute confidence from the Evidence[] trail using a weighted-recency formula.
    Most recent evidence is weighted highest.  If no evidence, returns 0.0.

    Side-effect: also updates node.state.review_depth by counting consecutive
    trailing 'review' evidence entries with score >= 0.8.  Any non-review entry
    (code_change, security_violation, blast_radius_cascade, etc.) breaks the
    streak and resets the depth to 0.  This makes review_depth a derived
    stability metric that the dashboard can display directly.
    """
    evidence = node.state.evidence
    if not evidence:
        node.state.review_depth = 0
        return 0.0

    # ── Confidence calculation (unchanged) ─────────────────────────────
    n = len(evidence)
    total_weight = 0.0
    weighted_sum = 0.0
    for i, ev in enumerate(evidence):
        weight = decay ** (n - 1 - i)  # most recent (last) gets weight=1.0
        weighted_sum += ev.score * weight
        total_weight += weight

    # ── R1-B: Derive review_depth from the evidence trail ──────────────
    # Walk backwards: count consecutive "review" entries with score >= 0.8.
    # The moment we hit a non-review kind, the streak breaks.
    depth = 0
    for ev in reversed(evidence):
        if ev.kind == "review" and ev.score >= 0.8:
            depth += 1
        else:
            break
    node.state.review_depth = depth

    return round(weighted_sum / total_weight, 4) if total_weight > 0 else 0.0


def auto_classify_failure(node: SpecNode, node_map: dict[str, SpecNode]) -> FailureClassification:
    """Deterministic heuristic classification of a node failure.
    Uses graph state (not LLM) to pick the most likely FailureClassification.
    """
    trace = (node.state.failure_trace or "").lower()

    # Rule 1: Upstream dependency is itself in FAILED status
    for dep_id in node.dependencies:
        dep = node_map.get(dep_id)
        if dep and dep.state.status == SpecStatus.FAILED:
            return FailureClassification.DEPENDENCY_FAILURE

    # Rule 2: Trace mentions dependency/import keywords (use word-boundary regex to avoid
    # false matches like "important" matching "import" or "required" matching "require")
    dep_patterns = [
        r"\bimport\b", r"module not found", r"\bdependency\b",
        r"cannot find module", r"no such file", r"\bpackage\b",
        r"\brequire\b", r"resolution failed",
    ]
    if any(re.search(pat, trace) for pat in dep_patterns):
        return FailureClassification.DEPENDENCY_FAILURE

    # Rule 3: Node has empty verification (no tests generated)
    if not node.verification.unit_tests and not node.verification.property_tests:
        return FailureClassification.TEST_GAP

    # Rule 4: Contract is empty and failure involves assertion
    if (not node.contract.invariants and not node.contract.inputs
            and ("assert" in trace or "invariant" in trace or "contract" in trace)):
        return FailureClassification.SPEC_GAP

    # Default
    return FailureClassification.IMPLEMENTATION_BUG


def cascade_blast_radius(origin_id: str, repo: str) -> dict:
    """Walk dependents[] recursively and apply distance-attenuated confidence
    penalties.  Returns a report of all affected nodes.

    Stage 3.2 hardening:
    - max_nodes circuit breaker: caps total traversal to prevent DB lock storms
      on high-fan-out graphs. Truncation is surfaced in the return value so the
      IDE Agent knows to escalate for human review.
    - Structured idempotency via Evidence.origin_id (not string-parsing on detail)
    - Evidence eviction: trims to last 30 entries before saving to prevent
      unbounded JSON blob growth and keep recalculate_confidence efficient
    """
    impacted_with_depth = get_impacted_subgraph_with_depth(origin_id)
    if not impacted_with_depth:
        return {"origin": origin_id, "impacted_count": 0, "affected": [], "truncated": False}

    # Circuit breaker: cap total nodes touched.
    # >50 impacted nodes on a change is almost certainly an L0_INFRA failure
    # that warrants human review, not automated propagation.
    # The cap is a safety valve — not a correctness limit.
    _MAX_CASCADE_NODES = 50
    total_impacted = len(impacted_with_depth)  # capture BEFORE slicing
    truncated = total_impacted > _MAX_CASCADE_NODES
    if truncated:
        impacted_with_depth = impacted_with_depth[:_MAX_CASCADE_NODES]

    # Proportional multipliers: depth-1 keeps 70% of confidence, depth-2 80%, depth-3+ 90%
    multiplier_schedule = {1: 0.7, 2: 0.8}  # depth -> keep_ratio; 3+ defaults to 0.9
    affected = []
    nodes_to_save = []

    for node_id, depth in impacted_with_depth:
        node = get_spec_node(node_id)
        if not node:
            continue

        # ── Idempotency: skip if this origin already cascaded to this node ──
        # Uses structured Evidence.origin_id field (not fragile string-parsing)
        already_cascaded = any(
            ev.kind == "blast_radius_cascade" and ev.origin_id == origin_id
            for ev in node.state.evidence
        )
        if already_cascaded:
            continue

        old_conf = node.state.confidence
        multiplier = multiplier_schedule.get(depth, 0.9)
        # Penalty is proportional to current confidence, not a fixed absolute offset
        penalty = round(old_conf * (1.0 - multiplier), 4)

        # Append typed evidence receipt with structured origin_id for idempotency
        node.state.evidence.append(Evidence(
            kind="blast_radius_cascade",
            score=round(old_conf * multiplier, 4),
            detail=f"Cascade from '{origin_id}' (depth={depth}, multiplier={multiplier})",
            source="blast_radius_engine",
            origin_id=origin_id,
        ))

        # Evidence eviction: keep only the 30 most recent entries.
        # Early entries have exponential weight decay (~0.85^30 < 0.01) —
        # they're mathematically irrelevant and unnecessarily bloat the JSON blob.
        if len(node.state.evidence) > 30:
            node.state.evidence = node.state.evidence[-30:]

        # Use recalculate_confidence as the single source of truth
        node.state.confidence = recalculate_confidence(node)
        node.updated_at = _now()
        nodes_to_save.append(node)

        affected.append({
            "node_id": node_id,
            "depth": depth,
            "old_confidence": old_conf,
            "new_confidence": node.state.confidence,
            "penalty": penalty,
        })

    # Atomic batch save — if any write fails, the entire cascade is rolled back
    if nodes_to_save:
        save_spec_nodes(nodes_to_save)

    result = {
        "origin": origin_id,
        "impacted_count": len(affected),
        "affected": affected,
        "truncated": truncated,
    }
    if truncated:
        result["truncation_warning"] = (
            f"Cascade truncated at {_MAX_CASCADE_NODES} nodes. "
            f"Total downstream impact was {total_impacted} nodes. "
            "This suggests an L0_INFRA failure — escalate for human review."
        )
    return result



# =============================================================================
# GRAPH-GAP DETECTOR (Stage 2 Automation)
# =============================================================================
# Instead of watching files on disk, we analyze the DAG itself after every
# mutation.  If structural gaps exist (e.g. L1 nodes with no L2 dependency
# edges, or L3 API nodes with no contract invariants), we return a structured
# prompt inside the tool response so the IDE Agent can act on it immediately.
# This is a pull-based, graph-driven trigger — no file watchers, no scripts.

def detect_graph_gaps(repo: str) -> list[dict]:
    """Scan the constraint graph for structural gaps that the IDE Agent
    should resolve.  Returns a list of gap descriptors.
    Only GOVERNED nodes trigger gap alerts — OBSERVED and PROPOSED nodes are
    informational and do not require structural enforcement."""
    all_nodes_raw = _all_nodes_for_repo(repo)
    if not all_nodes_raw:
        return []

    # Index by ID across ALL nodes so dependencies on observed nodes resolve properly
    node_map = {n.id: n for n in all_nodes_raw}

    # Filter to governed-only for subjects of gap detection
    governed_nodes = [n for n in all_nodes_raw if getattr(n, 'governance_tier', 'governed') == 'governed' or getattr(n, 'governance_tier', None) == GovernanceTier.GOVERNED]

    gaps: list[dict] = []

    # Collect existing levels across all nodes
    levels_present = {n.level for n in all_nodes_raw}

    # ── Gap 1: L1 nodes (in SPEC_DEFINED stage) with ZERO upstream L2 dependencies ──────────────
    # An L1 (file-level) node that has no dependencies pointing to an L2_EXTERNAL
    # or L3_API node means we haven't mapped its external boundary yet.
    # Exclude nodes that have advanced past SPEC_DEFINED to prevent nagging on pure files.
    for node in governed_nodes:
        if node.level == NodeLevel.L1_FILE and node.state.stage == ExecutionStage.SPEC_DEFINED:
            has_boundary_dep = any(
                node_map[dep_id].level in (NodeLevel.L2_EXTERNAL, NodeLevel.L3_API)
                for dep_id in node.dependencies
                if dep_id in node_map
            )
            if not has_boundary_dep:
                gaps.append({
                    "gap_type": "missing_boundary_edge",
                    "severity": "medium",
                    "node_id": node.id,
                    "node_title": node.title,
                    "action": (
                        f"L1 node '{node.title}' has no L2_EXTERNAL or L3_API dependency edges. "
                        f"Analyze its source files and call turingmind_create_spec_node for each "
                        f"external dependency with level=L2 or L3, then call turingmind_update_spec_node "
                        f"to add the dependency edge. If the file legitimately has no external dependencies, "
                        f"use turingmind_record_execution_stage to advance its stage to 'implementing' to clear this warning."
                    ),
                })

    # ── Gap 2: API endpoint nodes with empty contracts ──────────────────
    for node in governed_nodes:
        if node.surface_type == SurfaceType.API_ENDPOINT:
            if not node.contract.invariants and not node.contract.inputs:
                gaps.append({
                    "gap_type": "empty_api_contract",
                    "severity": "high",
                    "node_id": node.id,
                    "node_title": node.title,
                    "action": (
                        f"API endpoint node '{node.title}' has an empty contract. "
                        f"Analyze the OpenAPI spec or source code and call "
                        f"turingmind_update_spec_node to populate contract.inputs, "
                        f"contract.outputs, and contract.invariants."
                    ),
                })

    # ── Gap 3: Orphan nodes (no dependencies AND no dependents) ─────────
    # We must precompute dependents from the `edge_graph` table since `node.dependents`
    # inside the JSON blob is not updated when downstream nodes declare a dependency.
    import sqlite3
    from .database import _get_connection
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT upstream_id FROM edge_graph")
        all_upstream_ids = {row["upstream_id"] for row in cursor.fetchall()}

    for node in governed_nodes:
        if node.level not in (NodeLevel.L0_INFRA,):  # L0 infra nodes are allowed to be roots
            has_dependents = node.id in all_upstream_ids
            if not node.dependencies and not has_dependents:
                gaps.append({
                    "gap_type": "orphan_node",
                    "severity": "low",
                    "node_id": node.id,
                    "node_title": node.title,
                    "action": (
                        f"Node '{node.title}' is disconnected from the graph (no edges). "
                        f"Either link it to its upstream dependencies or remove it."
                    ),
                })

    # ── Gap 4: Failed nodes with no failure_classification (Stage 3) ─────
    # Note: node_map was already built above for Gap 1 — reuse it here (Stage 3.1 fix)
    for node in governed_nodes:
        if node.state.status == SpecStatus.FAILED and not node.state.failure_classification:
            suggested = auto_classify_failure(node, node_map)
            gaps.append({
                "gap_type": "unclassified_failure",
                "severity": "high",
                "node_id": node.id,
                "node_title": node.title,
                "suggested_classification": suggested.value,
                "action": (
                    f"Node '{node.title}' is in FAILED status but has no failure classification. "
                    f"Auto-classifier suggests: '{suggested.value}'. Call turingmind_classify_failure "
                    f"with classification='{suggested.value}' to accept, or override with your own."
                ),
            })

    return gaps


def _append_gap_hints(response: dict, repo: str) -> dict:
    """Enrich any handler response with graph-gap hints so the IDE Agent
    can proactively resolve structural issues on the next turn."""
    gaps = detect_graph_gaps(repo)
    if gaps:
        response["graph_gaps"] = gaps
        response["graph_gap_count"] = len(gaps)
        response["graph_gap_summary"] = (
            f"⚠️ {len(gaps)} structural gap(s) detected in the constraint graph. "
            f"Review the 'graph_gaps' array and resolve each action to complete Stage 2 boundary mapping."
        )
    return response


# =============================================================================
# STAGE 4: AUTONOMOUS WORKFLOW & DECISION QUEUE
# =============================================================================

async def handle_sync_codebase(args: dict, ctx: ToolContext) -> list[TextContent]:
    """Git hook receiver. Invalidate nodes that contain changed files and cascade.

    R2-A enhancement: Files not tracked by any existing SpecNode are auto-created
    as L1_FILE OBSERVED nodes so they never enter a 'dark zone' invisible to the
    constraint graph.
    """
    repo = args.get("repo")
    files = args.get("files", [])
    if not repo:
        return _err("repo is required")
    if not files:
        return _err("files list is required and must not be empty")

    all_nodes = _all_nodes_for_repo(repo)
    changed_set = set(files)
    impacted_nodes = []

    # Collect all files already tracked across all nodes
    tracked_files: set[str] = set()
    for node in all_nodes:
        tracked_files.update(node.implementation.files)

    for node in all_nodes:
        node_files = set(node.implementation.files)
        overlap = changed_set.intersection(node_files)
        if overlap:
            # File changed. Apply a 10% penalty to confidence and cascade.
            old_conf = node.state.confidence
            new_score = float(round(old_conf * 0.9, 4)) if old_conf > 0 else 0.0

            node.state.evidence.append(Evidence(
                kind="code_change",
                score=new_score,
                detail=f"Files modified: {', '.join(sorted(overlap))}",
                source="git_hook",
                origin_id=f"sync_{node.id}",
            ))
            node.state.confidence = recalculate_confidence(node)
            node.state.status = SpecStatus.IN_PROGRESS if node.state.status == SpecStatus.VERIFIED else node.state.status
            node.updated_at = _now()

            save_spec_node(node)
            impacted_nodes.append(node.id)

    # ── R2-A: Auto-discover untracked source files as OBSERVED L1 nodes ───
    SOURCE_EXTENSIONS = {
        ".py", ".js", ".ts", ".tsx", ".jsx",
        ".go", ".rs", ".java", ".rb", ".c", ".cpp", ".h", ".cs",
        ".swift", ".kt", ".scala", ".ex", ".exs",
        ".sh", ".bash",
        ".yaml", ".yml",  # config-as-code (k8s, CI, OpenGrep rules)
        ".tf", ".hcl",    # infrastructure-as-code
    }
    auto_created: list[str] = []
    untracked = changed_set - tracked_files
    for filepath in sorted(untracked):
        # Only auto-create for source code files
        ext = "." + filepath.rsplit(".", 1)[-1].lower() if "." in filepath else ""
        if ext not in SOURCE_EXTENSIONS:
            continue

        # Derive a deterministic node_id from the filepath
        node_id = f"L1::{filepath.replace('/', '::').replace('.', '_')}"

        # Check dedup — the node may already exist under a different implementation.files set
        existing = get_spec_node(node_id)
        if existing:
            # Node exists but didn't have this file — add the file to its implementation
            if filepath not in existing.implementation.files:
                existing.implementation.files.append(filepath)
                existing.updated_at = _now()
                save_spec_node(existing)
            continue

        # Create a new OBSERVED L1 node
        new_node = SpecNode(
            id=node_id,
            repo=repo,
            title=filepath.rsplit("/", 1)[-1],  # basename as title
            level=NodeLevel.L1_FILE,
            surface_type=SurfaceType.INTERNAL,
            governance_tier=GovernanceTier.OBSERVED,
            implementation=Implementation(files=[filepath]),
            state=NodeState(
                status=SpecStatus.PENDING,
                stage=ExecutionStage.SPEC_DEFINED,
                confidence=0.0,
                evidence=[Evidence(
                    kind="code_change",
                    score=0.0,
                    detail=f"Auto-discovered during sync: {filepath}",
                    source="git_hook",
                )],
            ),
        )
        save_spec_node(new_node)
        auto_created.append(node_id)

    cascades = []
    for nid in impacted_nodes:
        # Cascade the change down the graph
        res = cascade_blast_radius(nid, repo)
        if res.get("impacted_count", 0) > 0:
            cascades.append(res)

    return _ok({
        "status": "synced",
        "repo": repo,
        "direct_impact_count": len(impacted_nodes),
        "direct_impact_nodes": impacted_nodes,
        "auto_discovered_count": len(auto_created),
        "auto_discovered_nodes": auto_created,
        "cascades_triggered": len(cascades),
        "cascades": cascades,
    })


async def handle_get_decision_queue(args: dict, ctx: ToolContext) -> list[TextContent]:
    """Provides a prioritized list of action items (Decision Queue) to the IDE Agent."""
    repo = args.get("repo")
    if not repo:
        return _err("repo is required")
    limit = int(args.get("limit", 10))

    gaps = detect_graph_gaps(repo)

    # Severity to sort weight
    severity_weight = {
        "critical": 4,
        "high": 3,
        "medium": 2,
        "low": 1,
    }

    # Sort gaps by descending severity
    sorted_gaps = sorted(gaps, key=lambda g: severity_weight.get(g.get("severity", "low"), 0), reverse=True)
    top_items = sorted_gaps[:limit]

    return _ok({
        "status": "success",
        "repo": repo,
        "total_items_in_queue": len(gaps),
        "returned_items": len(top_items),
        "decision_queue": top_items,
        "instruction": "Agent: Pick the TOP item from this queue, execute its 'action', then poll this queue again.",
    })


async def handle_promote_node(args: dict, ctx: ToolContext) -> list[TextContent]:
    from .models import GovernanceTier
    from .database import _get_connection, get_spec_node, save_spec_node
    from .models import Contract, NodeLevel, SpecStatus, SurfaceType, ExecutionStage, Evidence, _now, _ok, _err
    
    node_id = args.get("node_id")
    if not node_id:
        return _err("Missing node_id parameter")
        
    node = get_spec_node(node_id)
    if not node:
        return _err(f"Node {node_id} not found")
        
    current_tier = getattr(node, 'governance_tier', 'governed')
    if current_tier == 'observed':
        node.governance_tier = GovernanceTier.PROPOSED
    elif current_tier == 'proposed':
        node.governance_tier = GovernanceTier.GOVERNED
        # Apply skeleton contract on final promotion
        if not getattr(node, 'contract', None):
            node.contract = Contract()
        if not node.contract.invariants:
            if getattr(node, 'surface_type', 'internal') == SurfaceType.API_ENDPOINT:
                node.contract.invariants = ['returns valid HTTP status']
            else:
                node.contract.invariants = ['implements declared interface']
        
    save_spec_node(node)
    return _ok({
        "status": "success",
        "node_id": node_id,
        "new_tier": getattr(node.governance_tier, "value", str(node.governance_tier)) if hasattr(node, "governance_tier") else "governed",
        "message": f"Successfully promoted {node_id}"
    })


# =============================================================================
# REGISTRATION
# =============================================================================

# =============================================================================
# PHASE 2.5b: SECURITY RULE LIFECYCLE HANDLERS
# =============================================================================

async def handle_test_opengrep_rule(args: dict, ctx: ToolContext) -> list[TextContent]:
    """Sandbox: Test an OpenGrep YAML rule against vulnerable + safe code snippets.
    
    Runs in an isolated /tmp/turingmind-sandbox-<uuid>/ directory.
    Returns structured result: match info or syntax error.
    """
    import uuid
    import subprocess
    import tempfile
    import shutil

    rule_yaml = args.get("rule_yaml", "")
    vulnerable_code = args.get("vulnerable_code", "")
    safe_code = args.get("safe_code", "")
    language = args.get("language", "py")

    if not rule_yaml or not vulnerable_code or not safe_code:
        return _err("rule_yaml, vulnerable_code, and safe_code are all required")

    # Create UUID-namespaced sandbox directory
    sandbox_id = str(uuid.uuid4())[:8]
    sandbox_dir = Path(tempfile.gettempdir()) / f"turingmind-sandbox-{sandbox_id}"
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Write rule
        rule_file = sandbox_dir / "test_rule.yml"
        rule_file.write_text(rule_yaml)

        # Write vulnerable fixture
        vuln_file = sandbox_dir / f"vulnerable.{language}"
        vuln_file.write_text(vulnerable_code)

        # Write safe fixture
        safe_file = sandbox_dir / f"safe.{language}"
        safe_file.write_text(safe_code)

        # Test 1: Rule SHOULD fire on vulnerable code
        vuln_result = subprocess.run(
            ["opengrep", "scan", "--json", "--config", str(rule_file), str(vuln_file)],
            capture_output=True, text=True, timeout=30, cwd=str(sandbox_dir),
        )

        # Test 2: Rule should NOT fire on safe code
        safe_result = subprocess.run(
            ["opengrep", "scan", "--json", "--config", str(rule_file), str(safe_file)],
            capture_output=True, text=True, timeout=30, cwd=str(sandbox_dir),
        )

        # Parse results
        vuln_findings = _extract_findings_count(vuln_result.stdout)
        safe_findings = _extract_findings_count(safe_result.stdout)

        # Check for syntax errors in stderr
        if "error" in vuln_result.stderr.lower() and "invalid" in vuln_result.stderr.lower():
            return _ok({
                "status": "syntax_error",
                "error": vuln_result.stderr[:500],
                "suggestion": "The rule YAML has a syntax error. Check pattern format and indentation.",
            })

        passed = vuln_findings > 0 and safe_findings == 0
        return _ok({
            "status": "passed" if passed else "failed",
            "vulnerable_fires": vuln_findings > 0,
            "safe_clean": safe_findings == 0,
            "vulnerable_finding_count": vuln_findings,
            "safe_finding_count": safe_findings,
            "sandbox_id": sandbox_id,
            "detail": (
                "Rule validated: fires on vulnerable code and stays clean on safe code."
                if passed else
                f"Rule validation failed: vulnerable_fires={vuln_findings > 0}, safe_clean={safe_findings == 0}. "
                f"Adjust the pattern to be more specific."
            ),
        })

    except subprocess.TimeoutExpired:
        return _ok({"status": "timeout", "error": "opengrep timed out after 30s"})
    except FileNotFoundError:
        return _ok({"status": "error", "error": "opengrep binary not found"})
    finally:
        # Always clean up sandbox
        shutil.rmtree(sandbox_dir, ignore_errors=True)


def _extract_findings_count(stdout: str) -> int:
    """Extract the number of findings from OpenGrep JSON output."""
    try:
        start = stdout.find('{"version"')
        if start == -1:
            return 0
        remaining = stdout[start:]
        end = remaining.find('\n\n')
        json_str = remaining[:end].strip() if end != -1 else remaining.strip()
        data = json.loads(json_str)
        return len(data.get("results", []))
    except (json.JSONDecodeError, ValueError):
        return 0


async def handle_register_rule(args: dict, ctx: ToolContext) -> list[TextContent]:
    """Register a validated OpenGrep rule: write YAML + fixtures + Evidence."""
    rule_id = args.get("rule_id", "")
    rule_yaml = args.get("rule_yaml", "")
    vulnerable_code = args.get("vulnerable_code", "")
    safe_code = args.get("safe_code", "")
    language = args.get("language", "py")
    node_id = args.get("node_id")
    workspace_dir = args.get("workspace_dir", "")

    if not rule_id or not rule_yaml or not workspace_dir:
        return _err("rule_id, rule_yaml, and workspace_dir are required")

    workspace = Path(workspace_dir)
    rules_dir = workspace / ".opengrep" / "rules"
    tests_dir = workspace / ".opengrep" / "tests"
    rules_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    # Write rule file
    rule_filename = f"{rule_id}.yml"
    rule_path = rules_dir / rule_filename
    rule_path.write_text(rule_yaml)

    # Write test fixtures
    vuln_fixture = tests_dir / f"{rule_id}_vulnerable.{language}"
    safe_fixture = tests_dir / f"{rule_id}_safe.{language}"
    vuln_fixture.write_text(vulnerable_code)
    safe_fixture.write_text(safe_code)

    result = {
        "rule_file": str(rule_path),
        "test_vulnerable": str(vuln_fixture),
        "test_safe": str(safe_fixture),
        "active": True,
    }

    # Attach Evidence to SpecNode if node_id provided
    if node_id:
        try:
            node = get_spec_node(node_id)
            if node:
                node.state.evidence.append(Evidence(
                    kind="opengrep_rule",
                    score=0.9,  # High confidence — deterministic rule
                    detail=f"OpenGrep rule '{rule_id}' registered as anti-regression guard",
                    source="opengrep",
                    origin_id=f"rule_{rule_id}",
                ))
                node.state.confidence = recalculate_confidence(node)
                node.updated_at = _now()
                save_spec_node(node)
                result["evidence_attached_to"] = node_id
                result["new_confidence"] = node.state.confidence
        except Exception as e:
            result["evidence_error"] = str(e)

    return _ok(result)


async def handle_quarantine_rule(args: dict, ctx: ToolContext) -> list[TextContent]:
    """Emergency disable: move a rule from rules/ to archive/."""
    rule_id = args.get("rule_id", "")
    reason = args.get("reason", "")
    workspace_dir = args.get("workspace_dir", "")

    if not rule_id or not workspace_dir:
        return _err("rule_id and workspace_dir are required")

    workspace = Path(workspace_dir)
    rules_dir = workspace / ".opengrep" / "rules"
    archive_dir = workspace / ".opengrep" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    # Find the rule file
    rule_file = rules_dir / rule_id
    if not rule_file.exists():
        # Try with .yml extension
        rule_file = rules_dir / f"{rule_id}.yml"

    if not rule_file.exists():
        return _err(f"Rule file not found: {rule_id}")

    # Move to archive
    import shutil
    dest = archive_dir / rule_file.name
    shutil.move(str(rule_file), str(dest))

    return _ok({
        "quarantined": rule_file.name,
        "moved_to": str(dest),
        "reason": reason,
        "active": False,
    })


def apply_security_confidence_impact(
    node: "SpecNode",
    finding_type: str,  # "finding" (uncommitted) or "violation" (shipped to main)
    rule_id: str,
    repo: str,
) -> dict:
    """Two-tier confidence impact for security findings.
    
    - Finding on uncommitted code: -10% confidence, NO cascade
    - Violation shipped to main: -40% confidence + cascade_blast_radius()
    """
    if finding_type == "violation":
        # Shipped violation — severe penalty + cascade
        penalty = 0.6  # multiply by 0.6 = -40%
        node.state.evidence.append(Evidence(
            kind="security_violation",
            score=0.0,  # Full failure evidence
            detail=f"SECURITY VIOLATION: Rule '{rule_id}' violation shipped to main branch",
            source="opengrep_ci",
            origin_id=f"violation_{rule_id}",
        ))
        node.state.confidence = recalculate_confidence(node)
        node.updated_at = _now()
        save_spec_node(node)

        # Cascade blast radius
        cascade_result = cascade_blast_radius(node.id, repo)
        return {
            "impact_type": "violation",
            "penalty": "40%",
            "cascaded": True,
            "cascade_result": cascade_result,
        }
    else:
        # Uncommitted finding — minor penalty, no cascade
        node.state.evidence.append(Evidence(
            kind="security_violation",
            score=0.5,  # Moderate evidence — caught before ship
            detail=f"Security finding: Rule '{rule_id}' matched (caught pre-commit)",
            source="opengrep",
            origin_id=f"finding_{rule_id}",
        ))
        node.state.confidence = recalculate_confidence(node)
        node.updated_at = _now()
        save_spec_node(node)
        return {
            "impact_type": "finding",
            "penalty": "10%",
            "cascaded": False,
        }


# =============================================================================
# ROADMAP & INTENT LAYER
# =============================================================================

async def handle_link_intent(args: dict, ctx: ToolContext) -> list[TextContent]:
    node_id = args.get("node_id")
    intent_justification = args.get("intent_justification")

    if not node_id or not intent_justification:
        return _err("node_id and intent_justification are required")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

    node.intent_justification = intent_justification
    node.updated_at = _now()
    save_spec_node(node)

    return _ok({
        "status": "intent_linked",
        "node_id": node_id,
        "message": f"Successfully linked intent to SpecNode '{node.title}'."
    })


V2_HANDLERS: dict[str, Any] = {
    # Core graph
    "turingmind_create_spec_node": handle_create_spec_node,
    "turingmind_update_spec_node": handle_update_spec_node,
    "turingmind_save_architecture_diagram": handle_save_architecture_diagram,
    "turingmind_get_spec_status": handle_get_spec_status,
    "turingmind_list_spec_nodes": handle_list_spec_nodes,
    "turingmind_get_ready_nodes": handle_get_ready_nodes,
    # Execution & verification
    "turingmind_generate_verification": handle_generate_verification,
    "turingmind_run_verification": handle_run_verification,
    "turingmind_record_execution_stage": handle_record_execution_stage,
    # Repair loop
    "turingmind_classify_failure": handle_classify_failure,
    "turingmind_apply_fix": handle_apply_fix,
    # Safe change engine
    "turingmind_apply_spec_delta": handle_apply_spec_delta,
    "turingmind_get_impacted_nodes": handle_get_impacted_nodes,
    # Runtime signal ingestion
    "turingmind_ingest_runtime_signal": handle_ingest_runtime_signal,
    # Human gate
    "turingmind_request_approval": handle_request_approval,
    # Execution state
    "turingmind_get_execution_state": handle_get_execution_state,
    # Bootstrap
    "turingmind_bootstrap_codebase": handle_bootstrap_codebase,
    # Autonomous Engine
    "turingmind_sync_codebase": handle_sync_codebase,
    "turingmind_get_decision_queue": handle_get_decision_queue,
    # Cloud
    "turingmind_sync_cloud": handle_sync_cloud,
    # Phase 2.5b: Security Rule Lifecycle
    "turingmind_test_opengrep_rule": handle_test_opengrep_rule,
    "turingmind_register_rule": handle_register_rule,
    "turingmind_quarantine_rule": handle_quarantine_rule,
    # Roadmap & Intent
    "turingmind_link_intent": handle_link_intent,
}


def register(handlers: dict) -> None:
    """Register all v2 handlers into the global handler registry."""
    handlers.update(V2_HANDLERS)
