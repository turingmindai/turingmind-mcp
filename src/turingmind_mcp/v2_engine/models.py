"""
Pydantic data models for the v2 Deterministic Constraint Engine.

Design principles (post-SDLC):
- Confidence is evidenced, not asserted.
- Metrics have thresholds — they are checkable, not descriptive.
- Runtime signals are first-class citizens that can invalidate nodes.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Dict, Any, Optional, Literal, Union
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class NodeLevel(str, Enum):
    L0_INFRA = "L0_INFRA"                 # System/Architecture level
    L1_FILE = "L1_FILE"                   # Service/Component code
    L2_EXTERNAL = "L2_EXTERNAL"           # SBOM/Dependencies/Module API
    L3_API = "L3_API"                     # Network Ingress/Egress 
    L4_FEATURE = "L4_FEATURE"             # Feature/Ticket tracker
    L5_BUSINESS_GOAL = "L5_BUSINESS_GOAL" # Epic/Business Intent
    L6_ACTION_ITEM = "L6_ACTION_ITEM"     # Decision Queue Task


class SurfaceType(str, Enum):
    INTERNAL = "internal"
    API_ENDPOINT = "api_endpoint"
    JOB = "job"
    HARDWARE_BRIDGE = "hardware_bridge"


class Priority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SpecStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    FAILED = "failed"


class ExecutionStage(str, Enum):
    SPEC_DEFINED = "spec_defined"
    VERIFICATION_GENERATED = "verification_generated"
    IMPLEMENTING = "implementing"
    AUDITING = "auditing"
    VERIFIED = "verified"


class FailureClassification(str, Enum):
    SPEC_GAP = "spec_gap"
    TEST_GAP = "test_gap"
    IMPLEMENTATION_BUG = "implementation_bug"
    DEPENDENCY_FAILURE = "dependency_failure"


class SignalType(str, Enum):
    ERROR_RATE = "error_rate"
    P95_LATENCY = "p95_latency"
    REGRESSION = "regression"
    USER_FEEDBACK = "user_feedback"
    COVERAGE_DROP = "coverage_drop"
    SECURITY_FINDING = "security_finding"
    CODE_REVIEW = "code_review"


# ==========================================
# Core Constraint Definitions
# ==========================================

class Metric(BaseModel):
    """
    A measurable, checkable performance or quality constraint.
    Replaces the old List[str] metrics — a string cannot be checked.
    """
    name: str = Field(..., description="Human-readable name, e.g. 'p99_response_time'")
    threshold: float = Field(..., description="Limit that must not be exceeded (or must be met)")
    unit: str = Field(default="", description="e.g. 'ms', 'percent', 'count'")
    direction: Literal["below", "above"] = Field(
        default="below",
        description="'below' = value must stay under threshold. 'above' = must stay above."
    )

    def is_breached(self, observed: float) -> bool:
        if self.direction == "below":
            return observed > self.threshold
        return observed < self.threshold


class Contract(BaseModel):
    """The strict mathematical definition of what this node must achieve."""
    inputs: Dict[str, Any] = Field(default_factory=dict, description="Input schema/types")
    outputs: Dict[str, Any] = Field(default_factory=dict, description="Output schema/types")
    invariants: List[str] = Field(
        default_factory=list,
        description="Rules that must never be broken (e.g. requires_jwt). String-form for backward compat."
    )
    metrics: List[Metric] = Field(
        default_factory=list,
        description="Checkable performance/quality constraints with numeric thresholds."
    )


class Verification(BaseModel):
    """The test suite generated to prove the contract."""
    unit_tests: List[str] = Field(default_factory=list)
    property_tests: List[str] = Field(default_factory=list)
    fuzz_tests: List[str] = Field(default_factory=list)
    static_checks: List[str] = Field(default_factory=list)
    security_checks: List[str] = Field(default_factory=list)
    performance_checks: List[str] = Field(default_factory=list)


class Implementation(BaseModel):
    """The actual code footprint satisfying the verification."""
    files: List[str] = Field(default_factory=list)
    functions: List[str] = Field(default_factory=list)


# ==========================================
# Evidence — the audit trail for confidence
# ==========================================

class Evidence(BaseModel):
    """
    A single piece of proof that contributed to (or reduced) this node's confidence.
    Confidence must be evidenced, not asserted.
    """
    kind: Literal[
        "test_run",             # pytest/jest results
        "approval",             # human sign-off
        "runtime_sample",       # live metric reading
        "regression_clear",     # a regression was fixed and verified
        "security_scan",        # SAST/DAST result
        "blast_radius_cascade", # confidence penalty propagated from a failed upstream node
        "code_change",          # Git hook detected a file modification
    ] = Field(..., description="What kind of evidence this is")
    recorded_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    score: float = Field(
        ..., ge=0.0, le=1.0,
        description="How much this evidence contributes to confidence (0.0 = full failure, 1.0 = full pass)"
    )
    detail: str = Field(default="", description="Human-readable summary, e.g. '47 passed, 0 failed, 92% coverage'")
    source: str = Field(default="unknown", description="Origin: 'gemini', 'ci', 'cursor', 'sentry', 'runtime'")
    run_id: Optional[str] = Field(
        default=None,
        description="UUID from turingmind_run_verification. Required for confidence > 0.7 unless source is 'pytest'."
    )
    origin_id: Optional[str] = Field(
        default=None,
        description="For blast_radius_cascade evidence: the node_id of the origin failure that triggered this cascade. Used for idempotency checks."
    )


class NodeState(BaseModel):
    """The real-time execution state of a node, with a full evidence trail."""
    status: SpecStatus = Field(default=SpecStatus.PENDING)
    stage: ExecutionStage = Field(default=ExecutionStage.SPEC_DEFINED)
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Composite confidence derived from evidence[]. Never set directly without appending evidence."
    )
    failure_classification: Optional[FailureClassification] = None
    failure_trace: Optional[str] = None
    last_verified_run_id: Optional[str] = Field(
        default=None,
        description="UUID from the last successful turingmind_run_verification call. "
                    "Must match evidence.run_id for confidence > 0.7 in record_execution_stage."
    )
    review_depth: int = Field(
        default=0,
        description="Number of consecutive clean review passes this node has survived. "
                    "Reset to 0 when a review finds issues. Feeds into confidence boost."
    )
    evidence: List[Evidence] = Field(
        default_factory=list,
        description="Ordered audit trail of all confidence-affecting events. "
                    "Confidence = weighted average of recent evidence scores."
    )


class SpecNode(BaseModel):
    """The atomic unit of work in the constraint DAG."""
    id: str = Field(..., description="Unique deterministic ID")
    repo: str = Field(..., description="Target repository (owner/repo)")
    title: str = Field(..., description="Human readable summary")
    level: NodeLevel = Field(...)
    surface_type: SurfaceType = Field(default=SurfaceType.INTERNAL)

    contract: Contract = Field(default_factory=Contract)
    verification: Verification = Field(default_factory=Verification)
    implementation: Implementation = Field(default_factory=Implementation)
    state: NodeState = Field(default_factory=NodeState)

    dependencies: List[str] = Field(default_factory=list, description="IDs of upstream nodes this node depends on")
    dependents: List[str] = Field(default_factory=list, description="IDs of downstream nodes blocked by this node")

    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ==========================================
# Control Plane Definitions
# ==========================================

class GlobalMetrics(BaseModel):
    """System-wide health indicators."""
    coverage: float = 0.0
    pass_rate: float = 0.0
    system_confidence: float = 0.0


class ExecutionState(BaseModel):
    """The global control plane managing the DAG execution."""
    current_node: Optional[str] = None
    ready_queue: List[str] = Field(default_factory=list)
    blocked_queue: List[str] = Field(default_factory=list)
    failed_nodes: List[str] = Field(default_factory=list)

    metrics: GlobalMetrics = Field(default_factory=GlobalMetrics)
