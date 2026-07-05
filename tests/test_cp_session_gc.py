from __future__ import annotations

import datetime
import pytest
import uuid
from turingmind_mcp.api_server import run_session_gc

def test_cp_session_heartbeat_extends_ttl(api_client, tier_repo):
    """TC-CP-25: Heartbeat endpoint updates expires_at timestamp extending TTL."""
    client, db = api_client
    composer_id = f"composer-{uuid.uuid4()}"

    # 1. Sync first time to create session
    p1 = {
        "repo": tier_repo,
        "files": ["src/reconcile.py"],
        "composer_id": composer_id,
    }
    r1 = client.post("/api/v2/sync", json=p1)
    assert r1.status_code == 200
    session_id = r1.json()["session"]["session_id"]

    # 2. Call heartbeat
    r2 = client.post(f"/api/v2/session/{session_id}/heartbeat")
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["status"] == "ok"
    assert "expires_at" in d2

    # Verify expires_at is roughly 4 hours in the future
    expires_dt = datetime.datetime.fromisoformat(d2["expires_at"])
    now_dt = datetime.datetime.utcnow()
    diff = expires_dt - now_dt
    assert 3.9 <= (diff.total_seconds() / 3600) <= 4.1


def test_cp_session_gc_cleanup(api_client, tier_repo):
    """TC-CP-24: Session GC deletes expired sessions and logs a distilled summary observation."""
    client, db = api_client
    composer_id = f"composer-{uuid.uuid4()}"

    # 1. Create a session
    session_id = str(uuid.uuid4())
    # Set expires_at to 10 minutes ago (expired)
    expired_at = (datetime.datetime.utcnow() - datetime.timedelta(minutes=10)).isoformat()
    db.create_coding_session(
        session_id=session_id,
        composer_id=composer_id,
        repo=tier_repo,
        branch="main",
        expires_at=expired_at,
    )

    # 2. Update session with some loaded scopes, touched files, and recalled memories
    db.update_coding_session(
        session_id=session_id,
        loaded_scopes=["reconcile", "database"],
        touched_files=["src/reconcile.py", "src/database.py"],
        touched_subsystems=["reconcile", "database"],
        recall_history=["mem-001", "mem-002"],
        expires_at=expired_at,
    )

    # Verify session is in db
    sess = db.get_coding_session_by_id(session_id)
    assert sess is not None

    # 3. Trigger GC cycle
    run_session_gc(db)

    # 4. Verify session is deleted
    assert db.get_coding_session_by_id(session_id) is None

    # 5. Verify distilled summary observation is written to db
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT * FROM observations WHERE repo = ? AND event_type = 'session_context' ORDER BY created_at DESC LIMIT 1",
        (tier_repo,),
    )
    row = cursor.fetchone()
    assert row is not None
    obs = dict(row)
    content = obs["content"]
    assert composer_id in content
    assert "reconcile" in content
    assert "database" in content
    assert "mem-001" in content
    assert "mem-002" in content
