"""Tier D — durability API scenarios.

Most Tier D cases are covered in test_bidirectional_sync.py,
test_memory_manager.py, test_memory_embeddings.py, and test_memory_vec_index.py.
"""

from __future__ import annotations

import os

import pytest


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
