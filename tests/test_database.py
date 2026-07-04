"""Unit tests for database operations."""

import tempfile
import unittest
from pathlib import Path

from turingmind_mcp.database import MemoryDatabase


class TestMemoryDatabase(unittest.TestCase):
    """Test cases for MemoryDatabase."""

    def setUp(self):
        """Set up test database."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.temp_dir) / "test.db")
        self.db = MemoryDatabase(db_path=self.db_path)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        import shutil
        shutil.rmtree(self.temp_dir)

    def test_create_memory_entry(self):
        """Test creating a memory entry."""
        memory_id = self.db.create_memory_entry(
            repo="test/repo",
            memory_type="explicit_rule",
            content="Test rule",
            scope="repo",
            confidence=0.9,
        )

        self.assertIsNotNone(memory_id)
        self.assertEqual(len(memory_id), 36)  # UUID length

        # Verify entry exists
        entry = self.db.get_memory_entry(memory_id)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["content"], "Test rule")
        self.assertEqual(entry["type"], "explicit_rule")
        self.assertEqual(entry["scope"], "repo")

    def test_list_memory_entries(self):
        """Test listing memory entries."""
        # Create multiple entries
        self.db.create_memory_entry(
            repo="test/repo",
            memory_type="explicit_rule",
            content="Rule 1",
            scope="repo",
        )
        self.db.create_memory_entry(
            repo="test/repo",
            memory_type="learned_pattern",
            content="Pattern 1",
            scope="file.py",
        )
        self.db.create_memory_entry(
            repo="other/repo",
            memory_type="explicit_rule",
            content="Rule 2",
            scope="repo",
        )

        # List all entries for test/repo
        entries = self.db.list_memory_entries("test/repo")
        self.assertEqual(len(entries), 2)

        # Filter by type
        rules = self.db.list_memory_entries("test/repo", memory_type="explicit_rule")
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["content"], "Rule 1")

    def test_update_memory_entry(self):
        """Test updating a memory entry."""
        memory_id = self.db.create_memory_entry(
            repo="test/repo",
            memory_type="explicit_rule",
            content="Original content",
            scope="repo",
            confidence=0.8,
        )

        # Update content and confidence
        success = self.db.update_memory_entry(
            memory_id, content="Updated content", confidence=0.95
        )
        self.assertTrue(success)

        # Verify update
        entry = self.db.get_memory_entry(memory_id)
        self.assertEqual(entry["content"], "Updated content")
        self.assertEqual(entry["confidence"], 0.95)

    def test_delete_memory_entry(self):
        """Test deleting a memory entry."""
        memory_id = self.db.create_memory_entry(
            repo="test/repo",
            memory_type="explicit_rule",
            content="To be deleted",
            scope="repo",
        )

        # Deprecate (soft delete)
        success = self.db.delete_memory_entry(memory_id, deprecate=True)
        self.assertTrue(success)

        entry = self.db.get_memory_entry(memory_id)
        self.assertEqual(entry["status"], "deprecated")

        # Hard delete
        success = self.db.delete_memory_entry(memory_id, deprecate=False)
        self.assertTrue(success)

        entry = self.db.get_memory_entry(memory_id)
        self.assertIsNone(entry)

    def test_add_evidence(self):
        """Test adding evidence to memory entry."""
        memory_id = self.db.create_memory_entry(
            repo="test/repo",
            memory_type="learned_pattern",
            content="Pattern",
            scope="repo",
        )

        evidence_id = self.db.add_evidence(
            memory_id=memory_id,
            evidence_type="feedback",
            content="User marked as false positive",
            file_path="test.py",
            line_number=42,
        )

        self.assertIsNotNone(evidence_id)

        # Get evidence
        evidence_list = self.db.get_evidence(memory_id)
        self.assertEqual(len(evidence_list), 1)
        self.assertEqual(evidence_list[0]["evidence_type"], "feedback")
        self.assertEqual(evidence_list[0]["file_path"], "test.py")

    def test_create_conflict(self):
        """Test creating a conflict."""
        memory_id_1 = self.db.create_memory_entry(
            repo="test/repo",
            memory_type="explicit_rule",
            content="Rule 1",
            scope="repo",
        )
        memory_id_2 = self.db.create_memory_entry(
            repo="test/repo",
            memory_type="explicit_rule",
            content="Rule 2",
            scope="repo",
        )

        conflict_id = self.db.create_conflict(
            repo="test/repo",
            memory_id_1=memory_id_1,
            memory_id_2=memory_id_2,
            conflict_type="contradiction",
            severity="high",
            description="Rules contradict each other",
        )

        self.assertIsNotNone(conflict_id)

        # Get conflicts
        conflicts = self.db.get_conflicts("test/repo", unresolved_only=True)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["conflict_type"], "contradiction")

    def test_resolve_conflict(self):
        """Test resolving a conflict."""
        memory_id_1 = self.db.create_memory_entry(
            repo="test/repo",
            memory_type="explicit_rule",
            content="Rule 1",
            scope="repo",
        )
        memory_id_2 = self.db.create_memory_entry(
            repo="test/repo",
            memory_type="explicit_rule",
            content="Rule 2",
            scope="repo",
        )

        conflict_id = self.db.create_conflict(
            repo="test/repo",
            memory_id_1=memory_id_1,
            memory_id_2=memory_id_2,
            conflict_type="contradiction",
            severity="high",
        )

        # Resolve conflict
        success = self.db.resolve_conflict(conflict_id, "priority")
        self.assertTrue(success)

        # Verify resolved
        conflicts = self.db.get_conflicts("test/repo", unresolved_only=True)
        self.assertEqual(len(conflicts), 0)


class TestMemorySearchFTS(unittest.TestCase):
    """Test cases for FTS5-backed memory search."""

    def setUp(self):
        """Set up test database with searchable entries."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.temp_dir) / "test.db")
        self.db = MemoryDatabase(db_path=self.db_path)
        self.db.create_memory_entry(
            repo="test/repo",
            memory_type="explicit_rule",
            content="Avoid synchronous MongoDB calls in FastAPI routes",
            scope="repo",
            confidence=0.9,
        )
        self.db.create_memory_entry(
            repo="test/repo",
            memory_type="learned_pattern",
            content="Use the async motor driver for database access",
            scope="repo",
            confidence=0.7,
        )
        self.db.create_memory_entry(
            repo="test/repo",
            memory_type="session_context",
            content="Working on the login page styling",
            scope="repo",
            confidence=0.8,
        )

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        import shutil
        shutil.rmtree(self.temp_dir)

    def test_fts_enabled(self):
        """FTS5 index should initialize on a fresh database."""
        self.assertTrue(self.db._fts_enabled)

    def test_search_matches_any_token(self):
        """Multi-token search should OR tokens instead of requiring a substring."""
        results = self.db.list_memory_entries("test/repo", search="MongoDB async")
        contents = [r["content"] for r in results]
        self.assertEqual(len(results), 2)
        self.assertIn("Avoid synchronous MongoDB calls in FastAPI routes", contents)
        self.assertIn("Use the async motor driver for database access", contents)

    def test_search_no_match(self):
        """Unrelated search should return nothing."""
        results = self.db.list_memory_entries("test/repo", search="kubernetes")
        self.assertEqual(len(results), 0)

    def test_search_reflects_updates(self):
        """FTS index should stay in sync after content updates."""
        memory_id = self.db.create_memory_entry(
            repo="test/repo",
            memory_type="explicit_rule",
            content="Original searchable text",
            scope="repo",
        )
        self.db.update_memory_entry(memory_id, content="Replaced with redis caching rule")

        self.assertEqual(
            len(self.db.list_memory_entries("test/repo", search="searchable")), 0
        )
        results = self.db.list_memory_entries("test/repo", search="redis")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["memory_id"], memory_id)

    def test_search_reflects_deletes(self):
        """FTS index should drop hard-deleted rows."""
        memory_id = self.db.create_memory_entry(
            repo="test/repo",
            memory_type="explicit_rule",
            content="Ephemeral graphql pagination rule",
            scope="repo",
        )
        self.db.delete_memory_entry(memory_id, deprecate=False)
        self.assertEqual(
            len(self.db.list_memory_entries("test/repo", search="graphql")), 0
        )

    def test_search_respects_filters(self):
        """FTS search should still honor type filters."""
        results = self.db.list_memory_entries(
            "test/repo", memory_type="learned_pattern", search="database"
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["type"], "learned_pattern")

    def test_fts_query_quotes_syntax(self):
        """Special FTS characters must not inject query syntax."""
        # Would raise sqlite3.OperationalError if unquoted
        results = self.db.list_memory_entries("test/repo", search='NEAR( "login" *')
        contents = [r["content"] for r in results]
        self.assertIn("Working on the login page styling", contents)


