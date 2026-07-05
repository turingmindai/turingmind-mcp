"""Tests for unified SQLite store and legacy v2 migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.db_migration import migrate_legacy_v2_if_needed
from turingmind_mcp.v2_engine.database import DB_PATH, get_spec_node, init_db, save_spec_node
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
def unified_paths(tmp_path, monkeypatch):
    primary = tmp_path / "memory.db"
    legacy = tmp_path / "v2_memory.db"
    monkeypatch.setenv("TURINGMIND_DB_PATH", str(primary))
    monkeypatch.setenv("TURINGMIND_LEGACY_V2_DB", str(legacy))
    import turingmind_mcp.v2_engine.database as v2db

    monkeypatch.setattr(v2db, "DB_PATH", str(primary))
    return primary, legacy


def _seed_legacy_v2(legacy_path: Path, node_id: str = "node-legacy-1") -> None:
    conn = sqlite3.connect(legacy_path)
    conn.execute("PRAGMA foreign_keys=ON")
    from turingmind_mcp.unified_schema import initialize_v2_schema

    initialize_v2_schema(conn.cursor())
    conn.execute(
        """
        INSERT INTO spec_nodes
        (id, repo, level, surface_type, status, stage, confidence, data, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            node_id,
            "org/legacy",
            "L1_FILE",
            "internal",
            "verified",
            "verified",
            1.0,
            '{"id":"node-legacy-1"}',
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
    )
    conn.commit()
    conn.close()


def test_migrate_legacy_v2_into_primary(unified_paths):
    primary, legacy = unified_paths
    _seed_legacy_v2(legacy)

    result = migrate_legacy_v2_if_needed(str(primary))
    assert result["migrated"] is True
    assert result["tables_copied"]["spec_nodes"] == 1
    assert not legacy.exists()

    conn = sqlite3.connect(primary)
    count = conn.execute("SELECT COUNT(*) FROM spec_nodes").fetchone()[0]
    conn.close()
    assert count == 1


def test_remove_empty_legacy_v2_shell(unified_paths):
    """Empty legacy v2_memory.db is deleted instead of left on disk."""
    primary, legacy = unified_paths
    legacy.touch()

    result = migrate_legacy_v2_if_needed(str(primary))
    assert result["reason"] == "legacy_empty"
    assert result.get("legacy_removed") is True
    assert not legacy.exists()


def test_memory_and_v2_share_one_file(unified_paths):
    primary, legacy = unified_paths
    db = MemoryDatabase(str(primary))
    try:
        node = SpecNode(
            id="unified-node-1",
            repo="org/unified",
            title="Unified",
            level=NodeLevel.L1_FILE,
            surface_type=SurfaceType.INTERNAL,
            dependencies=[],
            owner="test",
            description="test",
            implementation=Implementation(files=["a.py"], functions=[]),
            state=NodeState(
                status=SpecStatus.VERIFIED,
                stage=ExecutionStage.VERIFIED,
                confidence=1.0,
                evidence=[],
            ),
        )
        save_spec_node(node)

        memory_id = db.create_memory_entry(
            repo="org/unified",
            memory_type="learned_pattern",
            content="Always validate node_id",
            scope="a.py",
            node_id="unified-node-1",
        )
        assert memory_id

        loaded = get_spec_node("unified-node-1")
        assert loaded is not None
        assert loaded.id == "unified-node-1"
    finally:
        db.close()


def test_node_delete_clears_memory_node_id(unified_paths):
    primary, _legacy = unified_paths
    db = MemoryDatabase(str(primary))
    try:
        node = SpecNode(
            id="to-delete",
            repo="org/unified",
            title="Delete me",
            level=NodeLevel.L1_FILE,
            surface_type=SurfaceType.INTERNAL,
            dependencies=[],
            owner="test",
            description="test",
            implementation=Implementation(files=["b.py"], functions=[]),
            state=NodeState(
                status=SpecStatus.VERIFIED,
                stage=ExecutionStage.VERIFIED,
                confidence=1.0,
                evidence=[],
            ),
        )
        save_spec_node(node)
        memory_id = db.create_memory_entry(
            repo="org/unified",
            memory_type="learned_pattern",
            content="linked",
            scope="b.py",
            node_id="to-delete",
        )
        db.conn.execute("DELETE FROM spec_nodes WHERE id = ?", ("to-delete",))
        db.conn.commit()
        row = db.get_memory_entry(memory_id)
        assert row is not None
        assert row.get("node_id") is None
    finally:
        db.close()


def test_invalid_node_id_rejected(unified_paths):
    primary, _legacy = unified_paths
    db = MemoryDatabase(str(primary))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            db.create_memory_entry(
                repo="org/unified",
                memory_type="learned_pattern",
                content="orphan link",
                scope="repo",
                node_id="missing-node",
            )
    finally:
        db.close()
