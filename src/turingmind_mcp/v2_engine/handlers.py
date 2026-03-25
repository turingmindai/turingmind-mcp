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
    ExecutionStage,
    ExecutionState,
    FailureClassification,
    GlobalMetrics,
    NodeLevel,
    NodeState,
    Priority,
    SpecNode,
    SpecStatus,
    SurfaceType,
)


def _ok(data: dict[str, Any]) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, default=str))]


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"error": msg}))]


def _now() -> str:
    return datetime.datetime.utcnow().isoformat()


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
    if not node_id:
        return _err("node_id is required")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

    # Tester Mode: auto-generate stub verification from the contract invariants
    requested = set(args.get("verification_types") or [
        "unit_tests", "property_tests", "fuzz_tests",
        "static_checks", "security_checks", "performance_checks"
    ])

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
            slug = metric.lower().replace(" ", "_")[:40]
            node.verification.performance_checks.append(f"perf_{node_id}_{slug}")

    node.state.stage = ExecutionStage.VERIFICATION_GENERATED
    node.updated_at = _now()
    save_spec_node(node)

    return _ok({
        "status": "verification_generated",
        "node_id": node_id,
        "unit_tests": node.verification.unit_tests,
        "property_tests": node.verification.property_tests,
        "fuzz_tests": node.verification.fuzz_tests,
        "security_checks": node.verification.security_checks,
        "performance_checks": node.verification.performance_checks,
        "message": "Verification suite generated. Next: Builder Mode → implement, then turingmind_run_verification",
    })


async def handle_run_verification(args: dict, ctx: ToolContext) -> list[TextContent]:
    node_id = args.get("node_id")
    if not node_id:
        return _err("node_id is required")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

    # Auditor Mode: advance stage and signal ready for result recording
    node.state.stage = ExecutionStage.AUDITING
    node.updated_at = _now()
    save_spec_node(node)

    return _ok({
        "status": "auditing",
        "node_id": node_id,
        "message": (
            "Verification suite queued for execution. "
            "Run your test suite, then call turingmind_record_execution_stage "
            "or turingmind_classify_failure with results."
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

    if not node_id or not stage_raw or not status_raw:
        return _err("node_id, stage, and status are required")

    node = get_spec_node(node_id)
    if not node:
        return _err(f"SpecNode '{node_id}' not found")

    try:
        node.state.stage = ExecutionStage(stage_raw)
    except ValueError:
        return _err(f"Invalid stage: {stage_raw}")

    try:
        node.state.status = SpecStatus(status_raw)
    except ValueError:
        return _err(f"Invalid status: {status_raw}")

    if confidence is not None:
        node.state.confidence = max(0.0, min(1.0, float(confidence)))

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

async def handle_get_execution_state(args: dict, ctx: ToolContext) -> list[TextContent]:
    repo = args.get("repo")
    if not repo:
        return _err("repo is required")

    state = get_execution_state(repo)
    return _ok({
        "repo": repo,
        "ready_queue": state.ready_queue,
        "blocked_queue": state.blocked_queue,
        "failed_nodes": state.failed_nodes,
        "metrics": {
            "coverage": state.metrics.coverage,
            "pass_rate": state.metrics.pass_rate,
            "system_confidence": state.metrics.system_confidence,
        },
    })


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
    # Human gate
    "turingmind_request_approval": handle_request_approval,
    # Execution state
    "turingmind_get_execution_state": handle_get_execution_state,
}


def register(handlers: dict) -> None:
    """Register all v2 handlers into the global handler registry."""
    handlers.update(V2_HANDLERS)
