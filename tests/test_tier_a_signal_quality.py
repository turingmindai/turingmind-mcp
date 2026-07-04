"""Tier A — signal quality (TC-A01 … TC-A04).

Formal descriptions live in tests/tier_test_catalog.yaml and docs/memory-tier-test-plan.html.
"""

from __future__ import annotations

import pytest

from turingmind_mcp.reconcile import RECURRENCE_THRESHOLD


@pytest.mark.tier_a
def test_tc_a01_api_health(api_client):
    """TC-A01: API health responds OK."""
    client, _db = api_client
    response = client.get("/api/v2/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.tier_a
def test_tc_a02_memory_save_and_list(api_client, tier_repo):
    """TC-A02: REST memory save and list round-trip with node_id."""
    client, db = api_client
    payload = {
        "repo": tier_repo,
        "type": "explicit_rule",
        "content": "Always validate JWT on protected routes",
        "scope": "repo",
        "confidence": 0.95,
        "node_id": "node-auth-jwt",
    }
    save = client.post("/api/v2/memory", json=payload)
    assert save.status_code == 200
    body = save.json()
    assert body["status"] == "saved"
    memory_id = body["memory_id"]

    listed = client.get(
        "/api/v2/memory",
        params={"repo": tier_repo, "type": "explicit_rule", "status": "active"},
    )
    assert listed.status_code == 200
    entries = listed.json()["entries"]
    match = next(e for e in entries if e["memory_id"] == memory_id)
    assert match["node_id"] == "node-auth-jwt"
    assert match["content"] == payload["content"]

    stored = db.get_memory_entry(memory_id)
    assert stored is not None
    assert stored["node_id"] == "node-auth-jwt"


@pytest.mark.tier_a
def test_tc_a04_observations_not_in_recall(api_client, tier_repo):
    """TC-A04: Observations stay out of active memory recall until reconcile promotes."""
    client, _db = api_client
    token = "unique-draft-token-tier-a04"
    obs = client.post(
        "/api/v2/observations",
        json={
            "repo": tier_repo,
            "observations": [
                {
                    "event_type": "edit_cluster",
                    "content": f"targeted_fix/high: changed src/auth.py {token}",
                    "source": "cursor-hook",
                    "confidence": 0.3,
                }
            ],
        },
    )
    assert obs.status_code == 200

    memory = client.get(
        "/api/v2/memory",
        params={"repo": tier_repo, "search": token, "status": "active"},
    )
    assert memory.status_code == 200
    assert memory.json()["entries"] == []

    pending = client.get(
        "/api/v2/observations",
        params={"repo": tier_repo, "status": "pending", "event_type": "edit_cluster"},
    )
    assert pending.status_code == 200
    assert any(token in row["content"] for row in pending.json()["observations"])


@pytest.mark.tier_a
def test_tc_a04_reconcile_still_requires_consent_for_activation(api_client, tier_repo):
    """TC-A04 extension: even after reconcile, recurrence yields candidate not active."""
    client, db = api_client
    content = "targeted_fix/high: 1 code file changed in src/auth/jwt_middleware.py"
    for _ in range(RECURRENCE_THRESHOLD):
        db.create_observation(
            repo=tier_repo,
            event_type="edit_cluster",
            content=content,
            source="cursor-hook",
        )

    result = client.post("/api/v2/reconcile", json={"repo": tier_repo})
    assert result.status_code == 200

    active = db.list_memory_entries(
        repo=tier_repo, memory_type="learned_pattern", status="active"
    )
    candidates = db.list_memory_entries(
        repo=tier_repo, memory_type="learned_pattern", status="candidate"
    )
    assert active == []
    assert len(candidates) == 1
