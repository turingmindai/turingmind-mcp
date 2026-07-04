"""Tier C — reconcile extensions via API (TC-C06).

Engine-level tests for TC-C01–C05, C07–C08 live in test_reconcile.py and related modules.
"""

from __future__ import annotations

import pytest

from turingmind_mcp.reconcile import RECURRENCE_THRESHOLD


@pytest.mark.tier_c
def test_tc_c06_reconcile_api_decision_queue(api_client, tier_repo):
    """TC-C06: Reconcile via API surfaces promotion_candidate on decision queue."""
    client, db = api_client
    content = "targeted_fix/high: repeated edit in src/payments/handler.py"
    for _ in range(RECURRENCE_THRESHOLD):
        db.create_observation(
            repo=tier_repo,
            event_type="edit_cluster",
            content=content,
            source="cursor-hook",
        )

    reconcile = client.post("/api/v2/reconcile", json={"repo": tier_repo})
    assert reconcile.status_code == 200
    stats = reconcile.json()
    assert stats["status"] == "reconciled"
    assert stats.get("patterns_mined", 0) >= 1

    active = db.list_memory_entries(
        repo=tier_repo, memory_type="learned_pattern", status="active"
    )
    assert active == []

    queue = client.get("/api/v2/decision-queue", params={"repo": tier_repo, "limit": 50})
    assert queue.status_code == 200
    items = queue.json()["queue"]
    promotion = [
        item for item in items if item.get("gap_type") == "promotion_candidate"
    ]
    assert promotion, "Expected promotion_candidate on decision queue"
    assert promotion[0].get("finding_id")

    resolve = client.post(
        f"/api/v2/reconcile/findings/{promotion[0]['finding_id']}/resolve",
        json={"status": "dismissed"},
    )
    assert resolve.status_code == 200
