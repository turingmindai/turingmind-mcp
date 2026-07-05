"""V2 constraint-graph schema and cross-table integrity for the unified store."""

from __future__ import annotations

import sqlite3


def initialize_v2_schema(cursor: sqlite3.Cursor) -> None:
    """Create SpecNode graph tables (idempotent). Must run before memory tables that reference node_id."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS spec_nodes (
            id TEXT PRIMARY KEY,
            repo TEXT NOT NULL,
            level TEXT NOT NULL,
            surface_type TEXT NOT NULL,
            status TEXT NOT NULL,
            stage TEXT NOT NULL,
            confidence REAL NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS edge_graph (
            upstream_id TEXT NOT NULL,
            downstream_id TEXT NOT NULL,
            PRIMARY KEY (upstream_id, downstream_id),
            FOREIGN KEY (upstream_id) REFERENCES spec_nodes(id) ON DELETE CASCADE,
            FOREIGN KEY (downstream_id) REFERENCES spec_nodes(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS execution_state (
            repo TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS spec_blueprints (
            node_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (node_id) REFERENCES spec_nodes(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_repo ON spec_nodes(repo)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_stage ON spec_nodes(stage)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_downstream ON edge_graph(downstream_id)")


def ensure_node_integrity_triggers(cursor: sqlite3.Cursor) -> None:
    """Clear node_id references when a SpecNode is deleted (soft FK across legacy columns)."""
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_spec_nodes_delete_null_memory
        AFTER DELETE ON spec_nodes
        FOR EACH ROW
        BEGIN
            UPDATE memory_entries SET node_id = NULL WHERE node_id = OLD.id;
            UPDATE observations SET node_id = NULL WHERE node_id = OLD.id;
            UPDATE reconcile_findings SET node_id = NULL WHERE node_id = OLD.id;
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_memory_entries_node_id_valid
        BEFORE INSERT ON memory_entries
        FOR EACH ROW
        WHEN NEW.node_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM spec_nodes WHERE id = NEW.node_id)
        BEGIN
            SELECT RAISE(ABORT, 'memory_entries.node_id references missing spec_nodes row');
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_memory_entries_node_id_valid_upd
        BEFORE UPDATE OF node_id ON memory_entries
        FOR EACH ROW
        WHEN NEW.node_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM spec_nodes WHERE id = NEW.node_id)
        BEGIN
            SELECT RAISE(ABORT, 'memory_entries.node_id references missing spec_nodes row');
        END
    """)
