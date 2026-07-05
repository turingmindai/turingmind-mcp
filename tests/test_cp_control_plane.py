from __future__ import annotations

import pytest
import uuid
from turingmind_mcp.control_plane import CognitionControlPlane
from turingmind_mcp.v2_engine.database import get_all_spec_nodes

def test_cp_control_plane_sync_and_gc(memory_db, tier_repo):
    """TC-CP-30: Verify CognitionControlPlane orchestrator performs sync, patch, and GC correctly."""
    composer_id = f"composer-cp-{uuid.uuid4()}"
    
    # 1. Sync first time to create session
    res = CognitionControlPlane.sync_codebase(
        db=memory_db,
        repo=tier_repo,
        files=["src/reconcile.py"],
        composer_id=composer_id,
        branch="main",
    )
    assert res["status"] == "synced"
    session_id = res["session"]["session_id"]
    
    # Verify session in db
    sess = memory_db.get_coding_session_by_id(session_id)
    assert sess is not None
    assert sess["composer_id"] == composer_id
    
    # 2. Patch session
    patched = CognitionControlPlane.patch_session(
        db=memory_db,
        session_id=session_id,
        loaded_scopes=["reconcile", "database"],
        touched_files=["src/reconcile.py", "src/database.py"],
        touched_subsystems=["reconcile", "database"],
        policy_state="hydrated",
    )
    assert "reconcile" in patched["loaded_scopes"]
    assert "database" in patched["loaded_scopes"]

    # 3. Run GC on active session (should not expire yet)
    CognitionControlPlane.run_session_gc(memory_db)
    assert memory_db.get_coding_session_by_id(session_id) is not None

    # 4. Set expires_at to past and run GC
    memory_db.update_coding_session(
        session_id=session_id,
        loaded_scopes=["reconcile", "database"],
        touched_files=["src/reconcile.py", "src/database.py"],
        touched_subsystems=["reconcile", "database"],
        recall_history=[],
        expires_at="2020-01-01T00:00:00",
    )
    CognitionControlPlane.run_session_gc(memory_db)
    assert memory_db.get_coding_session_by_id(session_id) is None


def test_cp_control_plane_autobootstrap(memory_db, monkeypatch):
    """TC-CP-32: Verify empty repositories automatically bootstrap default spec nodes."""
    monkeypatch.setenv("TURINGMIND_PROFILE", "governed")
    temp_repo = f"test-org/bootstrap-{uuid.uuid4()}"
    
    # 1. Verify 0 spec nodes exist
    nodes_before = get_all_spec_nodes(temp_repo)
    assert len(nodes_before) == 0
    
    # 2. Sync to trigger auto-bootstrap
    res = CognitionControlPlane.sync_codebase(
        db=memory_db,
        repo=temp_repo,
        files=["src/reconcile.py"],
        composer_id=f"composer-boot-{uuid.uuid4()}",
    )
    assert res["status"] == "synced"
    
    # 3. Verify a bootstrap spec node now exists
    nodes_after = get_all_spec_nodes(temp_repo)
    assert len(nodes_after) == 1
    assert nodes_after[0].id == f"bootstrap-root-{temp_repo.replace('/', '-')}"
    assert nodes_after[0].title == "System Spec Root Constraints"
