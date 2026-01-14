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


if __name__ == "__main__":
    unittest.main()
