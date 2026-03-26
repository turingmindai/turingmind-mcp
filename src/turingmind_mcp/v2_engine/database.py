"""
SQLite database interface for the v2 Deterministic Constraint Engine.
Provides a strict graph-based schema mapping to SpecNodes and Execution loop queues.
"""

import logging
import os
import sqlite3
from collections import deque
from typing import List, Optional

from .models import SpecNode, ExecutionState, ExecutionStage, FailureClassification

logger = logging.getLogger(__name__)

# v2 Database lives isolated from the legacy system
DB_DIR = os.path.expanduser("~/.turingmind")
DB_PATH = os.path.join(DB_DIR, "v2_memory.db")

def _get_connection() -> sqlite3.Connection:
    """Gets a SQLite connection mapped to WAL mode for concurrent multi-agent safety."""
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR, exist_ok=True)
        
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # Enable Write-Ahead Logging for simultaneous Cursor CLI / AIDD UI access
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    # SQLite disables FK enforcement by default — must enable per-connection
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db() -> None:
    """Initializes the rigorous Constraint Schema."""
    with _get_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Atomic Constraints Table
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
        
        # 2. Directed Acyclic Graph Edges (For traversing blast radius)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS edge_graph (
                upstream_id TEXT NOT NULL,
                downstream_id TEXT NOT NULL,
                PRIMARY KEY (upstream_id, downstream_id),
                FOREIGN KEY (upstream_id) REFERENCES spec_nodes(id) ON DELETE CASCADE,
                FOREIGN KEY (downstream_id) REFERENCES spec_nodes(id) ON DELETE CASCADE
            )
        """)
        
        # 3. Global Control Plane 
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS execution_state (
                repo TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Indexes for pipeline speed
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_repo ON spec_nodes(repo)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_stage ON spec_nodes(stage)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_downstream ON edge_graph(downstream_id)")
        
        conn.commit()

# ==================================================
# DAG operations
# ==================================================

def save_spec_node(node: SpecNode) -> None:
    """Save a SpecNode and strictly sync its dependency edges in the mathematical graph."""
    # Evidence FIFO rotation — cap at 100 most recent entries to prevent
    # unbounded JSON blob growth after hundreds of CI runs.
    MAX_EVIDENCE = 100
    if len(node.state.evidence) > MAX_EVIDENCE:
        node.state.evidence = node.state.evidence[-MAX_EVIDENCE:]

    with _get_connection() as conn:
        cursor = conn.cursor()
        
        # Store node as JSON payload with indexed fast-access columns
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
        
        # Rebuild edges based strictly on node.dependencies
        # If node.dependencies = ['auth_node'], this node is DOWNSTREAM of 'auth_node'
        cursor.execute("DELETE FROM edge_graph WHERE downstream_id = ?", (node.id,))
        for upstream_id in node.dependencies:
            cursor.execute("""
                INSERT OR IGNORE INTO edge_graph (upstream_id, downstream_id) 
                VALUES (?, ?)
            """, (upstream_id, node.id))
            
        conn.commit()

def get_spec_node(node_id: str) -> Optional[SpecNode]:
    """Retrieve an atomic constraint node."""
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT data FROM spec_nodes WHERE id = ?", (node_id,))
        row = cursor.fetchone()
        if row:
            return SpecNode.model_validate_json(row["data"])
        return None

def get_nodes_by_stage(repo: str, stage: ExecutionStage) -> List[SpecNode]:
    """Fetch the exact nodes occupying a specific column in the manufacturing pipeline."""
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT data FROM spec_nodes 
            WHERE repo = ? AND stage = ?
            ORDER BY created_at ASC
        """, (repo, stage.value))
        
        return [SpecNode.model_validate_json(row["data"]) for row in cursor.fetchall()]

def get_all_spec_nodes(repo: str) -> List[SpecNode]:
    """Fetch all spec nodes for a repo (used for bootstrap deduplication)."""
    with _get_connection() as conn:
        cursor = conn.cursor()
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


def get_impacted_subgraph(upstream_id: str) -> List[str]:
    """
    The core Safe Change engine primitive: Returns all nodes downstream from an origin node.
    If 'upstream_id' (API endpoint) changes, EVERYTHING downstream must be invalidated.
    Uses iterative BFS to avoid stack overflow on large DAGs.
    """
    impacted: set[str] = set()
    queue: deque[str] = deque([upstream_id])

    with _get_connection() as conn:
        cursor = conn.cursor()
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

    return list(impacted)


def get_impacted_subgraph_with_depth(upstream_id: str) -> List[tuple]:
    """
    Like get_impacted_subgraph but returns (node_id, depth) tuples.
    Depth 1 = direct dependents, depth 2 = dependents of dependents, etc.
    Used by cascade_blast_radius for distance-attenuated confidence penalties.
    """
    result: list[tuple] = []
    visited: set[str] = set()
    queue: deque[tuple] = deque([(upstream_id, 0)])  # (node_id, depth)

    with _get_connection() as conn:
        cursor = conn.cursor()
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

    return result

# ==================================================
# Execution Loop Queue state
# ==================================================

def get_execution_state(repo: str) -> ExecutionState:
    """Loads the Control Plane data for the system."""
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT data FROM execution_state WHERE repo = ?", (repo,))
        row = cursor.fetchone()
        if row:
            return ExecutionState.model_validate_json(row["data"])
        return ExecutionState()

def save_execution_state(repo: str, state: ExecutionState) -> None:
    """Updates the global metrics, ready_queue, and failed_queues."""
    import datetime
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO execution_state (repo, data, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(repo) DO UPDATE SET
                data=excluded.data,
                updated_at=excluded.updated_at
        """, (repo, state.model_dump_json(), now_iso))
        conn.commit()

# Expose init on import
init_db()
