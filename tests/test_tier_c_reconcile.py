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


@pytest.mark.tier_c
@pytest.mark.asyncio
async def test_tc_c06_reconcile_mcp_decision_queue(tier_repo):
    """Verify that turingmind_get_decision_queue MCP handler merges reconcile findings."""
    import json
    import logging
    from unittest import mock
    from turingmind_mcp.v2_engine.handlers import handle_get_decision_queue
    from turingmind_mcp.tools.context import ToolContext
    from turingmind_mcp.database import MemoryDatabase

    db = MemoryDatabase()
    # clean before
    cursor = db.conn.cursor()
    cursor.execute("DELETE FROM reconcile_findings WHERE repo = ?", (tier_repo,))
    db.conn.commit()

    # Create a seeded finding in MemoryDatabase
    finding_id = db.create_finding(
        repo=tier_repo,
        finding_type="promotion_candidate",
        severity="medium",
        action="Approve or dismiss promotion of memory...",
        dedup_key="test_finding_mcp_dedup_123",
        evidence=[{"evidence_ref": "some_ref"}],
    )
    assert finding_id is not None


    ctx = ToolContext(
        client=mock.MagicMock(),
        api_url="",
        headers={},
        logger=logging.getLogger("test"),
        save_api_key=lambda url, key: "",
        version="1.0",
    )

    result = await handle_get_decision_queue({"repo": tier_repo}, ctx)
    assert len(result) == 1
    content = json.loads(result[0].text)
    assert content["status"] == "success"
    
    dq = content["decision_queue"]
    found = [item for item in dq if item.get("finding_id") == finding_id]
    assert found, f"Expected seeded finding to be merged, but decision queue was: {dq}"
    assert found[0]["gap_type"] == "promotion_candidate"
    assert found[0]["severity"] == "medium"


