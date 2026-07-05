"""One-time migration from legacy ``v2_memory.db`` into the unified primary store."""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List

from .db_paths import legacy_v2_db_path, resolve_primary_db_path
from .unified_schema import initialize_v2_schema

logger = logging.getLogger("turingmind-mcp.migration")

_V2_TABLES: List[str] = [
    "spec_nodes",
    "edge_graph",
    "execution_state",
    "spec_blueprints",
]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _remove_legacy_shell(legacy_path: str, result: Dict[str, Any]) -> None:
    """Delete an empty legacy database file and WAL sidecars."""
    if not os.path.exists(legacy_path):
        return
    try:
        os.remove(legacy_path)
        result["legacy_removed"] = True
        for suffix in ("-wal", "-shm"):
            sidecar = legacy_path + suffix
            if os.path.exists(sidecar):
                os.remove(sidecar)
        logger.info("Removed empty legacy v2 store shell: %s", legacy_path)
    except OSError as exc:
        logger.warning("Failed to remove empty legacy v2 store %s: %s", legacy_path, exc)


def _copy_table(
    source: sqlite3.Connection,
    dest: sqlite3.Connection,
    table: str,
) -> int:
    if not _table_exists(source, table):
        return 0
    rows = source.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        return 0
    columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})").fetchall()]
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)
    dest.executemany(
        f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})",
        [tuple(row[c] for c in columns) for row in rows],
    )
    return len(rows)


def migrate_legacy_v2_if_needed(primary_path: str | None = None) -> Dict[str, Any]:
    """Copy SpecNode graph data from ``v2_memory.db`` into the primary database once.

    Skips when the legacy file is absent, paths are identical, primary already
    contains spec nodes, or legacy has no rows. On success, renames legacy file
    to ``*.migrated-<timestamp>``.
    """
    primary = resolve_primary_db_path(primary_path)
    legacy = legacy_v2_db_path()

    result: Dict[str, Any] = {
        "migrated": False,
        "primary_path": primary,
        "legacy_path": legacy,
        "tables_copied": {},
    }

    if os.path.abspath(primary) == os.path.abspath(legacy):
        result["reason"] = "same_path"
        return result

    if not os.path.exists(legacy):
        result["reason"] = "no_legacy_file"
        return result

    legacy_conn = sqlite3.connect(legacy)
    legacy_conn.row_factory = sqlite3.Row
    try:
        legacy_nodes = _row_count(legacy_conn, "spec_nodes")
        if legacy_nodes == 0:
            result["reason"] = "legacy_empty"
            _remove_legacy_shell(legacy, result)
            return result

        dest = sqlite3.connect(primary, timeout=10.0)
        dest.row_factory = sqlite3.Row
        try:
            dest.execute("PRAGMA journal_mode = WAL")
            dest.execute("PRAGMA foreign_keys = ON")
            dest.execute("PRAGMA busy_timeout = 5000")

            cursor = dest.cursor()
            initialize_v2_schema(cursor)
            dest.commit()

            if _row_count(dest, "spec_nodes") > 0:
                result["reason"] = "primary_already_has_spec_nodes"
                return result

            dest.execute("BEGIN")
            copied_total = 0
            for table in _V2_TABLES:
                count = _copy_table(legacy_conn, dest, table)
                result["tables_copied"][table] = count
                copied_total += count
            dest.commit()

            if copied_total == 0:
                result["reason"] = "nothing_copied"
                return result

            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = f"{legacy}.migrated-{stamp}"
            shutil.move(legacy, backup_path)
            for suffix in ("-wal", "-shm"):
                sidecar = legacy + suffix
                if os.path.exists(sidecar):
                    shutil.move(sidecar, backup_path + suffix)

            result["migrated"] = True
            result["backup_path"] = backup_path
            logger.info(
                "Migrated legacy v2 store into %s (backup %s): %s",
                primary,
                backup_path,
                result["tables_copied"],
            )
            return result
        finally:
            dest.close()
    finally:
        legacy_conn.close()
