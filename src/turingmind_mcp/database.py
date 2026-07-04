"""
Database schema and operations for TuringMind MCP memory management.

This module provides database operations for:
- Memory entries (repo facts, learned patterns, explicit rules, session context)
- Evidence tracking
- Conflict detection
- Memory usage tracking
- Approval workflow
"""

from __future__ import annotations

import atexit
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import logging

logger = logging.getLogger("turingmind-mcp")

# Track all database instances for cleanup
_db_instances: List["MemoryDatabase"] = []


def _cleanup_all_databases():
    """Cleanup all database connections on shutdown."""
    for db in _db_instances:
        try:
            db.close()
            logger.debug(f"Closed database connection: {db.db_path}")
        except Exception as e:
            logger.warning(f"Error closing database: {e}")
    _db_instances.clear()


# Register cleanup on interpreter shutdown
atexit.register(_cleanup_all_databases)


class MemoryDatabase:
    """SQLite database for memory management."""

    def __init__(self, db_path: Optional[str] = None):
        """Initialize database connection."""
        if db_path is None:
            # Default to ~/.turingmind/memory.db
            config_dir = Path.home() / ".turingmind"
            config_dir.mkdir(mode=0o700, exist_ok=True)
            db_path = str(config_dir / "memory.db")

        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._closed = False
        
        # Set secure file permissions (read/write for owner only)
        try:
            os.chmod(db_path, 0o600)
        except Exception as e:
            logger.warning(f"Failed to set database file permissions: {e}")
        
        # Enable foreign key constraints
        self.conn.execute("PRAGMA foreign_keys = ON")
        # WAL allows concurrent readers (stdio MCP server, REST API, bridge)
        # against the same database file without SQLITE_BUSY storms.
        self.conn.execute("PRAGMA journal_mode = WAL")

        self._initialize_schema()

        # Expired session context should never surface as active memory
        try:
            expired = self.cleanup_expired_context()
            if expired:
                logger.info(f"Deprecated {expired} expired session context entries")
        except Exception as e:
            logger.warning(f"Expired-context cleanup failed: {e}")

        # Register this instance for cleanup
        _db_instances.append(self)

    def _initialize_schema(self):
        """Create database tables if they don't exist."""
        cursor = self.conn.cursor()

        # Memory Entries Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_entries (
                memory_id TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                scope TEXT NOT NULL,
                confidence REAL DEFAULT 0.8,
                status TEXT NOT NULL DEFAULT 'active',
                security_tags TEXT,
                yaml_definition TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME,
                created_by TEXT,
                node_id TEXT
            )
        """)

        # Migration: add node_id link to SpecNodes for databases created
        # before the column existed.
        existing_cols = {
            row[1] for row in cursor.execute("PRAGMA table_info(memory_entries)").fetchall()
        }
        if "node_id" not in existing_cols:
            cursor.execute("ALTER TABLE memory_entries ADD COLUMN node_id TEXT")
        if "deleted_at" not in existing_cols:
            cursor.execute("ALTER TABLE memory_entries ADD COLUMN deleted_at DATETIME")
        if "branch" not in existing_cols:
            cursor.execute("ALTER TABLE memory_entries ADD COLUMN branch TEXT")
        if "head_sha" not in existing_cols:
            cursor.execute("ALTER TABLE memory_entries ADD COLUMN head_sha TEXT")
        if "git_dirty" not in existing_cols:
            cursor.execute("ALTER TABLE memory_entries ADD COLUMN git_dirty INTEGER DEFAULT 0")
        if "scope_tier" not in existing_cols:
            cursor.execute("ALTER TABLE memory_entries ADD COLUMN scope_tier TEXT DEFAULT 'repo'")
        if "promoted_from" not in existing_cols:
            cursor.execute("ALTER TABLE memory_entries ADD COLUMN promoted_from TEXT")

        # Evidence Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_evidence (
                evidence_id TEXT PRIMARY KEY,
                memory_id TEXT NOT NULL,
                evidence_type TEXT NOT NULL,
                content TEXT NOT NULL,
                file_path TEXT,
                line_number INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (memory_id) REFERENCES memory_entries(memory_id) ON DELETE CASCADE
            )
        """)

        # Conflicts Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_conflicts (
                conflict_id TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                memory_id_1 TEXT NOT NULL,
                memory_id_2 TEXT NOT NULL,
                conflict_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                description TEXT,
                detected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                resolved_at DATETIME,
                resolution_strategy TEXT,
                FOREIGN KEY (memory_id_1) REFERENCES memory_entries(memory_id),
                FOREIGN KEY (memory_id_2) REFERENCES memory_entries(memory_id)
            )
        """)

        # Memory Usage Tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_usage (
                usage_id TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                context TEXT NOT NULL,
                issue_id TEXT,
                file_path TEXT,
                line_number INTEGER,
                weight REAL,
                used_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (memory_id) REFERENCES memory_entries(memory_id)
            )
        """)

        # Approvals Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_approvals (
                approval_id TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                approved_by TEXT,
                approved_at DATETIME,
                status TEXT NOT NULL DEFAULT 'pending',
                FOREIGN KEY (memory_id) REFERENCES memory_entries(memory_id)
            )
        """)

        # Code Entities Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS code_entities (
                entity_id TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                file_path TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                name TEXT NOT NULL,
                start_line INTEGER,
                end_line INTEGER,
                language TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(repo, file_path, name, entity_type)
            )
        """)

        # Code Relationships Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS code_relationships (
                relationship_id TEXT PRIMARY KEY,
                source_entity_id TEXT NOT NULL,
                target_entity_id TEXT,
                target_symbol_name TEXT,
                relationship_type TEXT NOT NULL,
                repo TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_entity_id) REFERENCES code_entities(entity_id),
                FOREIGN KEY (target_entity_id) REFERENCES code_entities(entity_id)
            )
        """)

        # Git Commits Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS git_commits (
                commit_hash TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                branch TEXT,
                changed_files TEXT,
                last_reviewed_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Edit Reasoning Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS edit_reasoning (
                reasoning_id TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                commit_hash TEXT,
                files TEXT,
                reasoning TEXT,
                change_type TEXT,
                memory_category TEXT,
                scope TEXT,
                confidence REAL,
                source TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Observations Table — draft beliefs captured by hooks/plugins.
        # These are hypotheses, not truth: reconciliation passes (or an
        # explicit accept) promote them into memory_entries; until then they
        # never surface in recall.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                observation_id TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                event_type TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT,
                confidence REAL DEFAULT 0.3,
                evidence TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                node_id TEXT,
                memory_id TEXT,
                observed_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                reconciled_at DATETIME
            )
        """)

        obs_cols = {
            row[1] for row in cursor.execute("PRAGMA table_info(observations)").fetchall()
        }
        if "branch" not in obs_cols:
            cursor.execute("ALTER TABLE observations ADD COLUMN branch TEXT")
        if "head_sha" not in obs_cols:
            cursor.execute("ALTER TABLE observations ADD COLUMN head_sha TEXT")
        if "git_dirty" not in obs_cols:
            cursor.execute("ALTER TABLE observations ADD COLUMN git_dirty INTEGER DEFAULT 0")
        if "git_context" not in obs_cols:
            cursor.execute("ALTER TABLE observations ADD COLUMN git_context TEXT")

        # Reconciliation findings — actionable proposals produced by the
        # deterministic passes (promotion candidates, stale memories,
        # conflicts, ungoverned files). Surfaced through the decision queue;
        # dedup_key prevents the same proposal from piling up every cycle.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reconcile_findings (
                finding_id TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                finding_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'medium',
                action TEXT NOT NULL,
                evidence TEXT,
                memory_id TEXT,
                node_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                dedup_key TEXT NOT NULL,
                run_id TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                resolved_at DATETIME,
                UNIQUE(repo, dedup_key)
            )
        """)

        # Reconciliation run stats — the gradient observability counter:
        # a clogged promotion funnel and a healthy one look different here.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reconcile_runs (
                run_id TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                stats TEXT NOT NULL,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                finished_at DATETIME
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS repo_sync_state (
                repo TEXT PRIMARY KEY,
                last_git_head TEXT,
                last_cloud_pull_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS branch_git_cursors (
                repo TEXT NOT NULL,
                branch TEXT NOT NULL,
                last_git_head TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (repo, branch)
            )
        """)

        # Chat capture cursor — one row per Cursor composerId.
        # Tracks what we already ingested so check_exchanges() can debounce
        # without re-reading the full Cursor DB every poll cycle.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_capture_state (
                composer_id TEXT PRIMARY KEY,
                message_count INTEGER DEFAULT 0,
                last_captured_at INTEGER,
                last_llm_enhanced_at INTEGER,
                last_llm_processed_prompt_index INTEGER,
                last_llm_processed_response_index INTEGER,
                last_captured_exchange_count INTEGER,
                last_exchange_timestamp INTEGER,
                processed_files_json TEXT,
                kanban_item_hashes_json TEXT,
                last_observation_bubble_count INTEGER,
                last_observation_at INTEGER,
                last_observation_exchange_timestamp INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        existing_cols = {
            row[1] for row in cursor.execute("PRAGMA table_info(chat_capture_state)").fetchall()
        }
        for col, ddl in (
            ("last_observation_bubble_count", "INTEGER"),
            ("last_observation_at", "INTEGER"),
            ("last_observation_exchange_timestamp", "INTEGER"),
        ):
            if col not in existing_cols:
                cursor.execute(f"ALTER TABLE chat_capture_state ADD COLUMN {col} {ddl}")

        # Tier D: deterministic embeddings for duplicate-merge suggestions.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_embeddings (
                memory_id TEXT PRIMARY KEY,
                embedding BLOB NOT NULL,
                method TEXT NOT NULL DEFAULT 'hash_bow_v1',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (memory_id) REFERENCES memory_entries(memory_id) ON DELETE CASCADE
            )
        """)

        # Create indexes
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_findings_repo_status ON reconcile_findings(repo, status)",
            "CREATE INDEX IF NOT EXISTS idx_reconcile_runs_repo ON reconcile_runs(repo, started_at)",
            "CREATE INDEX IF NOT EXISTS idx_observations_repo_status ON observations(repo, status)",
            "CREATE INDEX IF NOT EXISTS idx_observations_repo_event ON observations(repo, event_type)",
            "CREATE INDEX IF NOT EXISTS idx_chat_capture_composer ON chat_capture_state(composer_id)",
            "CREATE INDEX IF NOT EXISTS idx_memory_embeddings_method ON memory_embeddings(method)",
            "CREATE INDEX IF NOT EXISTS idx_memory_repo_type ON memory_entries(repo, type)",
            "CREATE INDEX IF NOT EXISTS idx_memory_repo_status ON memory_entries(repo, status)",
            "CREATE INDEX IF NOT EXISTS idx_memory_repo_scope ON memory_entries(repo, scope)",
            "CREATE INDEX IF NOT EXISTS idx_memory_repo_branch ON memory_entries(repo, branch)",
            "CREATE INDEX IF NOT EXISTS idx_memory_expires_at ON memory_entries(expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_evidence_memory_id ON memory_evidence(memory_id)",
            "CREATE INDEX IF NOT EXISTS idx_conflicts_repo ON memory_conflicts(repo)",
            "CREATE INDEX IF NOT EXISTS idx_conflicts_unresolved ON memory_conflicts(repo, resolved_at)",
            "CREATE INDEX IF NOT EXISTS idx_usage_memory_id ON memory_usage(memory_id)",
            "CREATE INDEX IF NOT EXISTS idx_usage_context ON memory_usage(repo, context)",
            "CREATE INDEX IF NOT EXISTS idx_approvals_repo_status ON memory_approvals(repo, status)",
            "CREATE INDEX IF NOT EXISTS idx_entities_repo_file ON code_entities(repo, file_path)",
            "CREATE INDEX IF NOT EXISTS idx_entities_name ON code_entities(name)",
            "CREATE INDEX IF NOT EXISTS idx_relationships_source ON code_relationships(source_entity_id)",
            "CREATE INDEX IF NOT EXISTS idx_relationships_target ON code_relationships(target_entity_id)",
            "CREATE INDEX IF NOT EXISTS idx_git_commits_repo ON git_commits(repo)",
            "CREATE INDEX IF NOT EXISTS idx_reasoning_repo_commit ON edit_reasoning(repo, commit_hash)",
        ]

        for index_sql in indexes:
            cursor.execute(index_sql)

        self.conn.commit()
        self._fts_enabled = self._initialize_fts()
        logger.info(f"Database schema initialized at {self.db_path}")

    def _initialize_fts(self) -> bool:
        """Create the FTS5 full-text index over memory content.

        Uses an external-content FTS5 table kept in sync with `memory_entries`
        via triggers. Returns False when the sqlite build lacks FTS5, in which
        case search falls back to LIKE.
        """
        try:
            cursor = self.conn.cursor()
            existed = cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_fts'"
            ).fetchone()

            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    content,
                    content='memory_entries',
                    content_rowid='rowid'
                )
            """)
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS memory_fts_ai AFTER INSERT ON memory_entries BEGIN
                    INSERT INTO memory_fts(rowid, content) VALUES (new.rowid, new.content);
                END
            """)
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS memory_fts_ad AFTER DELETE ON memory_entries BEGIN
                    INSERT INTO memory_fts(memory_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
                END
            """)
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS memory_fts_au AFTER UPDATE OF content ON memory_entries BEGIN
                    INSERT INTO memory_fts(memory_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
                    INSERT INTO memory_fts(rowid, content) VALUES (new.rowid, new.content);
                END
            """)

            # Backfill entries created before the FTS table existed
            if not existed:
                cursor.execute("INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')")

            self.conn.commit()
            return True
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS5 unavailable, memory search falls back to LIKE: {e}")
            self.conn.rollback()
            return False

    @staticmethod
    def _fts_query(search: str) -> str:
        """Convert a free-text search string into a safe FTS5 MATCH query.

        Each whitespace token is double-quoted so user input can't inject
        FTS syntax; tokens are OR-ed for recall over precision.
        """
        tokens = [t.replace('"', '""') for t in search.split() if t.strip()]
        return " OR ".join(f'"{t}"' for t in tokens)

    def close(self):
        """Close database connection."""
        if not self._closed:
            self.conn.close()
            self._closed = True
            # Remove from tracked instances
            if self in _db_instances:
                _db_instances.remove(self)

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        """
        Context manager for transactional operations.
        
        Commits on success, rolls back on exception.
        
        Usage:
            with db.transaction() as cursor:
                cursor.execute(...)
                cursor.execute(...)
            # Auto-commits if no exception
        """
        cursor = self.conn.cursor()
        try:
            yield cursor
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # Memory Entry Operations
    def create_memory_entry(
        self,
        repo: str,
        memory_type: str,
        content: str,
        scope: str,
        confidence: float = 0.8,
        status: str = "active",
        security_tags: Optional[List[str]] = None,
        yaml_definition: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        created_by: Optional[str] = None,
        node_id: Optional[str] = None,
        branch: Optional[str] = None,
        head_sha: Optional[str] = None,
        git_dirty: int = 0,
        scope_tier: str = "repo",
        promoted_from: Optional[str] = None,
    ) -> str:
        """Create a new memory entry, optionally linked to a SpecNode."""
        memory_id = str(uuid.uuid4())
        security_tags_json = json.dumps(security_tags) if security_tags else None
        
        # Clamp confidence to valid range [0.0, 1.0]
        confidence = max(0.0, min(1.0, confidence))

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO memory_entries (
                memory_id, repo, type, content, scope, confidence,
                status, security_tags, yaml_definition, expires_at, created_by, node_id,
                branch, head_sha, git_dirty, scope_tier, promoted_from
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                repo,
                memory_type,
                content,
                scope,
                confidence,
                status,
                security_tags_json,
                yaml_definition,
                expires_at.isoformat() if expires_at else None,
                created_by,
                node_id,
                branch,
                head_sha,
                git_dirty,
                scope_tier,
                promoted_from,
            ),
        )
        self.conn.commit()
        return memory_id

    def get_memory_entry(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a memory entry by ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM memory_entries WHERE memory_id = ?", (memory_id,))
        row = cursor.fetchone()
        if not row:
            return None

        result = dict(row)
        if result.get("security_tags"):
            result["security_tags"] = json.loads(result["security_tags"])
        return result

    def upsert_memory_embedding(
        self,
        memory_id: str,
        embedding: bytes,
        method: str = "hash_bow_v1",
    ) -> None:
        """Store or refresh the embedding vector for a memory entry."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO memory_embeddings (memory_id, embedding, method, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(memory_id) DO UPDATE SET
                embedding = excluded.embedding,
                method = excluded.method,
                updated_at = CURRENT_TIMESTAMP
            """,
            (memory_id, embedding, method),
        )
        self.conn.commit()

    def list_memory_embeddings(self, repo: str) -> List[Dict[str, Any]]:
        """Return memory_id + embedding blob for active/candidate entries in repo."""
        cursor = self.conn.cursor()
        rows = cursor.execute(
            """
            SELECT e.memory_id, e.embedding, e.method, m.content, m.type, m.status
            FROM memory_embeddings e
            JOIN memory_entries m ON m.memory_id = e.memory_id
            WHERE m.repo = ?
              AND m.status IN ('active', 'candidate')
              AND m.type IN ('learned_pattern', 'explicit_rule', 'repo_fact')
            """,
            (repo,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_memory_entries(
        self,
        repo: str,
        memory_type: Optional[str] = None,
        status: Optional[str] = None,
        scope: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
        search: Optional[str] = None,
        branch: Optional[str] = None,
        include_other_branches: bool = False,
    ) -> List[Dict[str, Any]]:
        """List memory entries with filtering.

        When `search` is given and FTS5 is available, results are matched via
        full-text search and ranked by BM25 relevance weighted by confidence.
        Otherwise falls back to a LIKE substring match ordered by recency.
        """
        cursor = self.conn.cursor()
        offset = (page - 1) * limit

        use_fts = bool(search) and getattr(self, "_fts_enabled", False)

        if use_fts:
            query = """
                SELECT m.* FROM memory_entries m
                JOIN memory_fts f ON f.rowid = m.rowid
                WHERE memory_fts MATCH ? AND m.repo = ?
            """
            params: List[Any] = [self._fts_query(search), repo]
        else:
            query = "SELECT * FROM memory_entries WHERE repo = ?"
            params = [repo]

        prefix = "m." if use_fts else ""

        if memory_type and memory_type != "all":
            query += f" AND {prefix}type = ?"
            params.append(memory_type)

        if status and status != "all":
            query += f" AND {prefix}status = ?"
            params.append(status)

        if scope:
            query += f" AND ({prefix}scope = ? OR {prefix}scope = 'repo')"
            params.append(scope)

        from .git_context import branch_memory_ranking_enabled

        if branch_memory_ranking_enabled() and branch and not include_other_branches:
            query += f" AND ({prefix}branch IS NULL OR {prefix}branch = ?)"
            params.append(branch)

        if status == "active":
            query += f" AND ({prefix}expires_at IS NULL OR {prefix}expires_at > datetime('now'))"

        if use_fts:
            # bm25() is lower-is-better; dividing by confidence promotes
            # trusted memories without letting low-relevance ones win.
            query += " ORDER BY bm25(memory_fts) / MAX(m.confidence, 0.05) ASC LIMIT ? OFFSET ?"
        else:
            if search:
                query += " AND content LIKE ?"
                params.append(f"%{search}%")
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        try:
            cursor.execute(query, params)
        except sqlite3.OperationalError:
            if not use_fts:
                raise
            # Malformed FTS query — degrade to LIKE rather than failing the tool
            logger.warning(f"FTS query failed for {search!r}; falling back to LIKE")
            return self._list_memory_entries_like(
                repo, memory_type, status, scope, page, limit, search
            )
        rows = cursor.fetchall()

        results = []
        for row in rows:
            result = dict(row)
            if result.get("security_tags"):
                result["security_tags"] = json.loads(result["security_tags"])
            results.append(result)

        return results

    def list_memory_entries_for_recall(
        self,
        repo: str,
        recall_branch: Optional[str],
        recall_head: Optional[str],
        *,
        include_other_branches: bool = False,
        detached: bool = False,
        status: str = "active",
        exclude_types: Optional[List[str]] = None,
        limit: int = 50,
        internal_limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Branch-filtered recall query — branch predicate and ORDER BY before LIMIT (SPEC-BR-04)."""
        cursor = self.conn.cursor()
        query = """
            SELECT * FROM memory_entries
            WHERE repo = ?
              AND status = ?
              AND deleted_at IS NULL
        """
        params: List[Any] = [repo, status]

        if status == "active":
            query += " AND (expires_at IS NULL OR expires_at > datetime('now'))"

        if exclude_types:
            placeholders = ",".join("?" for _ in exclude_types)
            query += f" AND type NOT IN ({placeholders})"
            params.extend(exclude_types)

        if recall_branch is not None or recall_head is not None:
            if not include_other_branches:
                if detached and recall_head:
                    query += """
                      AND (
                        branch IS NULL
                        OR head_sha = ?
                      )
                    """
                    params.append(recall_head)
                elif recall_branch is not None:
                    query += """
                      AND (
                        branch IS NULL
                        OR branch = ?
                      )
                    """
                    params.append(recall_branch)
            # include_other_branches: no SQL branch filter — rank/filter in Python

        if detached and recall_head:
            query += """
                ORDER BY
                  CASE
                    WHEN branch IS NULL THEN 3
                    WHEN head_sha = ? AND git_dirty = 1 THEN 0
                    WHEN head_sha = ? THEN 1
                    ELSE 4
                  END,
                  created_at DESC
                LIMIT ?
            """
            params.extend([recall_head, recall_head, internal_limit])
        elif recall_branch is not None:
            query += """
                ORDER BY
                  CASE
                    WHEN branch IS NULL THEN 3
                    WHEN branch = ? AND git_dirty = 1 THEN 0
                    WHEN branch = ? THEN 1
                    ELSE 4
                  END,
                  created_at DESC
                LIMIT ?
            """
            params.extend([recall_branch, recall_branch, internal_limit])
        else:
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(internal_limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        results = []
        for row in rows:
            result = dict(row)
            if result.get("security_tags"):
                result["security_tags"] = json.loads(result["security_tags"])
            results.append(result)
        return results

    def _list_memory_entries_like(
        self,
        repo: str,
        memory_type: Optional[str],
        status: Optional[str],
        scope: Optional[str],
        page: int,
        limit: int,
        search: Optional[str],
    ) -> List[Dict[str, Any]]:
        """LIKE-based fallback used when an FTS MATCH query is malformed."""
        cursor = self.conn.cursor()
        query = "SELECT * FROM memory_entries WHERE repo = ?"
        params: List[Any] = [repo]
        if memory_type and memory_type != "all":
            query += " AND type = ?"
            params.append(memory_type)
        if status and status != "all":
            query += " AND status = ?"
            params.append(status)
        if scope:
            query += " AND (scope = ? OR scope = 'repo')"
            params.append(scope)
        if status == "active":
            query += " AND (expires_at IS NULL OR expires_at > datetime('now'))"
        if search:
            query += " AND content LIKE ?"
            params.append(f"%{search}%")
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, (page - 1) * limit])
        cursor.execute(query, params)
        results = []
        for row in cursor.fetchall():
            result = dict(row)
            if result.get("security_tags"):
                result["security_tags"] = json.loads(result["security_tags"])
            results.append(result)
        return results

    # Chat capture state — debounce cursors for Cursor composer polling
    def get_chat_capture_state(self, composer_id: str) -> Optional[Dict[str, Any]]:
        """Return capture cursor for a composer, or None if never seen.

        Keys are camelCase to match the bridge/extension contract documented
        in chat_capture.py (messageCount, lastCapturedAt, processedFiles, …).
        """
        cursor = self.conn.cursor()
        row = cursor.execute(
            "SELECT * FROM chat_capture_state WHERE composer_id = ?",
            (composer_id,),
        ).fetchone()
        if not row:
            return None

        def _load_set(raw: Optional[str]) -> set:
            if not raw:
                return set()
            try:
                data = json.loads(raw)
                return set(data) if isinstance(data, list) else set()
            except (json.JSONDecodeError, TypeError):
                return set()

        return {
            "composerId": row["composer_id"],
            "messageCount": row["message_count"] or 0,
            "lastCapturedAt": row["last_captured_at"] or 0,
            "lastLLMEnhancedAt": row["last_llm_enhanced_at"] or 0,
            "lastLLMProcessedPromptIndex": row["last_llm_processed_prompt_index"] or 0,
            "lastLLMProcessedResponseIndex": row["last_llm_processed_response_index"] or 0,
            "lastCapturedExchangeCount": row["last_captured_exchange_count"] or 0,
            "lastExchangeTimestamp": row["last_exchange_timestamp"] or 0,
            "processedFiles": _load_set(row["processed_files_json"]),
            "kanbanItemHashes": _load_set(row["kanban_item_hashes_json"]),
            "lastObservationBubbleCount": row["last_observation_bubble_count"] or 0,
            "lastObservationAt": row["last_observation_at"] or 0,
            "lastObservationExchangeTimestamp": row["last_observation_exchange_timestamp"] or 0,
        }

    def update_chat_capture_state(
        self,
        composer_id: str,
        *,
        message_count: Optional[int] = None,
        last_captured_at: Optional[int] = None,
        last_llm_enhanced_at: Optional[int] = None,
        last_llm_processed_prompt_index: Optional[int] = None,
        last_llm_processed_response_index: Optional[int] = None,
        last_captured_exchange_count: Optional[int] = None,
        last_exchange_timestamp: Optional[int] = None,
        processed_files: Optional[set] = None,
        kanban_item_hashes: Optional[set] = None,
        last_observation_bubble_count: Optional[int] = None,
        last_observation_at: Optional[int] = None,
        last_observation_exchange_timestamp: Optional[int] = None,
    ) -> bool:
        """Upsert capture cursor. Set fields merge for processed_files / kanban hashes."""
        existing = self.get_chat_capture_state(composer_id)
        now = datetime.utcnow().isoformat()

        merged_files = set(existing["processedFiles"]) if existing else set()
        if processed_files:
            merged_files.update(processed_files)

        merged_hashes = set(existing["kanbanItemHashes"]) if existing else set()
        if kanban_item_hashes:
            merged_hashes.update(kanban_item_hashes)

        if existing:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                UPDATE chat_capture_state SET
                    message_count = COALESCE(?, message_count),
                    last_captured_at = COALESCE(?, last_captured_at),
                    last_llm_enhanced_at = COALESCE(?, last_llm_enhanced_at),
                    last_llm_processed_prompt_index = COALESCE(?, last_llm_processed_prompt_index),
                    last_llm_processed_response_index = COALESCE(?, last_llm_processed_response_index),
                    last_captured_exchange_count = COALESCE(?, last_captured_exchange_count),
                    last_exchange_timestamp = COALESCE(?, last_exchange_timestamp),
                    processed_files_json = ?,
                    kanban_item_hashes_json = ?,
                    last_observation_bubble_count = COALESCE(?, last_observation_bubble_count),
                    last_observation_at = COALESCE(?, last_observation_at),
                    last_observation_exchange_timestamp = COALESCE(?, last_observation_exchange_timestamp),
                    updated_at = ?
                WHERE composer_id = ?
                """,
                (
                    message_count,
                    last_captured_at,
                    last_llm_enhanced_at,
                    last_llm_processed_prompt_index,
                    last_llm_processed_response_index,
                    last_captured_exchange_count,
                    last_exchange_timestamp,
                    json.dumps(sorted(merged_files)),
                    json.dumps(sorted(merged_hashes)),
                    last_observation_bubble_count,
                    last_observation_at,
                    last_observation_exchange_timestamp,
                    now,
                    composer_id,
                ),
            )
        else:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO chat_capture_state (
                    composer_id, message_count, last_captured_at,
                    last_llm_enhanced_at, last_llm_processed_prompt_index,
                    last_llm_processed_response_index, last_captured_exchange_count,
                    last_exchange_timestamp, processed_files_json,
                    kanban_item_hashes_json,
                    last_observation_bubble_count, last_observation_at,
                    last_observation_exchange_timestamp, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    composer_id,
                    message_count or 0,
                    last_captured_at,
                    last_llm_enhanced_at,
                    last_llm_processed_prompt_index,
                    last_llm_processed_response_index,
                    last_captured_exchange_count,
                    last_exchange_timestamp,
                    json.dumps(sorted(merged_files)),
                    json.dumps(sorted(merged_hashes)),
                    last_observation_bubble_count or 0,
                    last_observation_at,
                    last_observation_exchange_timestamp,
                    now,
                ),
            )
        self.conn.commit()
        return True

    # Observation Operations (draft beliefs — see reconciliation design)
    def create_observation(
        self,
        repo: str,
        event_type: str,
        content: str,
        source: Optional[str] = None,
        confidence: float = 0.3,
        evidence: Optional[List[Dict[str, Any]]] = None,
        node_id: Optional[str] = None,
        observed_at: Optional[str] = None,
        branch: Optional[str] = None,
        head_sha: Optional[str] = None,
        git_dirty: int = 0,
        git_context: Optional[str] = None,
    ) -> str:
        """Record a draft observation. Never surfaces in memory recall until
        a reconciliation pass (or explicit accept) promotes it."""
        observation_id = str(uuid.uuid4())
        confidence = max(0.0, min(1.0, confidence))
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO observations (
                observation_id, repo, event_type, content, source,
                confidence, evidence, node_id, observed_at,
                branch, head_sha, git_dirty, git_context
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation_id,
                repo,
                event_type,
                content,
                source,
                confidence,
                json.dumps(evidence) if evidence else None,
                node_id,
                observed_at,
                branch,
                head_sha,
                git_dirty,
                git_context,
            ),
        )
        self.conn.commit()
        return observation_id

    def list_observations(
        self,
        repo: str,
        status: Optional[str] = "pending",
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List observations, defaulting to those awaiting reconciliation."""
        cursor = self.conn.cursor()
        query = "SELECT * FROM observations WHERE repo = ?"
        params: List[Any] = [repo]
        if status and status != "all":
            query += " AND status = ?"
            params.append(status)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        results = []
        for row in cursor.fetchall():
            result = dict(row)
            if result.get("evidence"):
                try:
                    result["evidence"] = json.loads(result["evidence"])
                except (json.JSONDecodeError, TypeError):
                    pass
            results.append(result)
        return results

    def resolve_observation(
        self,
        observation_id: str,
        status: str,
        memory_id: Optional[str] = None,
    ) -> bool:
        """Mark an observation accepted/rejected, optionally linking the
        memory entry it was promoted into."""
        if status not in ("accepted", "rejected"):
            raise ValueError(f"Invalid observation status: {status}")
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE observations
            SET status = ?, memory_id = ?, reconciled_at = CURRENT_TIMESTAMP
            WHERE observation_id = ?
            """,
            (status, memory_id, observation_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # Reconciliation Findings / Runs
    def create_finding(
        self,
        repo: str,
        finding_type: str,
        severity: str,
        action: str,
        dedup_key: str,
        evidence: Optional[List[Dict[str, Any]]] = None,
        memory_id: Optional[str] = None,
        node_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Optional[str]:
        """Record a reconciliation finding. Returns None when a pending finding
        with the same dedup_key already exists (proposal already on the queue)."""
        finding_id = str(uuid.uuid4())
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO reconcile_findings (
                finding_id, repo, finding_type, severity, action,
                evidence, memory_id, node_id, dedup_key, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                finding_id,
                repo,
                finding_type,
                severity,
                action,
                json.dumps(evidence) if evidence else None,
                memory_id,
                node_id,
                dedup_key,
                run_id,
            ),
        )
        self.conn.commit()
        return finding_id if cursor.rowcount > 0 else None

    def list_findings(
        self,
        repo: str,
        status: Optional[str] = "pending",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List reconciliation findings, pending (queue-visible) by default."""
        cursor = self.conn.cursor()
        query = "SELECT * FROM reconcile_findings WHERE repo = ?"
        params: List[Any] = [repo]
        if status and status != "all":
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        results = []
        for row in cursor.fetchall():
            result = dict(row)
            if result.get("evidence"):
                try:
                    result["evidence"] = json.loads(result["evidence"])
                except (json.JSONDecodeError, TypeError):
                    pass
            results.append(result)
        return results

    def get_finding(self, finding_id: str) -> Optional[Dict[str, Any]]:
        """Return a reconciliation finding by ID."""
        row = self.conn.execute(
            "SELECT * FROM reconcile_findings WHERE finding_id = ?",
            (finding_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        if result.get("evidence"):
            try:
                result["evidence"] = json.loads(result["evidence"])
            except (json.JSONDecodeError, TypeError):
                pass
        return result

    def resolve_finding(self, finding_id: str, status: str) -> bool:
        """Mark a finding actioned or dismissed."""
        if status not in ("actioned", "dismissed"):
            raise ValueError(f"Invalid finding status: {status}")
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE reconcile_findings
            SET status = ?, resolved_at = CURRENT_TIMESTAMP
            WHERE finding_id = ?
            """,
            (status, finding_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_branch_git_cursor(self, repo: str, branch: str) -> Optional[str]:
        """Return last reconciled HEAD for a repo branch (SPEC-BR-03)."""
        row = self.conn.execute(
            "SELECT last_git_head FROM branch_git_cursors WHERE repo = ? AND branch = ?",
            (repo, branch),
        ).fetchone()
        if not row:
            return None
        return row["last_git_head"]

    def set_branch_git_cursor(
        self, repo: str, branch: str, last_git_head: Optional[str]
    ) -> None:
        """Upsert per-branch git HEAD cursor (SPEC-BR-03)."""
        self.conn.execute(
            """
            INSERT INTO branch_git_cursors (repo, branch, last_git_head, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(repo, branch) DO UPDATE SET
                last_git_head = excluded.last_git_head,
                updated_at = CURRENT_TIMESTAMP
            """,
            (repo, branch, last_git_head),
        )
        self.conn.commit()

    def record_reconcile_run(self, repo: str, stats: Dict[str, Any]) -> str:
        """Persist per-cycle reconciliation stats (gradient observability)."""
        run_id = str(uuid.uuid4())
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO reconcile_runs (run_id, repo, stats, finished_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (run_id, repo, json.dumps(stats)),
        )
        self.conn.commit()
        return run_id

    def get_repo_sync_state(self, repo: str) -> Dict[str, Any]:
        """Return persisted git/cloud sync cursors for a repo."""
        row = self.conn.execute(
            "SELECT repo, last_git_head, last_cloud_pull_at FROM repo_sync_state WHERE repo = ?",
            (repo,),
        ).fetchone()
        if not row:
            return {"repo": repo, "last_git_head": None, "last_cloud_pull_at": None}
        return dict(row)

    def set_repo_sync_state(
        self,
        repo: str,
        *,
        last_git_head: Optional[str] = None,
        last_cloud_pull_at: Optional[str] = None,
    ) -> None:
        """Upsert git/cloud sync cursors."""
        current = self.get_repo_sync_state(repo)
        head = last_git_head if last_git_head is not None else current.get("last_git_head")
        pulled = (
            last_cloud_pull_at
            if last_cloud_pull_at is not None
            else current.get("last_cloud_pull_at")
        )
        self.conn.execute(
            """
            INSERT INTO repo_sync_state (repo, last_git_head, last_cloud_pull_at)
            VALUES (?, ?, ?)
            ON CONFLICT(repo) DO UPDATE SET
                last_git_head = excluded.last_git_head,
                last_cloud_pull_at = excluded.last_cloud_pull_at
            """,
            (repo, head, pulled),
        )
        self.conn.commit()

    def list_memory_entries_for_cloud_sync(
        self,
        repo: str,
        limit: int = 5000,
    ) -> List[Dict[str, Any]]:
        """Memories to push upstream, including tombstoned (deprecated) rows."""
        return self.list_memory_entries(
            repo=repo,
            status="all",
            limit=limit,
        )

    @staticmethod
    def _parse_db_timestamp(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace(" ", "T"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except (ValueError, TypeError):
            return None

    def upsert_memory_from_cloud(self, row: Dict[str, Any]) -> bool:
        """Apply a cloud memory row locally (last-write-wins on updated_at)."""
        memory_id = row.get("memory_id")
        repo = row.get("repo")
        if not memory_id or not repo:
            return False

        local = self.get_memory_entry(memory_id)
        cloud_updated = self._parse_db_timestamp(row.get("updated_at"))
        cloud_deleted = self._parse_db_timestamp(row.get("deleted_at"))
        status = (row.get("status") or "active").lower()
        is_tombstone = status in ("deprecated", "deleted") or cloud_deleted is not None

        if local and not is_tombstone:
            local_updated = self._parse_db_timestamp(local.get("updated_at"))
            if local_updated and cloud_updated and local_updated >= cloud_updated:
                return False

        branch_fields = {
            "branch": row.get("branch"),
            "head_sha": row.get("head_sha"),
            "git_dirty": int(row.get("git_dirty") or 0),
            "scope_tier": row.get("scope_tier") or ("repo" if not row.get("branch") else "branch"),
            "promoted_from": row.get("promoted_from"),
        }

        if local:
            applied = self.update_memory_entry(
                memory_id,
                content=row.get("content"),
                scope=row.get("scope"),
                confidence=float(row.get("confidence") or local.get("confidence") or 0.8),
                status=status,
                yaml_definition=row.get("yaml_definition"),
            )
            cursor = self.conn.cursor()
            cursor.execute(
                """
                UPDATE memory_entries SET
                    branch = ?, head_sha = ?, git_dirty = ?,
                    scope_tier = ?, promoted_from = ?
                WHERE memory_id = ?
                """,
                (
                    branch_fields["branch"],
                    branch_fields["head_sha"],
                    branch_fields["git_dirty"],
                    branch_fields["scope_tier"],
                    branch_fields["promoted_from"],
                    memory_id,
                ),
            )
            self.conn.commit()
            if applied and is_tombstone and row.get("deleted_at"):
                cursor.execute(
                    "UPDATE memory_entries SET deleted_at = ? WHERE memory_id = ?",
                    (row.get("deleted_at"), memory_id),
                )
                self.conn.commit()
            return applied or cursor.rowcount > 0

        cursor = self.conn.cursor()
        tags = row.get("security_tags")
        if isinstance(tags, list):
            tags_json = json.dumps(tags)
        elif isinstance(tags, str):
            tags_json = tags
        else:
            tags_json = None

        cursor.execute(
            """
            INSERT INTO memory_entries (
                memory_id, repo, type, content, scope, confidence,
                status, security_tags, yaml_definition, expires_at,
                created_by, node_id, created_at, updated_at, deleted_at,
                branch, head_sha, git_dirty, scope_tier, promoted_from
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                repo,
                row.get("type") or "learned_pattern",
                row.get("content") or "",
                row.get("scope") or "repo",
                float(row.get("confidence") or 0.8),
                status,
                tags_json,
                row.get("yaml_definition"),
                row.get("expires_at"),
                row.get("created_by"),
                row.get("node_id"),
                row.get("created_at"),
                row.get("updated_at"),
                row.get("deleted_at"),
                branch_fields["branch"],
                branch_fields["head_sha"],
                branch_fields["git_dirty"],
                branch_fields["scope_tier"],
                branch_fields["promoted_from"],
            ),
        )
        self.conn.commit()
        return True

    def apply_cloud_memory_rows(
        self,
        repo: str,
        rows: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        """Merge pulled cloud memories; propagate tombstones (deprecated/deleted)."""
        applied = 0
        tombstoned = 0
        for row in rows:
            if row.get("repo") != repo:
                continue
            status = (row.get("status") or "active").lower()
            before = self.get_memory_entry(row["memory_id"])
            before_active = before and before.get("status") == "active"
            if not self.upsert_memory_from_cloud(row):
                continue
            if status in ("deprecated", "deleted") and before_active:
                tombstoned += 1
            else:
                applied += 1
        return {"memories_applied": applied, "tombstones_applied": tombstoned}

    def update_memory_entry(
        self,
        memory_id: str,
        content: Optional[str] = None,
        scope: Optional[str] = None,
        confidence: Optional[float] = None,
        status: Optional[str] = None,
        security_tags: Optional[List[str]] = None,
        yaml_definition: Optional[str] = None,
    ) -> bool:
        """Update a memory entry."""
        cursor = self.conn.cursor()

        updates = []
        params = []

        if content is not None:
            updates.append("content = ?")
            params.append(content)

        if scope is not None:
            updates.append("scope = ?")
            params.append(scope)

        if confidence is not None:
            updates.append("confidence = ?")
            params.append(confidence)

        if status is not None:
            updates.append("status = ?")
            params.append(status)
            if status in ("deprecated", "deleted"):
                updates.append("deleted_at = CURRENT_TIMESTAMP")
            elif status == "active":
                updates.append("deleted_at = NULL")

        if security_tags is not None:
            updates.append("security_tags = ?")
            params.append(json.dumps(security_tags))

        if yaml_definition is not None:
            updates.append("yaml_definition = ?")
            params.append(yaml_definition)

        if not updates:
            return False

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(memory_id)

        query = f"UPDATE memory_entries SET {', '.join(updates)} WHERE memory_id = ?"
        cursor.execute(query, params)
        self.conn.commit()
        return cursor.rowcount > 0

    def delete_memory_entry(self, memory_id: str, deprecate: bool = True) -> bool:
        """Delete or deprecate a memory entry (deprecated rows sync as tombstones)."""
        if deprecate:
            return self.update_memory_entry(memory_id, status="deprecated")

        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM memory_entries WHERE memory_id = ?", (memory_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    # Evidence Operations
    def add_evidence(
        self,
        memory_id: str,
        evidence_type: str,
        content: str,
        file_path: Optional[str] = None,
        line_number: Optional[int] = None,
    ) -> str:
        """Add evidence to a memory entry."""
        evidence_id = str(uuid.uuid4())
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO memory_evidence (
                evidence_id, memory_id, evidence_type, content, file_path, line_number
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (evidence_id, memory_id, evidence_type, content, file_path, line_number),
        )
        self.conn.commit()
        return evidence_id

    def get_evidence(self, memory_id: str) -> List[Dict[str, Any]]:
        """Get all evidence for a memory entry."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM memory_evidence WHERE memory_id = ? ORDER BY created_at DESC",
            (memory_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    # Conflict Operations
    def create_conflict(
        self,
        repo: str,
        memory_id_1: str,
        memory_id_2: str,
        conflict_type: str,
        severity: str,
        description: Optional[str] = None,
    ) -> str:
        """Create a conflict record."""
        conflict_id = str(uuid.uuid4())
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO memory_conflicts (
                conflict_id, repo, memory_id_1, memory_id_2,
                conflict_type, severity, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (conflict_id, repo, memory_id_1, memory_id_2, conflict_type, severity, description),
        )
        self.conn.commit()
        return conflict_id

    def resolve_conflict(self, conflict_id: str, resolution_strategy: str) -> bool:
        """Resolve a conflict."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE memory_conflicts
            SET resolved_at = CURRENT_TIMESTAMP, resolution_strategy = ?
            WHERE conflict_id = ?
            """,
            (resolution_strategy, conflict_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_conflicts(self, repo: str, unresolved_only: bool = True) -> List[Dict[str, Any]]:
        """Get conflicts for a repository."""
        cursor = self.conn.cursor()
        if unresolved_only:
            cursor.execute(
                """
                SELECT * FROM memory_conflicts
                WHERE repo = ? AND resolved_at IS NULL
                ORDER BY detected_at DESC
                """,
                (repo,),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM memory_conflicts
                WHERE repo = ?
                ORDER BY detected_at DESC
                """,
                (repo,),
            )
        return [dict(row) for row in cursor.fetchall()]

    # Memory Usage Operations
    def track_memory_usage(
        self,
        repo: str,
        memory_id: str,
        context: str,
        weight: float,
        issue_id: Optional[str] = None,
        file_path: Optional[str] = None,
        line_number: Optional[int] = None,
    ) -> str:
        """Track memory usage for explainability."""
        usage_id = str(uuid.uuid4())
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO memory_usage (
                usage_id, repo, memory_id, context, issue_id,
                file_path, line_number, weight
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (usage_id, repo, memory_id, context, issue_id, file_path, line_number, weight),
        )
        self.conn.commit()
        return usage_id

    def get_memory_usage(
        self,
        repo: str,
        issue_id: Optional[str] = None,
        file_path: Optional[str] = None,
        line_number: Optional[int] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get memory usage for explainability."""
        cursor = self.conn.cursor()
        query = """
            SELECT mu.*, m.type, m.content, m.scope, m.confidence
            FROM memory_usage mu
            JOIN memory_entries m ON mu.memory_id = m.memory_id
            WHERE mu.repo = ?
        """
        params = [repo]

        if issue_id:
            query += " AND mu.issue_id = ?"
            params.append(issue_id)
        elif file_path:
            query += " AND mu.file_path = ?"
            params.append(file_path)
            if line_number:
                query += " AND mu.line_number = ?"
                params.append(line_number)

        query += " ORDER BY mu.weight DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    # Code Entity Operations
    def create_code_entity(
        self,
        repo: str,
        file_path: str,
        entity_type: str,
        name: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        language: Optional[str] = None,
        _cursor: Optional[sqlite3.Cursor] = None,
    ) -> str:
        """
        Create or update a code entity (upsert).
        
        If an entity with the same (repo, file_path, name, entity_type) exists,
        it will be updated. Otherwise, a new entity is created.
        
        Args:
            _cursor: Optional cursor for transaction batching. If provided,
                     caller is responsible for commit.
        
        Returns:
            The entity_id (either new or existing).
        """
        cursor = _cursor or self.conn.cursor()
        
        # First, try to find existing entity
        cursor.execute(
            """
            SELECT entity_id FROM code_entities 
            WHERE repo = ? AND file_path = ? AND name = ? AND entity_type = ?
            """,
            (repo, file_path, name, entity_type),
        )
        existing = cursor.fetchone()
        
        if existing:
            # Update existing entity
            entity_id = existing[0]
            cursor.execute(
                """
                UPDATE code_entities SET
                    start_line = ?,
                    end_line = ?,
                    language = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE entity_id = ?
                """,
                (start_line, end_line, language, entity_id),
            )
        else:
            # Insert new entity
            entity_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO code_entities (
                    entity_id, repo, file_path, entity_type, name,
                    start_line, end_line, language
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (entity_id, repo, file_path, entity_type, name, start_line, end_line, language),
            )
        
        if _cursor is None:
            self.conn.commit()
        
        return entity_id
    
    def clear_entities_for_repo(self, repo: str, _cursor: Optional[sqlite3.Cursor] = None) -> int:
        """
        Clear all code entities and relationships for a repository.
        
        Useful before re-indexing to avoid stale data.
        
        Args:
            _cursor: Optional cursor for transaction batching.
            
        Returns:
            Number of entities deleted.
        """
        cursor = _cursor or self.conn.cursor()
        
        # Delete relationships first (due to foreign key constraints)
        cursor.execute("DELETE FROM code_relationships WHERE repo = ?", (repo,))
        
        # Delete entities
        cursor.execute("DELETE FROM code_entities WHERE repo = ?", (repo,))
        deleted_count = cursor.rowcount
        
        if _cursor is None:
            self.conn.commit()
        
        logger.info(f"Cleared {deleted_count} entities for repo {repo}")
        return deleted_count
    
    def create_relationship_batch(
        self,
        relationships: List[Tuple[str, str, Optional[str], Optional[str], str]],
        _cursor: Optional[sqlite3.Cursor] = None,
    ) -> int:
        """
        Create multiple relationships in a batch.
        
        Args:
            relationships: List of (repo, source_entity_id, target_entity_id, 
                          target_symbol_name, relationship_type) tuples.
            _cursor: Optional cursor for transaction batching.
            
        Returns:
            Number of relationships created.
        """
        cursor = _cursor or self.conn.cursor()
        
        for repo, source_id, target_id, target_symbol, rel_type in relationships:
            relationship_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO code_relationships (
                    relationship_id, source_entity_id, target_entity_id,
                    target_symbol_name, relationship_type, repo
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (relationship_id, source_id, target_id, target_symbol, rel_type, repo),
            )
        
        if _cursor is None:
            self.conn.commit()
        
        return len(relationships)

    def get_code_entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Get a code entity by ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM code_entities WHERE entity_id = ?", (entity_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_entities_by_file(self, repo: str, file_path: str) -> List[Dict[str, Any]]:
        """Get all entities in a file."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM code_entities WHERE repo = ? AND file_path = ?",
            (repo, file_path),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_related_entities(
        self,
        entity_id: str,
        relationship_types: Optional[List[str]] = None,
        direction: str = "both",
    ) -> List[Dict[str, Any]]:
        """Get related entities."""
        cursor = self.conn.cursor()
        if relationship_types is None:
            relationship_types = ["calls", "imports"]

        results = []
        for rel_type in relationship_types:
            if direction in ("both", "outgoing"):
                cursor.execute(
                    """
                    SELECT e.*, r.relationship_type, 'outgoing' as direction
                    FROM code_entities e
                    JOIN code_relationships r ON e.entity_id = r.target_entity_id
                    WHERE r.source_entity_id = ? AND r.relationship_type = ?
                    """,
                    (entity_id, rel_type),
                )
                results.extend([dict(row) for row in cursor.fetchall()])

            if direction in ("both", "incoming"):
                cursor.execute(
                    """
                    SELECT e.*, r.relationship_type, 'incoming' as direction
                    FROM code_entities e
                    JOIN code_relationships r ON e.entity_id = r.source_entity_id
                    WHERE r.target_entity_id = ? AND r.relationship_type = ?
                    """,
                    (entity_id, rel_type),
                )
                results.extend([dict(row) for row in cursor.fetchall()])

        return results

    def create_relationship(
        self,
        repo: str,
        source_entity_id: str,
        target_entity_id: Optional[str],
        target_symbol_name: Optional[str],
        relationship_type: str,
    ) -> str:
        """Create a code relationship."""
        relationship_id = str(uuid.uuid4())
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO code_relationships (
                relationship_id, source_entity_id, target_entity_id,
                target_symbol_name, relationship_type, repo
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                relationship_id,
                source_entity_id,
                target_entity_id,
                target_symbol_name,
                relationship_type,
                repo,
            ),
        )
        self.conn.commit()
        return relationship_id

    # Git Commit Operations
    def record_commit(
        self,
        repo: str,
        commit_hash: str,
        branch: str,
        changed_files: List[str],
    ) -> None:
        """Record a git commit."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO git_commits (
                commit_hash, repo, branch, changed_files
            ) VALUES (?, ?, ?, ?)
            """,
            (commit_hash, repo, branch, json.dumps(changed_files)),
        )
        self.conn.commit()

    def get_last_reviewed_commit(self, repo: str, branch: str) -> Optional[str]:
        """Get the last reviewed commit hash."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT commit_hash FROM git_commits
            WHERE repo = ? AND branch = ? AND last_reviewed_at IS NOT NULL
            ORDER BY last_reviewed_at DESC LIMIT 1
            """,
            (repo, branch),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def mark_commit_reviewed(self, repo: str, commit_hash: str) -> None:
        """Mark a commit as reviewed."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE git_commits
            SET last_reviewed_at = CURRENT_TIMESTAMP
            WHERE repo = ? AND commit_hash = ?
            """,
            (repo, commit_hash),
        )
        self.conn.commit()

    # Edit Reasoning Operations
    def save_edit_reasoning(
        self,
        repo: str,
        files: List[Dict[str, Any]],
        commit_hash: Optional[str] = None,
        overall_reasoning: Optional[str] = None,
    ) -> str:
        """Save edit reasoning."""
        reasoning_id = str(uuid.uuid4())
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO edit_reasoning (
                reasoning_id, repo, commit_hash, files, reasoning,
                change_type, memory_category, scope, confidence, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reasoning_id,
                repo,
                commit_hash,
                json.dumps(files),
                overall_reasoning,
                None,  # change_type
                "session_context",  # memory_category
                "repo",  # scope
                0.8,  # confidence
                "explicit",  # source
            ),
        )
        self.conn.commit()
        return reasoning_id

    def get_edit_reasoning(
        self, repo: str, commit_hash: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get edit reasoning for a commit."""
        cursor = self.conn.cursor()
        if commit_hash:
            cursor.execute(
                "SELECT * FROM edit_reasoning WHERE repo = ? AND commit_hash = ? ORDER BY created_at DESC LIMIT 1",
                (repo, commit_hash),
            )
        else:
            cursor.execute(
                "SELECT * FROM edit_reasoning WHERE repo = ? ORDER BY created_at DESC LIMIT 1",
                (repo,),
            )
        row = cursor.fetchone()
        if not row:
            return None

        result = dict(row)
        if result.get("files"):
            result["files"] = json.loads(result["files"])
        return result

    def cleanup_expired_context(self) -> int:
        """Cleanup expired session context."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE memory_entries
            SET status = 'deprecated'
            WHERE type = 'session_context'
                AND expires_at < datetime('now')
                AND status = 'active'
            """
        )
        self.conn.commit()
        return cursor.rowcount
