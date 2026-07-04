"""Phase 4.5 — Cloud sync branch filter + tombstones (sidecar API / Mongo backend)."""

from __future__ import annotations

from unittest import mock

import pytest

from turingmind_mcp.cloud_memory_client import _serialize_push_entries, resolve_sync_branch
from turingmind_mcp.database import MemoryDatabase


@pytest.fixture
def memory_db(tmp_path):
    db = MemoryDatabase(str(tmp_path / "memory.db"))
    yield db
    db.close()


@pytest.fixture
def tier_repo() -> str:
    return "test-org/sync-sandbox"


def test_push_includes_branch(memory_db, tier_repo):
    """TC-BR-33: cloud push payload includes branch git columns."""
    memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="branch scoped rule",
        scope="src/auth.py",
        status="active",
        branch="feature/sync",
        head_sha="a" * 40,
        git_dirty=0,
        scope_tier="branch",
    )
    rows = _serialize_push_entries(memory_db, tier_repo)
    assert len(rows) == 1
    row = rows[0]
    assert row["branch"] == "feature/sync"
    assert row["head_sha"] == "a" * 40
    assert row["git_dirty"] == 0
    assert row["scope_tier"] == "branch"




def test_pull_branch_filter_excludes_other_branch_active(memory_db, tier_repo):
    """TC-BR-34: apply_cloud only merges rows returned by filtered pull (simulated)."""
    memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="main only",
        scope="repo",
        status="active",
        branch="main",
        scope_tier="branch",
    )
    pulled = [
        {
            "memory_id": "remote-feature",
            "repo": tier_repo,
            "type": "explicit_rule",
            "content": "feature rule",
            "scope": "src/a.py",
            "confidence": 0.9,
            "status": "active",
            "branch": "feature/x",
            "scope_tier": "branch",
            "updated_at": "2099-01-01T00:00:00+00:00",
        }
    ]
    stats = memory_db.apply_cloud_memory_rows(tier_repo, pulled)
    assert stats["memories_applied"] == 1
    local_main = [
        m for m in memory_db.list_memory_entries(repo=tier_repo, status="active") if m["branch"] == "main"
    ]
    assert len(local_main) == 1
    feature = memory_db.get_memory_entry("remote-feature")
    assert feature is not None
    assert feature["branch"] == "feature/x"


def test_tombstone_lww_branch_agnostic(memory_db, tier_repo):
    """TC-BR-35: remote tombstone deprecates local active row regardless of branch."""
    mem_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="local main copy",
        scope="repo",
        status="active",
        branch="main",
        scope_tier="branch",
    )
    memory_db.update_memory_entry(mem_id, content="local main copy touched")
    stats = memory_db.apply_cloud_memory_rows(
        tier_repo,
        [{
            "memory_id": mem_id,
            "repo": tier_repo,
            "type": "explicit_rule",
            "content": "local main copy",
            "scope": "repo",
            "confidence": 0.9,
            "status": "deprecated",
            "branch": "feature/old",
            "updated_at": "2000-01-01T00:00:00+00:00",
            "deleted_at": "2000-01-01T00:00:00+00:00",
        }],
    )
    assert stats["tombstones_applied"] == 1
    assert memory_db.get_memory_entry(mem_id)["status"] == "deprecated"


def test_tombstone_pull_includes_other_branch(memory_db, tier_repo):
    """TC-BR-31a cloud / SPEC-BR-05: tombstone on feature branch applies when syncing as main."""
    mem_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="shared rule",
        scope="repo",
        status="active",
        branch="main",
    )
    stats = memory_db.apply_cloud_memory_rows(
        tier_repo,
        [{
            "memory_id": mem_id,
            "repo": tier_repo,
            "type": "explicit_rule",
            "content": "shared rule",
            "scope": "repo",
            "confidence": 0.9,
            "status": "deprecated",
            "branch": "feature/pr-99",
            "updated_at": "2099-01-01T00:00:00+00:00",
        }],
    )
    assert stats["tombstones_applied"] == 1
    assert memory_db.get_memory_entry(mem_id)["status"] == "deprecated"


def test_multi_machine_same_branch_converge(memory_db, tier_repo):
    """TC-BR-37: second machine applies branch-scoped cloud row on same branch."""
    pulled = [{
        "memory_id": "shared-id",
        "repo": tier_repo,
        "type": "explicit_rule",
        "content": "converged",
        "scope": "repo",
        "confidence": 0.9,
        "status": "active",
        "branch": "feature/sync",
        "scope_tier": "branch",
        "updated_at": "2099-01-01T00:00:00+00:00",
    }]
    memory_db.apply_cloud_memory_rows(tier_repo, pulled)
    row = memory_db.get_memory_entry("shared-id")
    assert row["branch"] == "feature/sync"
    assert row["content"] == "converged"


def test_resolve_sync_branch_from_env(monkeypatch):
    monkeypatch.setenv("TURINGMIND_SYNC_BRANCH", "feature/env-branch")
    assert resolve_sync_branch() == "feature/env-branch"
