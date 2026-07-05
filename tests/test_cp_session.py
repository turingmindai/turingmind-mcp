from __future__ import annotations

import pytest
import uuid

def test_cp_session_creation_on_sync(api_client, tier_repo):
    """TC-CP-20: Sync creates a coding session row in the database on first contact."""
    client, db = api_client
    composer_id = f"composer-{uuid.uuid4()}"

    # Perform first sync
    payload = {
        "repo": tier_repo,
        "files": ["src/reconcile.py"],
        "composer_id": composer_id,
        "branch": "main",
    }
    response = client.post("/api/v2/sync", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert "session" in res_data
    session_id = res_data["session"]["session_id"]
    assert session_id is not None

    # Fetch from GET /api/v2/session endpoint
    get_res = client.get(f"/api/v2/session?repo={tier_repo}&composer_id={composer_id}")
    assert get_res.status_code == 200
    sess_data = get_res.json()
    assert sess_data["session_id"] == session_id
    assert sess_data["composer_id"] == composer_id
    assert "reconcile" in sess_data["loaded_scopes"]


def test_cp_session_cross_ide_recovery(api_client, tier_repo):
    """TC-CP-23: Cross-IDE session recovery using the same composer ID and repo."""
    client, db = api_client
    composer_id = "shared-composer-999"

    # 1. Sync from IDE 1 (Cursor)
    p1 = {
        "repo": tier_repo,
        "files": ["src/reconcile.py"],
        "composer_id": composer_id,
    }
    r1 = client.post("/api/v2/sync", json=p1)
    assert r1.status_code == 200
    sid1 = r1.json()["session"]["session_id"]

    # 2. Sync from IDE 2 (Antigravity)
    p2 = {
        "repo": tier_repo,
        "files": ["src/database.py"],
        "composer_id": composer_id,
    }
    r2 = client.post("/api/v2/sync", json=p2)
    assert r2.status_code == 200
    sid2 = r2.json()["session"]["session_id"]

    # Must recover the SAME session ID, and merge loaded scopes
    assert sid1 == sid2
    
    # Check session contents
    get_res = client.get(f"/api/v2/session?repo={tier_repo}&composer_id={composer_id}")
    sess_data = get_res.json()
    assert "reconcile" in sess_data["loaded_scopes"]
    assert "database" in sess_data["loaded_scopes"]
