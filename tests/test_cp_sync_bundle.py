from __future__ import annotations

import pytest
from turingmind_mcp.recall_bundle import RecallBundle

def test_cp_sync_bundle_returns_valid_schema(api_client, tier_repo):
    """TC-CP-01: Sync response returns a valid recall_bundle matching JSON schema."""
    client, db = api_client

    # 1. Seed some explicit rules and patterns in the test DB
    db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="Always use secure connection pools in production.",
        scope="database/postgres.py",
        branch="main",
        confidence=1.0,
    )
    db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="Frequent connection timeout errors detected on postgres module.",
        scope="database/postgres.py",
        branch="main",
        confidence=0.8,
    )

    # 2. Perform POST to sync endpoint
    payload = {
        "repo": tier_repo,
        "files": ["database/postgres.py", "app.py"],
        "composer_id": "composer-test-session-123",
        "branch": "main",
        "head_sha": "a1b2c3d4"
    }
    response = client.post("/api/v2/sync", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert "recall_bundle" in data
    assert "bundle_delta" in data
    assert "session" in data

    # Verify matching bundle fields
    bundle = data["recall_bundle"]
    assert len(bundle["explicit_rules"]) == 1
    assert bundle["explicit_rules"][0]["content"] == "Always use secure connection pools in production."
    assert len(bundle["learned_patterns"]) == 1
    assert bundle["learned_patterns"][0]["content"] == "Frequent connection timeout errors detected on postgres module."

    assert data["delivery"]["is_delta"] is True
    assert data["delivery"]["token_budget_used"] == 2

    # Validate against RecallBundle pydantic model schema
    RecallBundle(**bundle)


def test_cp_sync_bundle_fail_soft_on_malformed_data(api_client, tier_repo):
    """TC-CP-02: Schema validation surfaces TM-SCHEMA-ERR without crashing sync."""
    client, db = api_client

    bad_content = "X" * 2500
    db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content=bad_content,
        scope="database/postgres.py",
        branch="main",
        confidence=1.0,
    )

    payload = {
        "repo": tier_repo,
        "files": ["database/postgres.py"],
        "composer_id": "composer-bad-data-123",
    }

    response = client.post("/api/v2/sync", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "synced"
    assert len(data["recall_bundle"]["explicit_rules"]) == 0
    policy = data["recall_bundle"]["policy"]
    assert policy["code"] == "TM-SCHEMA-ERR"
    assert policy["hydrate_required"] is True
    assert data["bundle_delta"]["added_rule_ids"] == []
    assert data["bundle_delta"]["unchanged"] is True
    assert data["delivery"]["is_delta"] is False

    sess = db.get_coding_session("composer-bad-data-123", tier_repo)
    assert sess is not None
    assert sess["recall_history"] == []
