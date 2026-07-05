"""
SQLite database interface for the v2 Deterministic Constraint Engine.

SpecNode graph tables live in the same file as operational memory
(``~/.turingmind/memory.db``). Legacy ``v2_memory.db`` is migrated on first
``MemoryDatabase`` open via ``db_migration.migrate_legacy_v2_if_needed``.
"""

import logging
import sqlite3
from collections import deque
from contextlib import contextmanager
from typing import Generator, List, Optional

from ..db_paths import ensure_db_dir, resolve_primary_db_path
from ..unified_schema import initialize_v2_schema
from .models import SpecNode, ExecutionState, ExecutionStage, FailureClassification

logger = logging.getLogger(__name__)

# When set, save_spec_node/save_spec_nodes use this connection and skip commit.
_write_conn: Optional[sqlite3.Connection] = None

# Optional override for tests (patch this module attribute).
DB_PATH: Optional[str] = None
DB_DIR = None  # deprecated; kept so existing tests can patch DB_DIR without error


def _connection_path() -> str:
    if DB_PATH is not None:
        return DB_PATH
    return resolve_primary_db_path()


def _open_connection() -> sqlite3.Connection:
    """Open a new SQLite connection (never returns the active write-transaction conn)."""
    db_path = _connection_path()
    ensure_db_dir(db_path)

    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _get_connection() -> sqlite3.Connection:
    """Return the active write-transaction connection or open a standalone one."""
    if _write_conn is not None:
        return _write_conn
    return _open_connection()


def _borrow_connection(
    explicit: Optional[sqlite3.Connection] = None,
) -> tuple[sqlite3.Connection, bool]:
    """Return ``(connection, owned)``. Owned connections must be closed by caller.

    Never use ``with conn:`` on borrowed write-transaction connections — that
    would commit an in-flight ``BEGIN`` from atomic sync.
    """
    if explicit is not None:
        return explicit, False
    if _write_conn is not None:
        return _write_conn, False
    return _open_connection(), True


def _release_connection(conn: sqlite3.Connection, owned: bool) -> None:
    if owned:
        conn.close()


@contextmanager
def use_write_connection(conn: sqlite3.Connection) -> Generator[sqlite3.Connection, None, None]:
    """Route v2 writes through an external connection (caller owns transaction)."""
    global _write_conn
    previous = _write_conn
    _write_conn = conn
    try:
        yield conn
    finally:
        _write_conn = previous


def init_db() -> None:
    """Initialize the v2 constraint schema in the unified store (idempotent)."""
    conn = _open_connection()
    try:
        initialize_v2_schema(conn.cursor())
        conn.commit()
    finally:
        conn.close()


def _persist_spec_node(conn: sqlite3.Connection, node: SpecNode) -> None:
    """Write one SpecNode row and dependency edges."""
    cursor = conn.cursor()
    node_json = node.model_dump_json()
    cursor.execute("""
        INSERT INTO spec_nodes 
        (id, repo, level, surface_type, status, stage, confidence, data, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            status=excluded.status,
            stage=excluded.stage,
            confidence=excluded.confidence,
            data=excluded.data,
            updated_at=excluded.updated_at
    """, (
        node.id, node.repo, node.level.value, node.surface_type.value,
        node.state.status.value, node.state.stage.value, node.state.confidence,
        node_json, node.created_at, node.updated_at
    ))

    cursor.execute("DELETE FROM edge_graph WHERE downstream_id = ?", (node.id,))
    for upstream_id in node.dependencies:
        cursor.execute("""
            INSERT OR IGNORE INTO edge_graph (upstream_id, downstream_id) 
            VALUES (?, ?)
        """, (upstream_id, node.id))


def save_spec_node(node: SpecNode, conn: Optional[sqlite3.Connection] = None) -> None:
    """Save a SpecNode and strictly sync its dependency edges in the mathematical graph."""
    # Evidence FIFO rotation — cap at 100 most recent entries to prevent
    # unbounded JSON blob growth after hundreds of CI runs.
    MAX_EVIDENCE = 100
    if len(node.state.evidence) > MAX_EVIDENCE:
        node.state.evidence = node.state.evidence[-MAX_EVIDENCE:]

    external = conn is not None or _write_conn is not None
    active_conn = conn or _get_connection()
    owned = conn is None and _write_conn is None
    try:
        _persist_spec_node(active_conn, node)
        if not external:
            active_conn.commit()
    finally:
        if owned:
            active_conn.close()


