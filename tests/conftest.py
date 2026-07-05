"""Shared fixtures for memory tier test suites."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.memory_manager import MemoryManager

TIER_SANDBOX_REPO = "test-org/tier-sandbox"


@pytest.fixture
def tier_repo() -> str:
    return TIER_SANDBOX_REPO


@pytest.fixture
def memory_db(tmp_path):
    db = MemoryDatabase(str(tmp_path / "memory.db"))
    yield db
    db.close()


@pytest.fixture
def api_client(memory_db):
    """FastAPI TestClient backed by an isolated memory database."""
    import turingmind_mcp.api_server as api_mod
    from turingmind_mcp.recall_bundle import reset_recall_history_cache

    manager = MemoryManager(memory_db)
    previous_db = api_mod._memory_db_instance
    previous_manager = api_mod._memory_manager_instance
    api_mod._memory_db_instance = memory_db
    api_mod._memory_manager_instance = manager
    reset_recall_history_cache()
    from fastapi.testclient import TestClient

    client = TestClient(api_mod.app)
    yield client, memory_db
    reset_recall_history_cache()
    api_mod._memory_db_instance = previous_db
    api_mod._memory_manager_instance = previous_manager


def _run_git(cwd: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f"git failed: {args}")


@pytest.fixture
def git_sandbox(tmp_path) -> Path:
    """Minimal git repo on branch main with one commit."""
    root = tmp_path / "repo"
    root.mkdir()
    _run_git(root, "init", "-b", "main")
    _run_git(root, "config", "user.email", "test@turingmind.local")
    _run_git(root, "config", "user.name", "TuringMind Test")
    (root / "README.md").write_text("initial\n", encoding="utf-8")
    _run_git(root, "add", "README.md")
    _run_git(root, "commit", "-m", "initial")
    return root


@pytest.fixture
def branch_recall_enabled(monkeypatch):
    monkeypatch.setenv("TURINGMIND_BRANCH_MEMORY", "1")


@pytest.fixture
def sample_git_payload(git_sandbox) -> dict:
    """Git blob matching git_sandbox HEAD."""
    from turingmind_mcp.git_context import collect_git_context

    ctx = collect_git_context(git_sandbox)
    assert ctx is not None
    return {
        "branch": ctx.branch,
        "head": ctx.head,
        "dirty": ctx.dirty,
        "default_branch": ctx.default_branch,
    }
