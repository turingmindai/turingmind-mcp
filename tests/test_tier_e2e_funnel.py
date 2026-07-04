"""End-to-end memory funnel tests (TC-E2E01 … TC-E2E03)."""

from __future__ import annotations

from unittest import mock

import pytest

from turingmind_mcp.reconcile import RECURRENCE_THRESHOLD


@pytest.mark.tier_e2e
def test_tc_e2e01_capture_reconcile_queue(api_client, tier_repo):
    """TC-E2E01: Observations → reconcile → queue without auto-activation."""
    client, db = api_client
    content = "targeted_fix/high: auth middleware pattern in src/auth/jwt.py"
    ingest = client.post(
        "/api/v2/observations",
        json={
            "repo": tier_repo,
            "observations": [
                {
                    "event_type": "edit_cluster",
                    "content": content,
                    "source": "cursor-hook",
                }
                for _ in range(RECURRENCE_THRESHOLD)
            ],
        },
    )
    assert ingest.status_code == 200

    reconcile = client.post("/api/v2/reconcile", json={"repo": tier_repo})
    assert reconcile.status_code == 200

    active = db.list_memory_entries(
        repo=tier_repo, memory_type="learned_pattern", status="active"
    )
    assert active == []

    queue = client.get("/api/v2/decision-queue", params={"repo": tier_repo})
    assert any(
        item.get("gap_type") == "promotion_candidate" for item in queue.json()["queue"]
    )


@pytest.mark.tier_e2e
def test_tc_e2e02_agent_promotion_after_consent(api_client, tier_repo):
    """TC-E2E02: Agent promotes candidate to active after explicit consent."""
    client, db = api_client
    content = "targeted_fix/high: recurring change in src/billing/invoice.py"
    for _ in range(RECURRENCE_THRESHOLD):
        db.create_observation(
            repo=tier_repo,
            event_type="edit_cluster",
            content=content,
            source="cursor-hook",
        )
    client.post("/api/v2/reconcile", json={"repo": tier_repo})

    candidates = db.list_memory_entries(
        repo=tier_repo, memory_type="learned_pattern", status="candidate"
    )
    assert len(candidates) == 1
    memory_id = candidates[0]["memory_id"]

    findings = db.list_findings(repo=tier_repo, status="pending")
    promotion = next(f for f in findings if f["finding_type"] == "promotion_candidate")

    db.update_memory_entry(memory_id, status="active")
    client.post(
        f"/api/v2/reconcile/findings/{promotion['finding_id']}/resolve",
        json={"status": "actioned"},
    )

    active = db.get_memory_entry(memory_id)
    assert active["status"] == "active"

    recall = client.get(
        "/api/v2/memory",
        params={"repo": tier_repo, "type": "learned_pattern", "status": "active"},
    )
    assert any(e["memory_id"] == memory_id for e in recall.json()["entries"])

