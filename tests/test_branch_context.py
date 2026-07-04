"""Phase 4.1 branch git context capture (TC-BR-01 … TC-BR-10, TC-BR-02a).

Spec reference: docs/branch-aware-memory-plan.html
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.git_context import (
    branch_memory_ranking_enabled,
    collect_git_context,
    derive_scope_tier,
    resolve_default_branch,
)


def _run_git(cwd: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f"git failed: {args}")


@pytest.mark.branch
def test_tc_br_01_collect_git_context_clean(git_sandbox):
    """TC-BR-01: clean repo on main → branch, head, dirty=false."""
    ctx = collect_git_context(git_sandbox)
    assert ctx is not None
    assert ctx.branch == "main"
    assert ctx.head is not None
    assert len(ctx.head) == 40
    assert ctx.dirty is False


@pytest.mark.branch
def test_tc_br_02_collect_git_context_dirty(git_sandbox):
    """TC-BR-02: uncommitted change → dirty=true."""
    (git_sandbox / "dirty.txt").write_text("wip\n", encoding="utf-8")
    ctx = collect_git_context(git_sandbox)
    assert ctx is not None
    assert ctx.dirty is True


@pytest.mark.branch
def test_tc_br_02a_default_branch_fallback_chain(tmp_path):
    """TC-BR-02a: master-only repo resolves default_branch without origin/HEAD."""
    root = tmp_path / "master-repo"
    root.mkdir()
    _run_git(root, "init", "-b", "master")
    _run_git(root, "config", "user.email", "t@local")
    _run_git(root, "config", "user.name", "T")
    (root / "f.txt").write_text("x\n", encoding="utf-8")
    _run_git(root, "add", "f.txt")
    _run_git(root, "commit", "-m", "init")

    assert resolve_default_branch(root) == "master"
    ctx = collect_git_context(root)
    assert ctx is not None
    assert ctx.default_branch == "master"


@pytest.mark.branch
def test_tc_br_03_observation_api_git_persist(api_client, tier_repo, sample_git_payload):
    """TC-BR-03: POST /observations stores branch columns; stays pending."""
    client, db = api_client
    response = client.post(
        "/api/v2/observations",
        json={
            "repo": tier_repo,
            "git": sample_git_payload,
            "observations": [
                {
                    "event_type": "edit_cluster",
                    "content": "targeted_fix/low: test git obs",
                    "source": "cursor-hook",
                }
            ],
        },
    )
    assert response.status_code == 200
    obs_id = response.json()["observation_ids"][0]
    row = next(o for o in db.list_observations(repo=tier_repo) if o["observation_id"] == obs_id)
    assert row["branch"] == sample_git_payload["branch"]
    assert row["head_sha"] == sample_git_payload["head"]
    assert row["git_dirty"] == 0
    assert row["status"] == "pending"
    assert json.loads(row["git_context"])["branch"] == sample_git_payload["branch"]


@pytest.mark.branch
def test_tc_br_04_memory_api_git_persist(api_client, tier_repo, sample_git_payload):
    """TC-BR-04: POST /memory stores git columns."""
    client, db = api_client
    dirty_payload = {**sample_git_payload, "dirty": True}
    response = client.post(
        "/api/v2/memory",
        json={
            "repo": tier_repo,
            "type": "explicit_rule",
            "content": "Branch-scoped rule",
            "scope": "src/auth.py",
            "git": dirty_payload,
        },
    )
    assert response.status_code == 200
    memory_id = response.json()["memory_id"]
    row = db.get_memory_entry(memory_id)
    assert row["branch"] == dirty_payload["branch"]
    assert row["head_sha"] == dirty_payload["head"]
    assert row["git_dirty"] == 1
    assert row["scope_tier"] == "working_tree"


@pytest.mark.branch
def test_tc_br_05_legacy_payload_null_branch(api_client, tier_repo):
    """TC-BR-05: omitting git succeeds with NULL branch (L4 semantics)."""
    client, db = api_client
    obs = client.post(
        "/api/v2/observations",
        json={
            "repo": tier_repo,
            "observations": [{"event_type": "edit_cluster", "content": "legacy obs"}],
        },
    )
    assert obs.status_code == 200
    obs_row = db.list_observations(repo=tier_repo)[0]
    assert obs_row["branch"] is None
    assert obs_row["head_sha"] is None

    mem = client.post(
        "/api/v2/memory",
        json={
            "repo": tier_repo,
            "type": "explicit_rule",
            "content": "legacy memory",
            "scope": "repo",
        },
    )
    assert mem.status_code == 200
    row = db.get_memory_entry(mem.json()["memory_id"])
    assert row["branch"] is None
    assert row.get("scope_tier") in (None, "repo")


@pytest.mark.branch
def test_tc_br_06_spool_replay_git_context(api_client, tier_repo, sample_git_payload):
    """TC-BR-06: batch replay with git blob preserves branch/head."""
    client, db = api_client
    spool_body = {
        "repo": tier_repo,
        "git": sample_git_payload,
        "observations": [
            {
                "event_type": "edit_cluster",
                "content": "spooled cluster",
                "source": "cursor-hook",
                "observed_at": "2026-07-04T12:00:00Z",
            }
        ],
    }
    first = client.post("/api/v2/observations", json=spool_body)
    assert first.status_code == 200
    row = db.list_observations(repo=tier_repo)[0]
    assert row["branch"] == sample_git_payload["branch"]
    assert row["head_sha"] == sample_git_payload["head"]


@pytest.mark.branch
@pytest.mark.skip(reason="TC-BR-07: manual Cursor hook smoke (Gate 2)")
def test_tc_br_07_cursor_hook_manual():
    """TC-BR-07: verified manually per memory-tier-test-plan Gate 2."""


@pytest.mark.branch
@pytest.mark.skip(reason="TC-BR-08: chat poller integration — Phase 4.1 follow-up")
def test_tc_br_08_poller_git_context():
    """TC-BR-08: chat_observation_poller attaches git context."""


@pytest.mark.branch
def test_tc_br_09_migration_idempotent(tmp_path):
    """TC-BR-09: schema migration safe to run twice."""
    db_path = tmp_path / "memory.db"
    db1 = MemoryDatabase(str(db_path))
    db1.conn.execute(
        "INSERT INTO memory_entries (memory_id, repo, type, content, scope, confidence, status) "
        "VALUES ('legacy-1', 'org/r', 'explicit_rule', 'old', 'repo', 0.8, 'active')"
    )
    db1.conn.commit()
    db1.close()

    db2 = MemoryDatabase(str(db_path))
    cols = {r[1] for r in db2.conn.execute("PRAGMA table_info(memory_entries)").fetchall()}
    assert "branch" in cols
    assert "head_sha" in cols
    assert "scope_tier" in cols
    row = db2.get_memory_entry("legacy-1")
    assert row["branch"] is None
    db2.close()

    db3 = MemoryDatabase(str(db_path))
    db3.close()


@pytest.mark.branch
def test_tc_br_10_flag_off_stores_git(monkeypatch, api_client, tier_repo, sample_git_payload):
    """TC-BR-10: TURINGMIND_BRANCH_MEMORY=0 still persists git on write."""
    monkeypatch.setenv("TURINGMIND_BRANCH_MEMORY", "0")
    assert branch_memory_ranking_enabled() is False

    client, db = api_client
    response = client.post(
        "/api/v2/memory",
        json={
            "repo": tier_repo,
            "type": "explicit_rule",
            "content": "stored under flag off",
            "scope": "repo",
            "git": sample_git_payload,
        },
    )
    assert response.status_code == 200
    row = db.get_memory_entry(response.json()["memory_id"])
    assert row["branch"] == sample_git_payload["branch"]


@pytest.mark.branch
def test_tc_br_f01_invalid_git_blob_rejected(api_client, tier_repo):
    """TC-BR-F01: invalid git.head → 400, no partial row."""
    client, db = api_client
    before = len(db.list_observations(repo=tier_repo))
    response = client.post(
        "/api/v2/observations",
        json={
            "repo": tier_repo,
            "git": {"branch": "main", "head": "not-a-valid-sha", "dirty": False},
            "observations": [
                {"event_type": "edit_cluster", "content": "should not persist"}
            ],
        },
    )
    assert response.status_code == 400
    assert len(db.list_observations(repo=tier_repo)) == before


@pytest.mark.branch
def test_derive_scope_tier_rules():
    """Sanity check aligned with SPEC-BR-08 derivation."""
    assert derive_scope_tier(None, False) == "repo"
    assert derive_scope_tier("feature/x", False) == "branch"
    assert derive_scope_tier("feature/x", True) == "working_tree"
