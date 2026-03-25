"""
TuringMind v2 Engine — Tool Registry
~25 strict constraint-graph primitives replacing the legacy 74-tool surface.

Structure:
  - NEW: SpecNode graph operations (create, update, query)
  - NEW: Execution loop (execute, generate_verification, classify_failure, apply_fix)
  - NEW: Change propagation (apply_spec_delta, get_impacted_nodes)
  - NEW: Human gate (request_approval)
  - KEPT: Code intelligence (index_codebase, get_related_code, get_project_structure)
  - KEPT: Change tools (analyze_diff, apply_edit)
  - KEPT: Memory (get/save)
  - KEPT: Audit trail (log_reasoning, get_audit_trail)
"""

from __future__ import annotations

from mcp.types import Tool

# ──────────────────────────────────────────────────────────────────────────────
# GOLDEN TOOLS — Re-exported directly from the legacy registry.
# These have real implementations inside bridge_server.py and must not be
# redefined. We simply reference their Tool definitions here.
# ──────────────────────────────────────────────────────────────────────────────
from turingmind_mcp.tool_registry import ALL_TOOLS as _LEGACY_ALL

_GOLDEN_NAMES = {
    # Code intelligence — becomes the Spec Compiler input layer
    "turingmind_index_codebase",
    "turingmind_get_related_code",
    "turingmind_get_project_structure",
    "turingmind_get_edit_reasoning",
    # Change engine primitives
    "turingmind_analyze_diff",
    "turingmind_apply_edit",
    # Memory — reinterpreted as spec/execution knowledge store
    "turingmind_get_memory",
    "turingmind_save_memory",
    "turingmind_list_memory",
    # Audit trail — enterprise trust and agent debugging
    "turingmind_log_reasoning",
    "turingmind_get_audit_trail",
    # Auth (still needed to talk to TuringMind cloud for indexing)
    "turingmind_validate_auth",
}

GOLDEN_TOOLS: list[Tool] = [t for t in _LEGACY_ALL if t.name in _GOLDEN_NAMES]

# ──────────────────────────────────────────────────────────────────────────────
# NEW V2 TOOLS — The Constraint Graph Engine API
# ──────────────────────────────────────────────────────────────────────────────

_SPEC_NODE_PROPS = {
    "node_id": {"type": "string", "description": "Unique deterministic ID for this constraint node"},
    "repo": {"type": "string", "description": "Repository (owner/repo)"},
    "title": {"type": "string", "description": "Short human-readable title"},
    "level": {
        "type": "string",
        "enum": ["L0", "L1", "L2", "L3"],
        "description": "L0=system, L1=service, L2=module/api, L3=function",
    },
    "surface_type": {
        "type": "string",
        "enum": ["internal", "api_endpoint", "job", "hardware_bridge"],
        "description": "Risk surface classification. api_endpoint nodes appear in Risk Posture Map.",
    },
    "contract": {
        "type": "object",
        "description": "Strict mathematical contract: inputs, outputs, invariants, metrics",
        "properties": {
            "inputs": {"type": "object"},
            "outputs": {"type": "object"},
            "invariants": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Rules that must never be violated (e.g. requires_jwt_auth)",
            },
            "metrics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Performance/quality constraints (e.g. p99_latency < 200ms)",
            },
        },
    },
    "dependencies": {
        "type": "array",
        "items": {"type": "string"},
        "description": "IDs of upstream SpecNodes this node depends on",
    },
    "priority": {
        "type": "string",
        "enum": ["critical", "high", "medium", "low"],
    },
}

