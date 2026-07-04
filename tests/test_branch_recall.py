"""Phase 4.2 branch-aware recall (TC-BR-11 … TC-BR-19, TC-BR-14a)."""

from __future__ import annotations

import pytest


def _save_rule(
    db,
    repo,
    content,
    *,
    branch=None,
    head_sha=None,
    git_dirty=0,
    scope_tier="repo",
    scope="repo",
):
    return db.create_memory_entry(
        repo=repo,
        memory_type="explicit_rule",
        content=content,
        scope=scope,
        confidence=0.9,
        status="active",
        branch=branch,
        head_sha=head_sha,
        git_dirty=git_dirty,
        scope_tier=scope_tier,
    )


@pytest.mark.branch
def test_tc_br_11_l4_visible_everywhere(branch_recall_enabled, memory_db, tier_repo):
    """TC-BR-11: branch=NULL memories visible on any branch recall."""
    _save_rule(memory_db, tier_repo, "always applies", branch=None, scope_tier="repo")
    _save_rule(
        memory_db,
        tier_repo,
        "feature only",
        branch="feature/x",
        scope_tier="branch",
    )

    from turingmind_mcp.memory_manager import MemoryManager

    mgr = MemoryManager(memory_db)
    results = mgr.get_relevant_memory(
        tier_repo,
        [],
        branch="feature/x",
        head="a" * 40,
    )
    contents = {r["content"] for r in results}
    assert "always applies" in contents
    assert "feature only" in contents


@pytest.mark.branch
def test_tc_br_12_branch_match_ranks_higher(branch_recall_enabled, memory_db, tier_repo):
    """TC-BR-12: matching branch ranks above other-branch memory."""
    _save_rule(memory_db, tier_repo, "on feature a", branch="feature/a", scope_tier="branch")
    _save_rule(memory_db, tier_repo, "on feature b", branch="feature/b", scope_tier="branch")

    from turingmind_mcp.memory_manager import MemoryManager

    mgr = MemoryManager(memory_db)
    results = mgr.get_relevant_memory(
        tier_repo,
        [],
        branch="feature/a",
        head="b" * 40,
    )
    assert results[0]["content"] == "on feature a"


@pytest.mark.branch
def test_tc_br_13_other_branch_deprioritized(branch_recall_enabled, memory_db, tier_repo):
    """TC-BR-13: other-branch memories excluded by default on main."""
    _save_rule(memory_db, tier_repo, "stale feature", branch="feature/old", scope_tier="branch")

    from turingmind_mcp.memory_manager import MemoryManager

    mgr = MemoryManager(memory_db)
    results = mgr.get_relevant_memory(
        tier_repo,
        [],
        branch="main",
        head="c" * 40,
    )
    assert all(r.get("branch") in (None, "main") for r in results)


@pytest.mark.branch
def test_tc_br_14_working_tree_boost(branch_recall_enabled, memory_db, tier_repo):
    """TC-BR-14: L2 dirty+head match ranks above L3 same branch."""
    head = "d" * 40
    _save_rule(
        memory_db,
        tier_repo,
        "committed on branch",
        branch="feature/x",
        head_sha=head,
        git_dirty=0,
        scope_tier="branch",
    )
    _save_rule(
        memory_db,
        tier_repo,
        "dirty working tree",
        branch="feature/x",
        head_sha=head,
        git_dirty=1,
        scope_tier="working_tree",
    )

    from turingmind_mcp.memory_manager import MemoryManager

    mgr = MemoryManager(memory_db)
    results = mgr.get_relevant_memory(
        tier_repo,
        [],
        branch="feature/x",
        head=head,
        dirty=True,
    )
    assert results[0]["content"] == "dirty working tree"


@pytest.mark.branch
def test_tc_br_14a_detached_recall_by_head_sha(branch_recall_enabled, memory_db, tier_repo):
    """TC-BR-14a: detached checkout ranks by head_sha, not branch name."""
    head = "e" * 40
    _save_rule(
        memory_db,
        tier_repo,
        "at commit",
        branch="feature/x",
        head_sha=head,
        scope_tier="branch",
    )
    _save_rule(
        memory_db,
        tier_repo,
        "other commit",
        branch="feature/x",
        head_sha="f" * 40,
        scope_tier="branch",
    )

    from turingmind_mcp.memory_manager import MemoryManager

    mgr = MemoryManager(memory_db)
    results = mgr.get_relevant_memory(
        tier_repo,
        [],
        branch="HEAD",
        head=head,
        dirty=False,
    )
    assert len(results) == 1
    assert results[0]["content"] == "at commit"