class TestObservations(unittest.TestCase):
    """Test cases for the draft-observation layer."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db = MemoryDatabase(db_path=str(Path(self.temp_dir) / "test.db"))

    def tearDown(self):
        self.db.close()
        import shutil
        shutil.rmtree(self.temp_dir)

    def test_create_and_list_pending(self):
        """New observations default to pending and round-trip evidence JSON."""
        obs_id = self.db.create_observation(
            repo="test/repo",
            event_type="edit_cluster",
            content="targeted_fix/high: 1 code file changed",
            source="cursor-hook",
            confidence=0.3,
            evidence=[{"type": "files", "content": "src/auth.py"}],
        )
        rows = self.db.list_observations(repo="test/repo")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["observation_id"], obs_id)
        self.assertEqual(rows[0]["status"], "pending")
        self.assertEqual(rows[0]["evidence"][0]["content"], "src/auth.py")

    def test_resolve_promotes_out_of_pending(self):
        """Accepted observations leave the pending list and link their memory."""
        obs_id = self.db.create_observation(
            repo="test/repo", event_type="blocked_push", content="critical gap"
        )
        memory_id = self.db.create_memory_entry(
            repo="test/repo", memory_type="learned_pattern",
            content="critical gap", scope="repo",
        )
        self.assertTrue(self.db.resolve_observation(obs_id, "accepted", memory_id))
        self.assertEqual(self.db.list_observations(repo="test/repo"), [])
        accepted = self.db.list_observations(repo="test/repo", status="accepted")
        self.assertEqual(accepted[0]["memory_id"], memory_id)

    def test_resolve_rejects_invalid_status(self):
        obs_id = self.db.create_observation(
            repo="test/repo", event_type="intent", content="x"
        )
        with self.assertRaises(ValueError):
            self.db.resolve_observation(obs_id, "maybe")

    def test_observations_never_surface_in_memory_recall(self):
        """Draft observations must not leak into memory listings."""
        self.db.create_observation(
            repo="test/repo", event_type="edit_cluster", content="unique-draft-token"
        )
        entries = self.db.list_memory_entries(repo="test/repo", search="unique-draft-token")
        self.assertEqual(entries, [])


class TestMemoryToolJSON(unittest.TestCase):
    """Test cases for the JSON output contract of the memory tool handlers."""

    def setUp(self):
        """Set up a handler context backed by a temp database."""
        import logging

        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.temp_dir) / "test.db")
        self.db = MemoryDatabase(db_path=self.db_path)

        class StubContext:
            get_db = lambda _self=None, db=self.db: db
            get_memory_manager = None
            logger = logging.getLogger("test")

        self.ctx = StubContext()

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        import shutil
        shutil.rmtree(self.temp_dir)

    def test_list_and_get_round_trip(self):
        """list_memory must expose memory_id so get_memory can be called with it."""
        import asyncio
        import json

        from turingmind_mcp.tools.memory import handle_get_memory, handle_list_memory

        long_content = "A" * 300  # would have been truncated by the old formatter
        memory_id = self.db.create_memory_entry(
            repo="test/repo",
            memory_type="explicit_rule",
            content=long_content,
            scope="repo",
        )

        listed = asyncio.run(handle_list_memory({"repo": "test/repo"}, self.ctx))
        payload = json.loads(listed[0].text)
        self.assertEqual(payload["total"], 1)
        entry = payload["entries"][0]
        self.assertEqual(entry["memory_id"], memory_id)
        self.assertEqual(entry["content"], long_content)

        fetched = asyncio.run(
            handle_get_memory(
                {"repo": "test/repo", "memory_id": entry["memory_id"]}, self.ctx
            )
        )
        detail = json.loads(fetched[0].text)
        self.assertEqual(detail["memory_id"], memory_id)
        self.assertEqual(detail["content"], long_content)
        self.assertEqual(detail["evidence"], [])


if __name__ == "__main__":
    unittest.main()