V2_TOOLS: list[Tool] = [
    # ──────────────────────────────────────────────────────────────────────────
    # CORE GRAPH
    # ──────────────────────────────────────────────────────────────────────────
    Tool(
        name="turingmind_create_spec_node",
        description=(
            "Create an atomic SpecNode in the constraint DAG. "
            "Every unit of work is represented as a SpecNode with a strict contract "
            "(inputs, outputs, invariants) and a surface_type for risk posture mapping. "
            "Architect Mode only: do NOT write code, only define constraints."
        ),
        inputSchema={
            "type": "object",
            "properties": {k: v for k, v in _SPEC_NODE_PROPS.items()},
            "required": ["node_id", "repo", "title", "level"],
        },
    ),
    Tool(
        name="turingmind_update_spec_node",
        description=(
            "Update the contract or metadata of an existing SpecNode. "
            "Updating a contract triggers automatic subgraph invalidation downstream. "
            "Use this when refining specs based on new information or audit results."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "SpecNode ID to update"},
                "contract": _SPEC_NODE_PROPS["contract"],
                "priority": _SPEC_NODE_PROPS["priority"],
                "surface_type": _SPEC_NODE_PROPS["surface_type"],
            },
            "required": ["node_id"],
        },
    ),
    Tool(
        name="turingmind_get_spec_status",
        description=(
            "Get the full state of a SpecNode: stage, confidence, failure classification. "
            "Use to query where a node is in the Manufacturing Line pipeline."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["node_id"],
        },
    ),
    Tool(
        name="turingmind_list_spec_nodes",
        description=(
            "List SpecNodes for a repository, optionally filtered by stage, surface_type, or level. "
            "Use stage=failed to find nodes requiring repair. "
            "Use surface_type=api_endpoint to build the Risk Posture Map."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "stage": {
                    "type": "string",
                    "enum": ["spec_defined", "verification_generated", "implementing", "auditing", "verified", "all"],
                    "default": "all",
                },
                "surface_type": {
                    "type": "string",
                    "enum": ["internal", "api_endpoint", "job", "hardware_bridge", "all"],
                    "default": "all",
                },
                "level": {"type": "string", "enum": ["L0", "L1", "L2", "L3", "all"], "default": "all"},
            },
            "required": ["repo"],
        },
    ),
    Tool(
        name="turingmind_get_ready_nodes",
        description=(
            "Get all SpecNodes whose upstream dependencies are fully verified (ready_queue). "
            "The execution loop calls this to determine what the Builder can work on next."
        ),
        inputSchema={
            "type": "object",
            "properties": {"repo": {"type": "string"}},
            "required": ["repo"],
        },
    ),
    # ──────────────────────────────────────────────────────────────────────────
    # EXECUTION & VERIFICATION
    # ──────────────────────────────────────────────────────────────────────────
    Tool(
        name="turingmind_generate_verification",
        description=(
            "Tester Mode: Generate the full verification suite for a SpecNode from its contract. "
            "Translates invariants → property tests, inputs/outputs → unit tests, "
            "ambiguous paths → fuzz tests, metrics → performance checks. "
            "Do NOT write application code in this mode."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "SpecNode to generate tests for"},
                "verification_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["unit_tests", "property_tests", "fuzz_tests", "static_checks", "security_checks", "performance_checks"],
                    },
                    "description": "Which verification types to generate (default: all)",
                },
            },
            "required": ["node_id"],
        },
    ),
    Tool(
        name="turingmind_run_verification",
        description=(
            "Auditor Mode: Execute the verification suite for a SpecNode and record results. "
            "Runs tests, static checks, security scans. Returns structured pass/fail results "
            "and automatically updates node confidence score."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "verification_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Subset of checks to run (default: all)",
                },
            },
            "required": ["node_id"],
        },
    ),
    Tool(
        name="turingmind_record_execution_stage",
        description=(
            "Builder/Auditor Mode: Record a SpecNode's current execution stage and confidence. "
            "Called by the agent as it moves a node through the Manufacturing Line pipeline: "
            "spec_defined → verification_generated → implementing → auditing → verified."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "stage": {
                    "type": "string",
                    "enum": ["spec_defined", "verification_generated", "implementing", "auditing", "verified"],
                },
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "verified", "failed"],
                },
            },
            "required": ["node_id", "stage", "status"],
        },
    ),
    # ──────────────────────────────────────────────────────────────────────────
    # REPAIR LOOP
    # ──────────────────────────────────────────────────────────────────────────
    Tool(
        name="turingmind_classify_failure",
        description=(
            "Classify a SpecNode failure deterministically. Do NOT guess. "
            "Exactly one classification applies:\n"
            "  spec_gap → contract is incomplete or ambiguous → escalate to Architect Mode\n"
            "  test_gap → tests don't cover the failure scenario → escalate to Tester Mode\n"
            "  implementation_bug → code is wrong, spec and tests are correct → escalate to Builder Mode\n"
            "  dependency_failure → upstream node is broken, block this node"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "classification": {
                    "type": "string",
                    "enum": ["spec_gap", "test_gap", "implementation_bug", "dependency_failure"],
                },
                "failure_trace": {
                    "type": "string",
                    "description": "Raw test output, stack trace, or error message",
                },
                "evidence": {
                    "type": "string",
                    "description": "Why this classification was chosen",
                },
            },
            "required": ["node_id", "classification", "failure_trace"],
        },
    ),
    Tool(
        name="turingmind_apply_fix",
        description=(
            "Record that a repair action was applied to a failed SpecNode. "
            "Resets the node stage so the execution loop re-runs verification. "
            "Must be called after the repair action (code patch, test expansion, spec refinement)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "fix_type": {
                    "type": "string",
                    "enum": ["refine_spec", "expand_tests", "patch_code", "unblock_dependency"],
                },
                "description": {"type": "string", "description": "What was changed"},
            },
            "required": ["node_id", "fix_type", "description"],
        },
    ),
    # ──────────────────────────────────────────────────────────────────────────
    # SAFE CHANGE ENGINE — The Killer Feature
    # ──────────────────────────────────────────────────────────────────────────
    Tool(
        name="turingmind_apply_spec_delta",
        description=(
            "THE KILLER FEATURE: Apply a contract change to a SpecNode and trigger "
            "automatic downstream invalidation. All dependent nodes in the DAG will have "
            "their verification and implementation state reset, placing them back in "
            "the ready_queue for automatic regeneration. "
            "Use this whenever a requirement changes — the engine guarantees correctness is restored."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "The SpecNode whose contract changed"},
                "delta": {
                    "type": "object",
                    "description": "The partial contract update to apply",
                    "properties": {
                        "invariants_added": {"type": "array", "items": {"type": "string"}},
                        "invariants_removed": {"type": "array", "items": {"type": "string"}},
                        "inputs_changed": {"type": "object"},
                        "outputs_changed": {"type": "object"},
                    },
                },
                "reason": {"type": "string", "description": "Why the spec changed"},
            },
            "required": ["node_id", "delta"],
        },
    ),
    Tool(
        name="turingmind_get_impacted_nodes",
        description=(
            "Compute the exact blast radius of a SpecNode change: all downstream nodes "
            "in the DAG that depend on this node (directly or transitively). "
            "Use BEFORE applying a spec change to preview impact on the manufacturing line."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Origin node to compute impact from"},
            },
            "required": ["node_id"],
        },
    ),
    # ──────────────────────────────────────────────────────────────────────────
    # HUMAN GATE
    # ──────────────────────────────────────────────────────────────────────────
    Tool(
        name="turingmind_request_approval",
        description=(
            "Request human approval for a SpecNode. Only use for: "
            "(1) L0/L1 spec approval before system-wide execution, "
            "(2) low-confidence nodes (< 0.6) after repair cycles, "
            "(3) high-risk surface changes (api_endpoint or security_checks failing). "
            "For everything else, the engine runs autonomously."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "reason": {
                    "type": "string",
                    "enum": ["spec_approval", "low_confidence", "high_risk_surface"],
                },
                "context": {
                    "type": "string",
                    "description": "Summary of the situation requiring approval",
                },
            },
            "required": ["node_id", "reason", "context"],
        },
    ),
    # ──────────────────────────────────────────────────────────────────────────
    # EXECUTION STATE
    # ──────────────────────────────────────────────────────────────────────────
    Tool(
        name="turingmind_get_execution_state",
        description=(
            "Get the global control plane state: ready_queue, blocked_queue, failed_nodes, "
            "and global confidence metrics. "
            "The UI polls this to render the Manufacturing Line and Confidence Score dial."
        ),
        inputSchema={
            "type": "object",
            "properties": {"repo": {"type": "string"}},
            "required": ["repo"],
        },
    ),
]

# ──────────────────────────────────────────────────────────────────────────────
# Combined: the complete v2 surface area
# ──────────────────────────────────────────────────────────────────────────────
ALL_V2_TOOLS: list[Tool] = V2_TOOLS + GOLDEN_TOOLS

__all__ = ["ALL_V2_TOOLS", "V2_TOOLS", "GOLDEN_TOOLS"]
