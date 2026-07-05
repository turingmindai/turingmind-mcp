from __future__ import annotations

import pytest

def test_cp_ranker_token_budget_and_sorting(api_client, tier_repo):
    """TC-CP-06: Verify token budget limits (max 8 items) and relevance score sorting for recall bundles."""
    client, db = api_client

    # 1. Seed 10 explicit rules with different scopes/confidences
    for i in range(10):
        # We vary confidence to result in different scores
        confidence = 0.5 + (i * 0.05)  # 0.5 to 0.95
        db.create_memory_entry(
            repo=tier_repo,
            memory_type="explicit_rule",
            content=f"Rule number {i}",
            scope="database/postgres.py" if i % 2 == 0 else "app.py",
            branch="main",
            confidence=confidence,
        )

    # 2. Seed 10 learned patterns
    for i in range(10):
        confidence = 0.4 + (i * 0.05)  # 0.4 to 0.85
        db.create_memory_entry(
            repo=tier_repo,
            memory_type="learned_pattern",
            content=f"Pattern number {i}",
            scope="database/postgres.py" if i % 2 == 0 else "app.py",
            branch="main",
            confidence=confidence,
        )

    # 3. Perform sync POST
    payload = {
        "repo": tier_repo,
        "files": ["database/postgres.py"],
        "composer_id": "composer-budget-test-789",
        "branch": "main"
    }
    response = client.post("/api/v2/sync", json=payload)
    assert response.status_code == 200

    data = response.json()
    bundle = data["recall_bundle"]

    # 4. Assert token budgets (max 8) are strictly honored
    assert len(bundle["explicit_rules"]) <= 8
    assert len(bundle["learned_patterns"]) <= 8

    # 5. Assert score sorting is strictly descending
    rules = bundle["explicit_rules"]
    for idx in range(len(rules) - 1):
        assert rules[idx]["score"] >= rules[idx + 1]["score"]

    patterns = bundle["learned_patterns"]
    for idx in range(len(patterns) - 1):
        assert patterns[idx]["score"] >= patterns[idx + 1]["score"]
