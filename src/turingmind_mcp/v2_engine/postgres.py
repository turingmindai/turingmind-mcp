import os
import json
import logging
from typing import List, Dict, Any, Optional

try:
    import psycopg2
    from psycopg2.extras import Json
except ImportError:
    psycopg2 = None

logger = logging.getLogger(__name__)

POSTGRES_URI = os.getenv("POSTGRES_URI")

def get_connection():
    if not psycopg2:
        raise RuntimeError("psycopg2 is not installed. Run pip install psycopg2-binary")
    if not POSTGRES_URI:
        raise ValueError("POSTGRES_URI environment variable is not set. Cannot connect to cloud database.")
    return psycopg2.connect(POSTGRES_URI)

def init_postgres():
    """Initializes the authoritative Postgres schema with JSONB support."""
    if not psycopg2:
        return
        
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # spec_nodes table using JSONB for the 'data' blob
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS spec_nodes (
                        id TEXT NOT NULL,
                        repo TEXT NOT NULL,
                        title TEXT NOT NULL,
                        level TEXT NOT NULL,
                        surface_type TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        status TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        data JSONB NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (repo, id)
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_spec_nodes_repo ON spec_nodes(repo)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_spec_nodes_stage ON spec_nodes(stage)")

                # edge_graph table for Recursive CTE capability
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS edge_graph (
                        upstream_id TEXT NOT NULL,
                        downstream_id TEXT NOT NULL,
                        repo TEXT NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (upstream_id, downstream_id)
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_edge_graph_repo ON edge_graph(repo)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_edge_graph_downstream ON edge_graph(downstream_id)")

                # execution_state table
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS execution_state (
                        repo TEXT PRIMARY KEY,
                        data JSONB NOT NULL,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # memory_entries — durable cloud copy of local SQLite memories
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS memory_entries (
                        memory_id TEXT NOT NULL,
                        repo TEXT NOT NULL,
                        type TEXT NOT NULL,
                        content TEXT NOT NULL,
                        scope TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        status TEXT NOT NULL,
                        security_tags JSONB,
                        yaml_definition TEXT,
                        created_at TIMESTAMP WITH TIME ZONE,
                        updated_at TIMESTAMP WITH TIME ZONE,
                        expires_at TIMESTAMP WITH TIME ZONE,
                        created_by TEXT,
                        node_id TEXT,
                        PRIMARY KEY (repo, memory_id)
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memory_entries_repo_type "
                    "ON memory_entries(repo, type)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memory_entries_repo_status "
                    "ON memory_entries(repo, status)"
                )
                cur.execute(
                    "ALTER TABLE memory_entries ADD COLUMN IF NOT EXISTS deleted_at "
                    "TIMESTAMP WITH TIME ZONE"
                )
    except Exception as e:
        logger.error(f"Failed to initialize Postgres schema: {e}")

def sync_cloud_state(repo: str, nodes: List[Any], edges: List[tuple], exec_state: Any) -> bool:
    """
    Atomically upserts local SQLite state into the Cloud Postgres DB.
    `nodes` should be a list of SpecNode Python objects.
    `edges` should be a list of tuples: (upstream_id, downstream_id).
    `exec_state` should be the ExecutionState object.
    """
    if not psycopg2:
        logger.error("psycopg2 not installed. Cannot sync to cloud.")
        return False
        
    import datetime
    import copy
    from ..secret_scrub import scrub_secrets

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def scrub_evidence(evidence_list):
        scrubbed = []
        for item in evidence_list:
            safe_item = copy.deepcopy(item) if isinstance(item, dict) else item
            if isinstance(safe_item, dict) and 'detail' in safe_item and isinstance(safe_item['detail'], str):
                safe_item['detail'] = scrub_secrets(safe_item['detail'])
            scrubbed.append(safe_item)
        return scrubbed
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Sync Execution State
                if exec_state:
                    cur.execute("""
                        INSERT INTO execution_state (repo, data, updated_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (repo) DO UPDATE SET
                            data = EXCLUDED.data,
                            updated_at = EXCLUDED.updated_at
                    """, (repo, Json(exec_state.model_dump()), now_iso))

                # 2. Sync SpecNodes
                for node in nodes:
                    cur.execute("""
                        INSERT INTO spec_nodes (id, repo, title, level, surface_type, stage, status, confidence, data, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (repo, id) DO UPDATE SET
                            title = EXCLUDED.title,
                            level = EXCLUDED.level,
                            surface_type = EXCLUDED.surface_type,
                            stage = EXCLUDED.stage,
                            status = EXCLUDED.status,
                            confidence = EXCLUDED.confidence,
                            data = EXCLUDED.data,
                            updated_at = EXCLUDED.updated_at
                    """, (
                        node.id,
                        repo,
                        node.title,
                        node.level.value,
                        node.surface_type.value,
                        node.state.stage.value,
                        node.state.status.value,
                        node.state.confidence,
                        Json({**node.model_dump(), "state": {**node.state.model_dump(), "evidence": scrub_evidence(node.state.evidence)}}),
                        node.created_at,
                        node.updated_at
                    ))

                # 3. Sync Edge Graph
                # Fastest sync: delete all edges for this repo and re-insert the authoritative local list
                cur.execute("DELETE FROM edge_graph WHERE repo = %s", (repo,))
                for up_id, down_id in edges:
                    cur.execute("""
                        INSERT INTO edge_graph (upstream_id, downstream_id, repo)
                        VALUES (%s, %s, %s)
                    """, (up_id, down_id, repo))

            # Transaction commits automatically on exit of `with conn`
        return True
    except Exception as e:
        logger.error(f"Cloud sync failed: {e}")
        return False

def sync_memory_entries(repo: str, entries: List[Dict[str, Any]]) -> int:
    """Upsert local memory entries into cloud Postgres. Returns rows synced."""
    if not psycopg2:
        logger.error("psycopg2 not installed. Cannot sync memories to cloud.")
        return 0

    if not entries:
        return 0

    import datetime
    from ..secret_scrub import scrub_secrets

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    synced = 0

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for entry in entries:
                    tags = entry.get("security_tags")
                    if isinstance(tags, str):
                        try:
                            tags = json.loads(tags)
                        except json.JSONDecodeError:
                            tags = None
                    elif isinstance(tags, list):
                        pass
                    else:
                        tags = None
                    safe_content = scrub_secrets(entry.get("content") or "")
                    safe_yaml = scrub_secrets(entry.get("yaml_definition"))
                    status = entry.get("status") or "active"
                    deleted_at = entry.get("deleted_at")
                    if status in ("deprecated", "deleted") and not deleted_at:
                        deleted_at = now_iso
                    cur.execute(
                        """
                        INSERT INTO memory_entries (
                            memory_id, repo, type, content, scope, confidence,
                            status, security_tags, yaml_definition,
                            created_at, updated_at, expires_at, created_by, node_id,
                            deleted_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (repo, memory_id) DO UPDATE SET
                            type = EXCLUDED.type,
                            content = EXCLUDED.content,
                            scope = EXCLUDED.scope,
                            confidence = EXCLUDED.confidence,
                            status = EXCLUDED.status,
                            security_tags = EXCLUDED.security_tags,
                            yaml_definition = EXCLUDED.yaml_definition,
                            updated_at = EXCLUDED.updated_at,
                            expires_at = EXCLUDED.expires_at,
                            created_by = EXCLUDED.created_by,
                            node_id = EXCLUDED.node_id,
                            deleted_at = EXCLUDED.deleted_at
                        """,
                        (
                            entry["memory_id"],
                            repo,
                            entry["type"],
                            safe_content,
                            entry["scope"],
                            float(entry.get("confidence") or 0.8),
                            status,
                            Json(tags) if tags is not None else None,
                            safe_yaml,
                            entry.get("created_at") or now_iso,
                            now_iso,
                            entry.get("expires_at"),
                            entry.get("created_by"),
                            entry.get("node_id"),
                            deleted_at,
                        ),
                    )
                    synced += 1
        return synced
    except Exception as e:
        logger.error(f"Memory cloud sync failed: {e}")
        return 0


def pull_memory_entries(
    repo: str,
    since_iso: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Pull memory rows from cloud Postgres for bidirectional sync."""
    if not psycopg2:
        logger.error("psycopg2 not installed. Cannot pull memories from cloud.")
        return []

    query = """
        SELECT memory_id, repo, type, content, scope, confidence, status,
               security_tags, yaml_definition, created_at, updated_at,
               expires_at, created_by, node_id, deleted_at
        FROM memory_entries
        WHERE repo = %s
    """
    params: List[Any] = [repo]
    if since_iso:
        query += " AND updated_at > %s"
        params.append(since_iso)
    query += " ORDER BY updated_at ASC"

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                columns = [desc[0] for desc in cur.description]
                rows: List[Dict[str, Any]] = []
                for record in cur.fetchall():
                    row = dict(zip(columns, record))
                    tags = row.get("security_tags")
                    if tags is not None and not isinstance(tags, list):
                        row["security_tags"] = tags
                    for key in ("created_at", "updated_at", "expires_at", "deleted_at"):
                        if row.get(key) is not None:
                            row[key] = row[key].isoformat()
                    rows.append(row)
                return rows
    except Exception as e:
        logger.error(f"Memory cloud pull failed: {e}")
        return []


def sync_memories_bidirectional(
    db: Any,
    repo: str,
) -> Dict[str, Any]:
    """Pull cloud changes (incl. tombstones) then push local state upstream."""
    from datetime import datetime, timezone

    sync_state = db.get_repo_sync_state(repo)
    since = sync_state.get("last_cloud_pull_at")
    pulled = pull_memory_entries(repo, since_iso=since)
    merge_stats = db.apply_cloud_memory_rows(repo, pulled) if pulled else {
        "memories_applied": 0,
        "tombstones_applied": 0,
    }

    entries = [
        e for e in db.list_memory_entries_for_cloud_sync(repo=repo)
        if e.get("status") in ("active", "candidate", "deprecated")
    ]
    pushed = sync_memory_entries(repo, entries)
    now_iso = datetime.now(timezone.utc).isoformat()
    db.set_repo_sync_state(repo, last_cloud_pull_at=now_iso)

    return {
        "memories_pulled": len(pulled),
        "memories_applied": merge_stats.get("memories_applied", 0),
        "tombstones_applied": merge_stats.get("tombstones_applied", 0),
        "memories_pushed": pushed,
    }

# Initialize schema on module load if DB is reachable
try:
    init_postgres()
except Exception:
    pass
