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
import uuid
from typing import Any

from mcp.types import TextContent

from ..tools.context import ToolContext
from .database import (
    get_execution_state,
    get_impacted_subgraph,
    get_nodes_by_stage,
    get_spec_node,
    save_execution_state,
    save_spec_node,
)
from .models import (
    Contract,
    Evidence,
    ExecutionStage,
    ExecutionState,
    FailureClassification,
    GlobalMetrics,
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
    )

    save_spec_node(node)
    return _ok({
        "status": "created",
        "node_id": node_id,
        "repo": repo,
        "level": level.value,
        "surface_type": surface.value,
        "stage": node.state.stage.value,
        "message": f"SpecNode '{title}' created. Next: turingmind_generate_verification",
    })


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

    node.updated_at = _now()
    save_spec_node(node)
    return _ok({"status": "updated", "node_id": node_id})


async def handle_get_spec_status(args: dict, ctx: ToolContext) -> list[TextContent]:
    node_id = args.get("node_id")
    if not node_id:
        return _err("node_id is required")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

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
        nodes = [n for n in nodes if n.level.value == level_filter]

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
            # Pytest summary format: "3 passed, 1 failed in 0.42s" or "3 passed in 0.42s"
            summary_match = re.search(
                r'(\d+) passed(?:,\s*(\d+) failed)?',
                output, re.MULTILINE
            )
            if summary_match:
                passed = int(summary_match.group(1))
                failed = int(summary_match.group(2) or 0)
            else:
                # Fallback: no summary line means collection error or empty suite
                passed = 0
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

    return _ok({
        "status": "recorded",
        "node_id": node_id,
        "stage": node.state.stage.value,
        "confidence": node.state.confidence,
        "evidence_count": len(node.state.evidence),
    })


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

    node.state.status = SpecStatus.FAILED
    node.state.failure_classification = classification
    node.state.failure_trace = failure_trace
    node.updated_at = _now()
    save_spec_node(node)

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

    return _ok({
        "status": "failure_classified",
        "node_id": node_id,
        "classification": classification.value,
        "escalation_action": escalation_map[classification],
    })


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
    save_spec_node(node)

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

    if all_nodes:
        total = len(all_nodes)
        verified_count = sum(1 for n in all_nodes if n.state.stage == ExecutionStage.VERIFIED)
        failed_count = sum(1 for n in all_nodes if n.state.status == SpecStatus.FAILED)
        passed_count = verified_count  # nodes that reached verified = passed
        tested_count = passed_count + failed_count

        avg_confidence = sum(n.state.confidence for n in all_nodes) / total
        pass_rate = (passed_count / tested_count) if tested_count > 0 else 0.0
        coverage = verified_count / total  # fraction of nodes fully verified

        state.metrics.system_confidence = round(avg_confidence, 3)
        state.metrics.pass_rate = round(pass_rate, 3)
        state.metrics.coverage = round(coverage, 3)

        # Sync failed_nodes list from actual node data
        actual_failed = [n.id for n in all_nodes if n.state.status == SpecStatus.FAILED]
        state.failed_nodes = actual_failed

    return _ok({
        "repo": repo,
        "ready_queue": state.ready_queue,
        "blocked_queue": state.blocked_queue,
        "failed_nodes": state.failed_nodes,
        "total_nodes": len(all_nodes) if all_nodes else 0,
        "verified_nodes": sum(1 for n in all_nodes if n.state.stage == ExecutionStage.VERIFIED) if all_nodes else 0,
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
    patterns     = args.get("include_patterns") or ["*.py", "*.ts", "*.js", "*.jsx", "*.tsx"]
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
            level=NodeLevel.L2,
            surface_type=SurfaceType.INTERNAL,
            implementation=Implementation(files=files),
        )
        save_spec_node(node)
        created.append({"node_id": node_id, "title": title, "files": files})

    return _ok({
        "status": "bootstrapped" if not dry_run else "dry_run",
        "repo": repo,
        "nodes_created": len(created) if not dry_run else 0,
        "nodes_dry_run": len(created) if dry_run else 0,
        "nodes_skipped": len(skipped),
        "created": created,
        "skipped": skipped,
        "message": (
            f"{'Would create' if dry_run else 'Created'} {len(created)} L2 SpecNodes from {sum(len(v) for v in module_groups.values())} files. "
            "Contracts are blank — fill in invariants and metrics progressively."
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
# REGISTRATION
# =============================================================================

V2_HANDLERS: dict[str, Any] = {
    # Core graph
    "turingmind_create_spec_node": handle_create_spec_node,
    "turingmind_update_spec_node": handle_update_spec_node,
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
    # Cloud
    "turingmind_sync_cloud": handle_sync_cloud,
}


def register(handlers: dict) -> None:
    """Register all v2 handlers into the global handler registry."""
    handlers.update(V2_HANDLERS)