def get_spec_node(
    node_id: str,
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[SpecNode]:
    """Retrieve an atomic constraint node."""
    active, owned = _borrow_connection(conn)
    try:
        cursor = active.cursor()
        cursor.execute("SELECT data FROM spec_nodes WHERE id = ?", (node_id,))
        row = cursor.fetchone()
        if row:
            return SpecNode.model_validate_json(row["data"])
        return None
    finally:
        _release_connection(active, owned)


def save_spec_nodes(nodes: List[SpecNode], conn: Optional[sqlite3.Connection] = None) -> None:
    """Atomically persist multiple SpecNodes in a single SQLite transaction."""
    if not nodes:
        return

    external = conn is not None or _write_conn is not None
    active_conn = conn or _get_connection()
    owned = conn is None and _write_conn is None
    owns_transaction = not external
    try:
        if owns_transaction:
            active_conn.execute("BEGIN")
        cursor = active_conn.cursor()
        for node in nodes:
            cursor.execute("""
                UPDATE spec_nodes
                SET status = ?, stage = ?, confidence = ?, data = ?, updated_at = ?
                WHERE id = ?
            """, (
                node.state.status.value,
                node.state.stage.value,
                node.state.confidence,
                node.model_dump_json(),
                node.updated_at,
                node.id,
            ))
        if owns_transaction:
            active_conn.execute("COMMIT")
    except Exception:
        if owns_transaction:
            active_conn.execute("ROLLBACK")
        raise
    finally:
        if owned:
            active_conn.close()


def get_nodes_by_stage(repo: str, stage: ExecutionStage) -> List[SpecNode]:
    """Fetch the exact nodes occupying a specific column in the manufacturing pipeline."""
    active, owned = _borrow_connection()
    try:
        cursor = active.cursor()
        cursor.execute("""
            SELECT data FROM spec_nodes 
            WHERE repo = ? AND stage = ?
            ORDER BY created_at ASC
        """, (repo, stage.value))

        return [SpecNode.model_validate_json(row["data"]) for row in cursor.fetchall()]
    finally:
        _release_connection(active, owned)


def get_all_spec_nodes(
    repo: str,
    conn: Optional[sqlite3.Connection] = None,
) -> List[SpecNode]:
    """Fetch all spec nodes for a repo (used for bootstrap deduplication)."""
    active, owned = _borrow_connection(conn)
    try:
        cursor = active.cursor()
        cursor.execute("""
            SELECT data FROM spec_nodes
            WHERE repo = ?
            ORDER BY created_at ASC
        """, (repo,))
        nodes = []
        for row in cursor.fetchall():
            try:
                nodes.append(SpecNode.model_validate_json(row["data"]))
            except Exception:
                pass
        return nodes
    finally:
        _release_connection(active, owned)


def get_impacted_subgraph(
    upstream_id: str,
    conn: Optional[sqlite3.Connection] = None,
) -> List[str]:
    """Return all nodes downstream from an origin node (BFS)."""
    impacted: set[str] = set()
    queue: deque[str] = deque([upstream_id])

    active, owned = _borrow_connection(conn)
    try:
        cursor = active.cursor()
        while queue:
            current = queue.popleft()
            cursor.execute(
                "SELECT downstream_id FROM edge_graph WHERE upstream_id = ?", (current,)
            )
            for row in cursor.fetchall():
                down_id = row["downstream_id"]
                if down_id not in impacted:
                    impacted.add(down_id)
                    queue.append(down_id)
    finally:
        _release_connection(active, owned)

    return list(impacted)


def get_impacted_subgraph_with_depth(
    upstream_id: str,
    conn: Optional[sqlite3.Connection] = None,
) -> List[tuple]:
    """Like get_impacted_subgraph but returns (node_id, depth) tuples."""
    result: list[tuple] = []
    visited: set[str] = set()
    queue: deque[tuple] = deque([(upstream_id, 0)])

    active, owned = _borrow_connection(conn)
    try:
        cursor = active.cursor()
        while queue:
            current, depth = queue.popleft()
            cursor.execute(
                "SELECT downstream_id FROM edge_graph WHERE upstream_id = ?", (current,)
            )
            for row in cursor.fetchall():
                down_id = row["downstream_id"]
                if down_id not in visited:
                    visited.add(down_id)
                    child_depth = depth + 1
                    result.append((down_id, child_depth))
                    queue.append((down_id, child_depth))
    finally:
        _release_connection(active, owned)

    return result


def save_blueprint(node_id: str, payload: str) -> None:
    """Save an architectural diagram payload separate from main node JSON bloat."""
    import datetime

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = _open_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO spec_blueprints (node_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                payload=excluded.payload,
                updated_at=excluded.updated_at
        """, (node_id, payload, now_iso))
        conn.commit()
    finally:
        conn.close()


def get_blueprint(node_id: str) -> Optional[str]:
    """Retrieve an architectural payload."""
    active, owned = _borrow_connection()
    try:
        cursor = active.cursor()
        cursor.execute("SELECT payload FROM spec_blueprints WHERE node_id = ?", (node_id,))
        row = cursor.fetchone()
        if row:
            return row["payload"]
        return None
    finally:
        _release_connection(active, owned)


def get_execution_state(repo: str) -> ExecutionState:
    """Loads the Control Plane data for the system."""
    active, owned = _borrow_connection()
    try:
        cursor = active.cursor()
        cursor.execute("SELECT data FROM execution_state WHERE repo = ?", (repo,))
        row = cursor.fetchone()
        if row:
            return ExecutionState.model_validate_json(row["data"])
        return ExecutionState()
    finally:
        _release_connection(active, owned)


def save_execution_state(repo: str, state: ExecutionState) -> None:
    """Updates the global metrics, ready_queue, and failed_queues."""
    import datetime

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    conn = _open_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO execution_state (repo, data, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(repo) DO UPDATE SET
                data=excluded.data,
                updated_at=excluded.updated_at
        """, (repo, state.model_dump_json(), now_iso))
        conn.commit()
    finally:
        conn.close()


init_db()
