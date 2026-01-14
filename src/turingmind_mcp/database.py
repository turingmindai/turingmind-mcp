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

import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import logging

logger = logging.getLogger("turingmind-mcp")


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
        self._initialize_schema()

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
                created_by TEXT
            )
        """)

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
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
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

        # Create indexes
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_memory_repo_type ON memory_entries(repo, type)",
            "CREATE INDEX IF NOT EXISTS idx_memory_repo_status ON memory_entries(repo, status)",
            "CREATE INDEX IF NOT EXISTS idx_memory_repo_scope ON memory_entries(repo, scope)",
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
        logger.info(f"Database schema initialized at {self.db_path}")

    def close(self):
        """Close database connection."""
        self.conn.close()

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
    ) -> str:
        """Create a new memory entry."""
        memory_id = str(uuid.uuid4())
        security_tags_json = json.dumps(security_tags) if security_tags else None

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO memory_entries (
                memory_id, repo, type, content, scope, confidence,
                status, security_tags, yaml_definition, expires_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def list_memory_entries(
        self,
        repo: str,
        memory_type: Optional[str] = None,
        status: Optional[str] = None,
        scope: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List memory entries with filtering."""
        cursor = self.conn.cursor()
        offset = (page - 1) * limit

        query = "SELECT * FROM memory_entries WHERE repo = ?"
        params = [repo]

        if memory_type and memory_type != "all":
            query += " AND type = ?"
            params.append(memory_type)

        if status and status != "all":
            query += " AND status = ?"
            params.append(status)

        if scope:
            query += " AND (scope = ? OR scope = 'repo')"
            params.append(scope)

        if search:
            query += " AND content LIKE ?"
            params.append(f"%{search}%")

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()

        results = []
        for row in rows:
            result = dict(row)
            if result.get("security_tags"):
                result["security_tags"] = json.loads(result["security_tags"])
            results.append(result)

        return results

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
        """Delete or deprecate a memory entry."""
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
    ) -> str:
        """Create a code entity."""
        entity_id = str(uuid.uuid4())
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO code_entities (
                entity_id, repo, file_path, entity_type, name,
                start_line, end_line, language
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (entity_id, repo, file_path, entity_type, name, start_line, end_line, language),
        )
        self.conn.commit()
        return entity_id

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
