import logging
import datetime
from typing import List, Optional, Any, Dict
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from .v2_engine.models import SpecNode, ExecutionState, SpecStatus, ExecutionStage, FailureClassification, Evidence
from .v2_engine.database import get_all_spec_nodes, get_execution_state, get_spec_node, save_spec_node, save_execution_state, get_impacted_subgraph

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
