"""Sync behavior under Memory vs Governed install profiles."""

from __future__ import annotations

import uuid

import pytest

from turingmind_mcp.control_plane import CognitionControlPlane
from turingmind_mcp.sync_service import run_sync
from turingmind_mcp.v2_engine.database import get_all_spec_nodes, get_spec_node, save_spec_node
from turingmind_mcp.v2_engine.models import (
    ExecutionStage,
    Implementation,
    NodeLevel,
    NodeState,
    SpecNode,
    SpecStatus,
    SurfaceType,
)


@pytest.fixture
def unified_v2_db(memory_db, monkeypatch):
    from turingmind_mcp.v2_engine import database as v2db

    monkeypatch.setenv("TURINGMIND_DB_PATH", memory_db.db_path)
    monkeypatch.setattr(v2db, "DB_PATH", memory_db.db_path)
    return memory_db


def _node(node_id: str, repo: str, files: list[str]) -> SpecNode:
    return SpecNode(
        id=node_id,
        repo=repo,
        title=node_id,
        level=NodeLevel.L3_API,
        surface_type=SurfaceType.INTERNAL,
        dependencies=[],
        owner="test",
        description="profile sync test node",
        implementation=Implementation(files=files, functions=[]),
        state=NodeState(
            status=SpecStatus.VERIFIED,
            stage=ExecutionStage.VERIFIED,
            confidence=0.9,
            evidence=[],
        ),
    )


def test_memory_profile_skips_invalidation_and_autobootstrap(
    memory_db, tier_repo, unified_v2_db, monkeypatch
):
    """Memory SKU sync must not mutate SpecNode graph on file edits."""
    monkeypatch.setenv("TURINGMIND_PROFILE", "memory")
    repo = tier_repo
    touched = "src/shared/module.py"
    node_id = f"node-memory-{uuid.uuid4().hex[:8]}"
    save_spec_node(_node(node_id, repo, [touched]))

    result = run_sync(
        memory_db,
        repo=repo,
        files=[touched],
        composer_id=f"composer-memory-{uuid.uuid4()}",
    )

    assert result["status"] == "synced"
    assert result["direct_impact_nodes"] == []
    assert get_all_spec_nodes(repo) == [get_spec_node(node_id)]
    refreshed = get_spec_node(node_id)
    assert refreshed is not None
    assert refreshed.state.confidence == pytest.approx(0.9)


def test_governed_profile_still_invalidates(
    memory_db, unified_v2_db, monkeypatch
):
    """Governed sync keeps graph invalidation on touched SpecNodes."""
    monkeypatch.setenv("TURINGMIND_PROFILE", "governed")
    repo = f"test-org/governed-sync-{uuid.uuid4().hex[:8]}"
    touched = "src/shared/module.py"
    node_id = f"node-governed-{uuid.uuid4().hex[:8]}"
    save_spec_node(_node(node_id, repo, [touched]))

    result = run_sync(
        memory_db,
        repo=repo,
        files=[touched],
        composer_id=f"composer-governed-{uuid.uuid4()}",
    )
    assert result["status"] == "synced"
    assert node_id in result["direct_impact_nodes"]


def test_memory_profile_skips_autobootstrap_on_empty_repo(
    memory_db, unified_v2_db, monkeypatch
):
    """Empty repos under memory profile must stay graph-free after sync."""
    monkeypatch.setenv("TURINGMIND_PROFILE", "memory")
    repo = f"test-org/memory-empty-{uuid.uuid4().hex[:8]}"

    result = CognitionControlPlane.sync_codebase(
        db=memory_db,
        repo=repo,
        files=["src/reconcile.py"],
        composer_id=f"composer-empty-{uuid.uuid4()}",
    )
    assert result["status"] == "synced"
    assert get_all_spec_nodes(repo) == []
