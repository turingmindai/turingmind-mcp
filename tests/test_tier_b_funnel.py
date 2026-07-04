"""Tier B — fill the funnel (TC-B05+ API scenarios).

Helper-level tests for TC-B01–B04 and B06 live in test_observation_capture.py.
"""

from __future__ import annotations

import pytest

from turingmind_mcp.observation_capture import (
    EVENT_CHAT_EXCHANGE,
    EVENT_GIT_REVERT,
    EVENT_PRE_PUSH_HIGH,
    EVENT_VERIFICATION_SUCCESS,
)


@pytest.mark.tier_b
def test_tc_b05_observations_api_batch(api_client, tier_repo):
    """TC-B05: Batch observation ingest via REST stays pending until reconcile."""
    client, db = api_client
    batch = {
        "repo": tier_repo,
        "observations": [
            {
                "event_type": EVENT_CHAT_EXCHANGE,
                "content": "user: fix auth\nassistant: updated login.py",
                "source": "chat-poller",
                "confidence": 0.3,
            },
            {
                "event_type": EVENT_GIT_REVERT,
                "content": 'Revert "bad change" — files: src/a.py',
                "source": "antigravity-hook",
                "confidence": 0.5,
            },
            {
                "event_type": EVENT_VERIFICATION_SUCCESS,
                "content": "Node Auth fix verified: 3 passed",
                "source": "run_verification",
                "confidence": 0.8,
                "node_id": "node-1",
            },
            {
                "event_type": EVENT_PRE_PUSH_HIGH,
                "content": "HIGH gap: missing tests on auth module",
                "source": "pre-push-hook",
                "confidence": 0.4,
            },
        ],
    }
    response = client.post("/api/v2/observations", json=batch)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "recorded"
    assert len(body["observation_ids"]) == 4

    listed = client.get(
        "/api/v2/observations",
        params={"repo": tier_repo, "status": "pending"},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 4

    memories = db.list_memory_entries(repo=tier_repo)
    assert memories == []
