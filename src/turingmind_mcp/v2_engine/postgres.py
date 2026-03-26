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
    import re
    import copy
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    # Secret scrubbing regex to prevent LLM evidence logs from leaking keys to cloud
    SECRET_REGEX = re.compile(r'(?i)(bearer\s+[a-z0-9_\-\.]+)|(sk-[a-zA-Z0-9]{20,})|(AKIA[0-9A-Z]{16})|(xox[baprs]-[0-9a-zA-Z]{10,})')

    def scrub_evidence(evidence_list):
        scrubbed = []
        for item in evidence_list:
            safe_item = copy.deepcopy(item) if isinstance(item, dict) else item
            if isinstance(safe_item, dict) and 'detail' in safe_item and isinstance(safe_item['detail'], str):
                safe_item['detail'] = SECRET_REGEX.sub('[REDACTED_SECRET]', safe_item['detail'])
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

# Initialize schema on module load if DB is reachable
try:
    init_postgres()
except Exception:
    pass
