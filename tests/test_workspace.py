"""Multi-root workspace_id resolution and ground union."""

from __future__ import annotations

import json
from pathlib import Path

from turingmind_mcp.grounding import compose_ground
from turingmind_mcp.memory_manager import MemoryManager
from turingmind_mcp.workspace import (
    derive_workspace_id,
    merge_entry_lists,
    resolve_workspace,
)


def test_resolve_workspace_from_explicit_repos():
    ws = resolve_workspace(
        repos=["org/a", "org/b"],
        primary_repo="org/a",
    )
    assert ws.primary_repo == "org/a"
    assert ws.repos == ["org/a", "org/b"]
    assert ws.workspace_id == derive_workspace_id(["org/a", "org/b"])
    assert ws.working_set_repo == f"workspace/{ws.workspace_id}"


def test_resolve_workspace_from_config_file(tmp_path, monkeypatch):
    path = tmp_path / "workspaces.json"
    path.write_text(
        json.dumps({
            "gaussian-asg": {
                "repos": ["gaussian/macos", "gaussian/smcp", "gaussian/service"],
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("TURINGMIND_WORKSPACES_FILE", str(path))
    monkeypatch.delenv("TURINGMIND_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("TURINGMIND_WORKSPACE_REPOS", raising=False)

    ws = resolve_workspace(workspace_id="gaussian-asg")
    assert ws.workspace_id == "gaussian-asg"
    assert ws.repos == ["gaussian/macos", "gaussian/smcp", "gaussian/service"]
    assert ws.working_set_repo == "workspace/gaussian-asg"


def test_merge_entry_lists_dedupes():
    a = {"memory_id": "1", "content": "A", "confidence": 0.5}
    b = {"memory_id": "1", "content": "A", "confidence": 0.9}
    c = {"memory_id": "2", "content": "B", "confidence": 0.7}
    merged = merge_entry_lists([[a], [b, c]], limit=10)
    assert len(merged) == 2
    assert merged[0]["memory_id"] == "1"
    assert merged[0]["confidence"] == 0.9


def test_compose_ground_unions_repos(memory_db, tier_repo):
    mgr = MemoryManager(memory_db)
    other = "org/other-linked"
    mgr.create_explicit_rule(
        repo=tier_repo,
        content="Primary repo rule: use ground with workspace_id",
        scope="repo",
    )
    mgr.create_explicit_rule(
        repo=other,
        content="Linked repo rule: never store secrets in memory",
        scope="repo",
    )
    memory_db.create_memory_entry(
        repo=f"workspace/demo",
        memory_type="session_context",
        content="Working set [demo] — composer test — session closed",
        scope="workspace",
        confidence=0.5,
        status="active",
    )

    payload = compose_ground(
        mgr,
        repo=tier_repo,
        repos=[tier_repo, other],
        workspace_id="demo",
        limit=10,
        decision_queue_builder=lambda **kw: {"queue": []},
    )
    assert payload["workspace_id"] == "demo"
    assert set(payload["repos"]) >= {tier_repo, other}
    contents = {e["content"] for e in payload["rules"]}
    assert any("Primary repo rule" in c for c in contents)
    assert any("Linked repo rule" in c for c in contents)
    assert payload["working_set"]
    assert "workspace: `demo`" in payload["digest"]
