"""Regression tests for sync atomicity and schema/recall_history ordering."""

from __future__ import annotations

import uuid

import pytest

from turingmind_mcp.sync_service import run_sync
from turingmind_mcp.v2_engine.database import get_spec_node, save_spec_node
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
    """Point v2 graph operations at the same SQLite file as MemoryDatabase."""
    from turingmind_mcp.v2_engine import database as v2db

    monkeypatch.setenv("TURINGMIND_DB_PATH", memory_db.db_path)
    monkeypatch.setattr(v2db, "DB_PATH", memory_db.db_path)
    return memory_db


def _node(
    node_id: str,
    repo: str,
    files: list[str],
    deps: list[str] | None = None,
    confidence: float = 0.9,
) -> SpecNode:
    return SpecNode(
        id=node_id,
        repo=repo,
        title=node_id,
        level=NodeLevel.L3_API,
        surface_type=SurfaceType.INTERNAL,
        dependencies=deps or [],
        owner="test",
        description="test node",
        implementation=Implementation(files=files, functions=[]),
        state=NodeState(
            status=SpecStatus.VERIFIED,
            stage=ExecutionStage.VERIFIED,
            confidence=confidence,
            evidence=[],
        ),
    )


def test_schema_error_does_not_pollute_recall_history(api_client, tier_repo):
    """TM-SCHEMA-ERR must not append memory ids to session recall_history."""
    client, db = api_client
    composer_id = f"composer-schema-{uuid.uuid4()}"

    bad_content = "X" * 2500
    db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content=bad_content,
        scope="database/postgres.py",
        branch="main",
        confidence=1.0,
    )

    response = client.post(
        "/api/v2/sync",
        json={
            "repo": tier_repo,
            "files": ["database/postgres.py"],
            "composer_id": composer_id,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["recall_bundle"]["policy"]["code"] == "TM-SCHEMA-ERR"

    sess = db.get_coding_session(composer_id, tier_repo)
    assert sess is not None
    assert sess["recall_history"] == []


def test_cascade_preserves_invalidation_evidence_in_transaction(
    api_client, tier_repo, unified_v2_db
):
    """Cascade reads within the sync transaction must not clobber code_change evidence."""
    _, db = api_client
    repo = tier_repo
    upstream_id = f"node-upstream-{uuid.uuid4().hex[:8]}"
    downstream_id = f"node-downstream-{uuid.uuid4().hex[:8]}"
    touched = "src/shared/module.py"

    upstream = _node(upstream_id, repo, [touched])
    downstream = _node(downstream_id, repo, [touched], deps=[upstream_id])
    save_spec_node(upstream)
    save_spec_node(downstream)

    result = run_sync(
        db,
        repo=repo,
        files=[touched],
        composer_id=f"composer-cascade-{uuid.uuid4()}",
    )
    assert result["status"] == "synced"
    assert upstream_id in result["direct_impact_nodes"] or downstream_id in result["direct_impact_nodes"]

    refreshed = get_spec_node(downstream_id)
    assert refreshed is not None
    kinds = {ev.kind for ev in refreshed.state.evidence}
    if downstream_id in result["direct_impact_nodes"]:
        assert "code_change" in kinds
    if result["cascades_triggered"] > 0:
        assert "blast_radius_cascade" in kinds


def test_v2_read_does_not_commit_write_transaction(api_client, tier_repo, unified_v2_db):
    """Borrowed write connections must not auto-commit on read (regression guard)."""
    from turingmind_mcp.v2_engine.database import _borrow_connection, use_write_connection

    _, db = api_client
    node_id = f"node-read-{uuid.uuid4().hex[:8]}"
    save_spec_node(_node(node_id, tier_repo, ["src/read_guard.py"]))

    conn = db.conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        with use_write_connection(conn):
            before = get_spec_node(node_id, conn=conn)
            assert before is not None
            active, owned = _borrow_connection(conn)
            assert active is conn
            assert owned is False
            _ = get_spec_node(node_id, conn=conn)
        # If reads auto-committed, this rollback would not undo a phantom commit.
        conn.execute("ROLLBACK")
    finally:
        pass

    after = get_spec_node(node_id)
    assert after is not None
    assert after.state.confidence == pytest.approx(0.9)
