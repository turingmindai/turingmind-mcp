"""Phase 4.4 — PR / CI scope tests (TC-BR-28–31)."""

from __future__ import annotations

import pytest

from turingmind_mcp.memory_manager import MemoryManager
from turingmind_mcp.reconcile import RECURRENCE_THRESHOLD, ReconciliationEngine

CI_INGEST_KEY = "test-ingest-key"
VALID_HEAD = "a" * 40


def _ci_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("TURINGMIND_INGEST_KEY", CI_INGEST_KEY)
    return {"X-TuringMind-Ingest-Key": CI_INGEST_KEY}


def _valid_ci_payload(repo: str, **overrides) -> dict:
    payload = {
        "repo": repo,
        "branch": "feature/pr-42",
        "pr_number": 42,
        "head_sha": VALID_HEAD,
        "check_name": "unit-tests",
        "conclusion": "failure",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def ci_headers(monkeypatch):
    return _ci_headers(monkeypatch)


def test_ci_requires_branch(api_client, tier_repo, ci_headers):
    """TC-BR-28: CI ingest without branch → 400."""
    client, _db = api_client
    response = client.post(
        "/api/v2/observations/ci",
        headers=ci_headers,
        json={
            "repo": tier_repo,
            "pr_number": 42,
            "head_sha": VALID_HEAD,
            "conclusion": "failure",
        },
    )
    assert response.status_code == 400
    assert "branch" in response.json()["detail"].lower()


def test_ci_requires_head_sha(api_client, tier_repo, ci_headers):
    """TC-BR-28: CI ingest without head_sha → 400."""
    client, _db = api_client
    response = client.post(
        "/api/v2/observations/ci",
        headers=ci_headers,
        json={
            "repo": tier_repo,
            "branch": "feature/pr-42",
            "pr_number": 42,
            "conclusion": "failure",
        },
    )
    assert response.status_code == 400
    assert "head_sha" in response.json()["detail"].lower()


def test_ci_requires_pr_number(api_client, tier_repo, ci_headers):
    """TC-BR-28: CI ingest without pr_number → 400."""
    client, _db = api_client
    response = client.post(
        "/api/v2/observations/ci",
        headers=ci_headers,
        json={
            "repo": tier_repo,
            "branch": "feature/pr-42",
            "head_sha": VALID_HEAD,
            "conclusion": "failure",
        },
    )
    assert response.status_code == 400
    assert "pr_number" in response.json()["detail"].lower()


def test_ci_observation_branch_tagged(api_client, tier_repo, ci_headers):
    """TC-BR-29: valid CI payload tags branch + head, dirty=false."""
    client, db = api_client
    response = client.post(
        "/api/v2/observations/ci",
        headers=ci_headers,
        json=_valid_ci_payload(tier_repo),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["branch"] == "feature/pr-42"
    assert body["head_sha"] == VALID_HEAD
    assert body["pr_number"] == 42

    rows = db.list_observations(repo=tier_repo, event_type="ci_check", status="pending")
    assert len(rows) == 1
    row = rows[0]
    assert row["branch"] == "feature/pr-42"
    assert row["head_sha"] == VALID_HEAD
    assert row["git_dirty"] == 0


def test_auto_review_branch_recall(branch_recall_enabled, memory_db, tier_repo):
    """TC-BR-30: recall with branch excludes other-branch active memories."""
    memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="feature branch rule",
        scope="src/auth.py",
        status="active",
        branch="feature/pr-42",
        scope_tier="branch",
    )
    memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="main branch rule",
        scope="src/auth.py",
        status="active",
        branch="main",
        scope_tier="branch",
    )
    memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="repo wide rule",
        scope="repo",
        status="active",
        branch=None,
        scope_tier="repo",
    )

    mgr = MemoryManager(memory_db)
    results = mgr.get_relevant_memory(
        tier_repo,
        ["src/auth.py"],
        branch="feature/pr-42",
        head=VALID_HEAD,
        dirty=False,
    )
    contents = {entry["content"] for entry in results}
    assert "feature branch rule" in contents
    assert "repo wide rule" in contents
    assert "main branch rule" not in contents


CI_RECURRENCE_CONTENT = "CI unit-tests pr=42 branch=feature/pr conclusion=failure"


def test_ci_pass1_branch_isolation(memory_db, tier_repo):
    """TC-BR-31 / SPEC-BR-09: CI obs on feature branch not clustered with main edits."""
    engine = ReconciliationEngine(memory_db)
    for _ in range(RECURRENCE_THRESHOLD):
        memory_db.create_observation(
            repo=tier_repo,
            event_type="ci_check",
            content=CI_RECURRENCE_CONTENT,
            source="ci-webhook",
            branch="feature/pr",
            head_sha=VALID_HEAD,
            git_dirty=0,
        )
        memory_db.create_observation(
            repo=tier_repo,
            event_type="edit_cluster",
            content=CI_RECURRENCE_CONTENT,
            source="cursor-hook",
            branch="main",
        )

    stats = engine.mine_recurrence(tier_repo)
    assert stats["patterns_mined"] == 2

    candidates = memory_db.list_memory_entries(
        repo=tier_repo, memory_type="learned_pattern", status="candidate"
    )
    branches = {row["branch"] for row in candidates}
    assert branches == {"feature/pr", "main"}


def test_ci_not_in_active_recall(branch_recall_enabled, memory_db, tier_repo):
    """TC-BR-31a: pending CI obs never appear in active recall on main."""
    memory_db.create_observation(
        repo=tier_repo,
        event_type="ci_check",
        content="CI failure on feature branch",
        source="ci-webhook",
        branch="feature/pr",
        head_sha=VALID_HEAD,
        git_dirty=0,
    )
    memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="main memory",
        scope="repo",
        status="active",
        branch="main",
        scope_tier="branch",
    )

    mgr = MemoryManager(memory_db)
    results = mgr.get_relevant_memory(
        tier_repo,
        ["src/foo.py"],
        branch="main",
        dirty=False,
    )
    contents = {entry["content"] for entry in results}
    assert "main memory" in contents
    assert not any("CI failure" in content for content in contents)
