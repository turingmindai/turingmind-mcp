from __future__ import annotations

import pytest
import uuid

def test_cp_pager_loads_scope_once(api_client, tier_repo):
    """TC-CP-21: Memory Pager loads scope rules once and avoids duplicate hydration on subsequent edits."""
    client, db = api_client
    composer_id = f"composer-{uuid.uuid4()}"

    # 1. Sync first time (database subsystem) -> should trigger page fault (hydrate_required=True)
    p1 = {
        "repo": tier_repo,
        "files": ["src/database.py"],
        "composer_id": composer_id,
    }
    r1 = client.post("/api/v2/sync", json=p1)
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["recall_bundle"]["policy"]["hydrate_required"] is True

    # 2. Sync second time (same file/subsystem) -> should NOT trigger page fault (hydrate_required=False)
    r2 = client.post("/api/v2/sync", json=p1)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["recall_bundle"]["policy"]["hydrate_required"] is False


def test_cp_pager_drift_detection(api_client, tier_repo):
    """TC-CP-27: Emits drift policy code (TM-DRIFT-002) when editing across disjoint subsystems."""
    client, db = api_client
    composer_id = f"composer-{uuid.uuid4()}"

    # 1. Sync first time (database subsystem) -> loads "database" scope
    p1 = {
        "repo": tier_repo,
        "files": ["src/database.py"],
        "composer_id": composer_id,
    }
    r1 = client.post("/api/v2/sync", json=p1)
    assert r1.status_code == 200

    # 2. Sync second time (reconcile subsystem) -> triggers TM-DRIFT-002 warning
    p2 = {
        "repo": tier_repo,
        "files": ["src/reconcile.py"],
        "composer_id": composer_id,
    }
    r2 = client.post("/api/v2/sync", json=p2)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["recall_bundle"]["policy"]["code"] == "TM-DRIFT-002"
    assert "drift" in d2["recall_bundle"]["policy"]["message"].lower()


def test_cp_pager_scope_invalidation(api_client, tier_repo):
    """TC-CP-26: AST / structural changes invalidate loaded scopes, triggering new page faults."""
    client, db = api_client
    composer_id = f"composer-{uuid.uuid4()}"

    # 1. Sync first time
    p1 = {
        "repo": tier_repo,
        "files": ["src/database.py"],
        "composer_id": composer_id,
    }
    r1 = client.post("/api/v2/sync", json=p1)
    assert r1.status_code == 200
    session_id = r1.json()["session"]["session_id"]

    # 2. Simulate structural change invalidation by clearing loaded_scopes in the database
    # In practice, this could be triggered by import graph refactoring or git pull deltas.
    db.update_coding_session(
        session_id=session_id,
        loaded_scopes=[], # Clear scopes to force reload
        touched_files=[],
        touched_subsystems=[],
        recall_history=[],
    )

    # 3. Next sync should trigger page fault again
    r2 = client.post("/api/v2/sync", json=p1)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["recall_bundle"]["policy"]["hydrate_required"] is True
