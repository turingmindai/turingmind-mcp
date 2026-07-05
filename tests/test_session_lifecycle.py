from __future__ import annotations

import datetime
import uuid

from turingmind_mcp.api_server import run_session_gc
from turingmind_mcp.session_lifecycle import (
    SESSION_TTL_HOURS,
    end_session_by_composer,
    end_session_by_id,
    session_expires_at,
)


def test_session_ttl_hours_default():
    assert SESSION_TTL_HOURS == 4.0


def test_session_expires_at_four_hours_ahead():
    now = datetime.datetime(2026, 1, 1, 12, 0, 0)
    expires = session_expires_at(now=now)
    expires_dt = datetime.datetime.fromisoformat(expires)
    assert expires_dt - now == datetime.timedelta(hours=4)


def test_session_end_by_id(api_client, tier_repo):
    client, db = api_client
    session_id = str(uuid.uuid4())
    composer_id = f"composer-end-{uuid.uuid4()}"
    db.create_coding_session(
        session_id=session_id,
        composer_id=composer_id,
        repo=tier_repo,
        branch="main",
        expires_at=session_expires_at(),
    )

    result = end_session_by_id(db, session_id, reason="session_end")
    assert result["status"] == "ended"
    assert db.get_coding_session_by_id(session_id) is None

    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT content FROM observations WHERE repo = ? AND event_type = 'session_context' ORDER BY created_at DESC LIMIT 1",
        (tier_repo,),
    )
    row = cursor.fetchone()
    assert row is not None
    assert composer_id in row["content"]
    assert "session_end" in row["content"]


def test_session_end_by_composer(api_client, tier_repo):
    _, db = api_client
    session_id = str(uuid.uuid4())
    composer_id = f"composer-end2-{uuid.uuid4()}"
    db.create_coding_session(
        session_id=session_id,
        composer_id=composer_id,
        repo=tier_repo,
    )

    result = end_session_by_composer(db, composer_id, tier_repo)
    assert result["status"] == "ended"
    assert db.get_coding_session(composer_id, tier_repo) is None


def test_session_end_rest(api_client, tier_repo):
    client, db = api_client
    session_id = str(uuid.uuid4())
    composer_id = f"composer-rest-{uuid.uuid4()}"
    db.create_coding_session(
        session_id=session_id,
        composer_id=composer_id,
        repo=tier_repo,
    )

    response = client.post(f"/api/v2/session/{session_id}/end", json={"reason": "session_end"})
    assert response.status_code == 200
    assert response.json()["status"] == "ended"


def test_run_session_gc_returns_stats(api_client, tier_repo):
    _, db = api_client
    session_id = str(uuid.uuid4())
    expired_at = (datetime.datetime.utcnow() - datetime.timedelta(minutes=10)).isoformat()
    db.create_coding_session(
        session_id=session_id,
        composer_id=f"composer-gc-{uuid.uuid4()}",
        repo=tier_repo,
        expires_at=expired_at,
    )

    stats = run_session_gc(db)
    assert stats["archived"] >= 1
    assert db.get_coding_session_by_id(session_id) is None
