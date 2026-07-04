"""Tier D — durability API scenarios (TC-D08 live Postgres).

Most Tier D cases are covered in test_postgres_memory_sync.py, test_bidirectional_sync.py,
test_memory_manager.py, test_memory_embeddings.py, and test_memory_vec_index.py.
"""

from __future__ import annotations

import os

import pytest

from turingmind_mcp.v2_engine import postgres


@pytest.mark.tier_d
def test_tc_d05_relevant_memory_api_excludes_session_context(api_client, tier_repo):
    """TC-D05: GET /memory/relevant excludes session_context by default."""
    client, db = api_client
    db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="Use bcrypt for password hashing",
        scope="src/auth.py",
        confidence=0.9,
    )
    db.create_memory_entry(
        repo=tier_repo,
        memory_type="session_context",
        content="Currently editing auth flow",
        scope="src/auth.py",
        confidence=0.5,
    )

    response = client.get(
        "/api/v2/memory/relevant",
        params={"repo": tier_repo, "files": "src/auth.py"},
    )
    assert response.status_code == 200
    entries = response.json()["entries"]
    types = {e["type"] for e in entries}
    assert "learned_pattern" in types
    assert "session_context" not in types


@pytest.mark.tier_d
@pytest.mark.postgres_live
@pytest.mark.skipif(not os.getenv("POSTGRES_URI"), reason="Set POSTGRES_URI for live Postgres tests")
def test_tc_d08_live_postgres_round_trip(memory_db, tier_repo):
    """TC-D08: Live Postgres upsert, pull, and schema initialization."""
    postgres.init_postgres()
    memory_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="Never commit secrets to the repository",
        scope="repo",
        confidence=0.92,
    )

    pushed = postgres.sync_memory_entries(
        tier_repo,
        memory_db.list_memory_entries_for_cloud_sync(repo=tier_repo),
    )
    assert pushed >= 1

    pulled = postgres.pull_memory_entries(tier_repo)
    ids = {row["memory_id"] for row in pulled}
    assert memory_id in ids

    match = next(row for row in pulled if row["memory_id"] == memory_id)
    assert match["content"] == "Never commit secrets to the repository"
    assert match["status"] == "active"
