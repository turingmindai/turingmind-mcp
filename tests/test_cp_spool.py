"""TC-CP-05: spool replay idempotency for POST /api/v2/sync."""

from __future__ import annotations

import pytest


def test_tc_cp_05_sync_idempotency_returns_cached_bundle(api_client, tier_repo):
    """Duplicate event_id replays cached response without re-processing."""
    client, db = api_client
    db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="Idempotent sync rule.",
        scope="auth/login.py",
        branch="main",
        confidence=1.0,
    )

    payload = {
        "repo": tier_repo,
        "files": ["auth/login.py"],
        "composer_id": "composer-spool-idem-1",
        "event_id": "evt-spool-test-001",
    }

    first = client.post("/api/v2/sync", json=payload)
    assert first.status_code == 200
    first_data = first.json()
    assert "recall_bundle" in first_data
    assert first_data.get("idempotent_replay") is not True
    session_id = first_data["session"]["session_id"]

    second = client.post("/api/v2/sync", json=payload)
    assert second.status_code == 200
    second_data = second.json()
    assert second_data.get("idempotent_replay") is True
    assert second_data["session"]["session_id"] == session_id
    assert second_data["recall_bundle"] == first_data["recall_bundle"]

    cached = db.get_sync_idempotency("evt-spool-test-001")
    assert cached is not None
    assert cached["session"]["session_id"] == session_id


def test_tc_cp_05_spool_replay_sync_gets_bundle(api_client, tier_repo):
    """Simulate spool replay: same event_id after API recovery returns bundle."""
    client, db = api_client
    db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="Replay should still deliver patterns.",
        scope="api/handler.py",
        branch="main",
        confidence=0.9,
    )

    event_id = "evt-spool-replay-002"
    base = {
        "repo": tier_repo,
        "files": ["api/handler.py"],
        "composer_id": "composer-spool-replay",
        "event_id": event_id,
    }

    ok = client.post("/api/v2/sync", json=base)
    assert ok.status_code == 200
    assert ok.json()["recall_bundle"]["learned_patterns"]

    replay = client.post("/api/v2/sync", json=base)
    assert replay.status_code == 200
    body = replay.json()
    assert body.get("idempotent_replay") is True
    assert body["recall_bundle"]["learned_patterns"][0]["content"] == (
        "Replay should still deliver patterns."
    )


def test_tc_cp_05_different_event_ids_run_separate_syncs(api_client, tier_repo):
    """Distinct event_ids are not deduplicated."""
    client, _db = api_client
    common = {
        "repo": tier_repo,
        "files": ["lib/util.py"],
        "composer_id": "composer-spool-distinct",
    }

    r1 = client.post("/api/v2/sync", json={**common, "event_id": "evt-a"})
    r2 = client.post("/api/v2/sync", json={**common, "event_id": "evt-b"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json().get("idempotent_replay") is not True
    assert r2.json().get("idempotent_replay") is not True
