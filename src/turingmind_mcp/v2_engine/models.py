"""
Pydantic data models for the v2 Deterministic Constraint Engine.
These models represent the SpecNode DAG and Execution loop state.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field

class NodeLevel(str, Enum):
    L0 = "L0"  # System/Architecture level
    L1 = "L1"  # Service/Component level
    L2 = "L2"  # Module/API level
    L3 = "L3"  # Function/Implementation level

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

# ==========================================
# Core Constraint Definitions
# ==========================================

class Contract(BaseModel):
    """The strict mathematical definition of what this node must achieve."""
    inputs: Dict[str, Any] = Field(default_factory=dict, description="Input schema/types")
    outputs: Dict[str, Any] = Field(default_factory=dict, description="Output schema/types")
    invariants: List[str] = Field(default_factory=list, description="Rules that must never be broken (e.g. requires_jwt)")
    metrics: List[str] = Field(default_factory=list, description="Performance or quality metrics")

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

class NodeState(BaseModel):
    """The real-time execution bounds of the node."""
    status: SpecStatus = Field(default=SpecStatus.PENDING)
    stage: ExecutionStage = Field(default=ExecutionStage.SPEC_DEFINED)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    failure_classification: Optional[FailureClassification] = None
    failure_trace: Optional[str] = None

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
    
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

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
