from __future__ import annotations

import pytest

def test_cp_delta_hydration_unchanged(api_client, tier_repo):
    """TC-CP-04: Sync delta returns unchanged=true when rules have already been delivered in session."""
    client, db = api_client
    session_id = "session-delta-test-456"

    # 1. Seed memory rule
    db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="Always sanitize SQL inputs.",
        scope="db.py",
        branch="main",
        confidence=1.0,
    )

    # 2. First sync - Should deliver rule (unchanged = False)
    payload1 = {
        "repo": tier_repo,
        "files": ["db.py"],
        "session_id": session_id,
        "branch": "main"
    }
    response1 = client.post("/api/v2/sync", json=payload1)
    assert response1.status_code == 200
    data1 = response1.json()
    assert data1["bundle_delta"]["unchanged"] is False
    assert len(data1["bundle_delta"]["added_rule_ids"]) == 1
    assert data1["delivery"]["is_delta"] is True
    assert len(data1["recall_bundle"]["explicit_rules"]) == 1

    # 3. Second sync - Same session and files, should return unchanged = True
    response2 = client.post("/api/v2/sync", json=payload1)
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["bundle_delta"]["unchanged"] is True
    assert len(data2["bundle_delta"]["added_rule_ids"]) == 0
    assert data2["delivery"]["is_delta"] is False
    assert len(data2["recall_bundle"]["explicit_rules"]) == 0
    assert len(data2["recall_bundle"]["learned_patterns"]) == 0

    # 4. Third sync - Different session, should deliver rule again (unchanged = False)
    payload3 = {
        "repo": tier_repo,
        "files": ["db.py"],
        "session_id": "session-different-789",
        "branch": "main"
    }
    response3 = client.post("/api/v2/sync", json=payload3)
    assert response3.status_code == 200
    data3 = response3.json()
    assert data3["bundle_delta"]["unchanged"] is False
    assert len(data3["bundle_delta"]["added_rule_ids"]) == 1
    assert data3["delivery"]["is_delta"] is True
    assert len(data3["recall_bundle"]["explicit_rules"]) == 1