@pytest.mark.branch
def test_tc_br_15_relevant_api_branch_params(branch_recall_enabled, api_client, tier_repo):
    """TC-BR-15: GET /memory/relevant accepts branch params."""
    client, db = api_client
    _save_rule(db, tier_repo, "branch rule", branch="feature/api", scope_tier="branch")
    response = client.get(
        "/api/v2/memory/relevant",
        params={
            "repo": tier_repo,
            "branch": "feature/api",
            "head": "a" * 40,
        },
    )
    assert response.status_code == 200
    assert any(e["content"] == "branch rule" for e in response.json()["entries"])


@pytest.mark.branch
def test_tc_br_16_mcp_list_branch_filter(branch_recall_enabled, api_client, tier_repo):
    """TC-BR-16: list memory with branch returns branch + repo-wide only."""
    client, db = api_client
    _save_rule(db, tier_repo, "repo wide", branch=None)
    _save_rule(db, tier_repo, "on a", branch="feature/a", scope_tier="branch")
    _save_rule(db, tier_repo, "on b", branch="feature/b", scope_tier="branch")

    response = client.get(
        "/api/v2/memory",
        params={"repo": tier_repo, "branch": "feature/a", "limit": 50},
    )
    assert response.status_code == 200
    contents = {e["content"] for e in response.json()["entries"]}
    assert "repo wide" in contents
    assert "on a" in contents
    assert "on b" not in contents


@pytest.mark.branch
def test_tc_br_17_observations_not_in_recall(branch_recall_enabled, api_client, tier_repo):
    """TC-BR-17: branch-tagged pending observations stay out of recall."""
    client, db = api_client
    db.create_observation(
        repo=tier_repo,
        event_type="edit_cluster",
        content="pending branch obs",
        branch="feature/x",
        head_sha="a" * 40,
    )
    response = client.get(
        "/api/v2/memory/relevant",
        params={"repo": tier_repo, "branch": "feature/x", "head": "a" * 40},
    )
    assert response.status_code == 200
    assert response.json()["entries"] == []


@pytest.mark.branch
def test_tc_br_18_session_context_working_tree_option_b(
    branch_recall_enabled, api_client, tier_repo, sample_git_payload
):
    """TC-BR-18 / SPEC-BR-01 Option B: session_context only with include_session_context."""
    client, db = api_client
    dirty = {**sample_git_payload, "dirty": True}
    save = client.post(
        "/api/v2/memory",
        json={
            "repo": tier_repo,
            "type": "session_context",
            "content": "ephemeral cluster",
            "scope": "repo",
            "git": dirty,
        },
    )
    assert save.status_code == 200

    default = client.get(
        "/api/v2/memory/relevant",
        params={
            "repo": tier_repo,
            "branch": dirty["branch"],
            "head": dirty["head"],
            "dirty": "true",
        },
    )
    assert default.json()["entries"] == []

    included = client.get(
        "/api/v2/memory/relevant",
        params={
            "repo": tier_repo,
            "branch": dirty["branch"],
            "head": dirty["head"],
            "dirty": "true",
            "include_session_context": "true",
        },
    )
    assert len(included.json()["entries"]) == 1


@pytest.mark.branch
def test_tc_br_19_include_other_branches(branch_recall_enabled, memory_db, tier_repo):
    """TC-BR-19: include_other_branches returns deprioritized rows."""
    _save_rule(memory_db, tier_repo, "other", branch="feature/other", scope_tier="branch")

    from turingmind_mcp.memory_manager import MemoryManager

    mgr = MemoryManager(memory_db)
    excluded = mgr.get_relevant_memory(
        tier_repo, [], branch="main", head="a" * 40, include_other_branches=False
    )
    assert not any(r["content"] == "other" for r in excluded)

    included = mgr.get_relevant_memory(
        tier_repo, [], branch="main", head="a" * 40, include_other_branches=True
    )
    assert any(r["content"] == "other" for r in included)


@pytest.mark.branch
def test_tc_br_s07_branch_memory_not_truncated(branch_recall_enabled, memory_db, tier_repo):
    """TC-BR-S07 / SPEC-BR-04: feature memory survives many main-branch rows."""
    for i in range(60):
        _save_rule(
            memory_db,
            tier_repo,
            f"main rule {i}",
            branch="main",
            scope_tier="branch",
        )
    target_id = _save_rule(
        memory_db,
        tier_repo,
        "needle feature memory",
        branch="feature/x",
        scope_tier="branch",
    )

    from turingmind_mcp.memory_manager import MemoryManager

    mgr = MemoryManager(memory_db)
    results = mgr.get_relevant_memory(
        tier_repo,
        [],
        branch="feature/x",
        head="a" * 40,
        limit=50,
    )
    assert any(r["memory_id"] == target_id for r in results)
