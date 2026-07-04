"""Unit tests for memory manager."""

import tempfile
import unittest
from pathlib import Path

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.memory_manager import MemoryManager


class TestMemoryManager(unittest.TestCase):
    """Test cases for MemoryManager."""

    def setUp(self):
        """Set up test database and manager."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.temp_dir) / "test.db")
        self.db = MemoryDatabase(db_path=self.db_path)
        self.manager = MemoryManager(self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        import shutil
        shutil.rmtree(self.temp_dir)

    def test_create_explicit_rule(self):
        """Test creating an explicit rule."""
        result = self.manager.create_explicit_rule(
            repo="test/repo",
            content="Use async/await for I/O operations",
            scope="repo",
            yaml_definition="rule: use_async_io",
        )

        self.assertIn("memory_id", result)
        entry = self.db.get_memory_entry(result["memory_id"])
        self.assertEqual(entry["type"], "explicit_rule")
        self.assertEqual(entry["content"], "Use async/await for I/O operations")

    def test_learn_pattern_from_feedback(self):
        """Test learning a pattern from feedback."""
        # Create initial pattern
        memory_id = self.manager.learn_pattern_from_feedback(
            repo="test/repo",
            pattern="console.log()",
            file_path="test.js",
            reason="Debug statement, not a security issue",
        )

        self.assertIsNotNone(memory_id)

        # Learn same pattern again (should increase confidence)
        memory_id_2 = self.manager.learn_pattern_from_feedback(
            repo="test/repo",
            pattern="console.log()",
            file_path="test2.js",
            reason="Another debug statement",
        )

        # Should return same memory ID
        self.assertEqual(memory_id, memory_id_2)

        # Check confidence increased
        entry = self.db.get_memory_entry(memory_id)
        self.assertGreater(entry["confidence"], 0.7)

    def test_create_session_context(self):
        """Test creating session context."""
        memory_id = self.manager.create_session_context(
            repo="test/repo",
            content="Working on authentication feature",
            scope="auth.py",
            evidence=[
                {
                    "type": "conversation",
                    "content": "User mentioned adding OAuth support",
                    "file": "auth.py",
                }
            ],
        )

        self.assertIsNotNone(memory_id)
        entry = self.db.get_memory_entry(memory_id)
        self.assertEqual(entry["type"], "session_context")
        self.assertIsNotNone(entry.get("expires_at"))

    def test_detect_conflicts(self):
        """Test conflict detection."""
        # Create conflicting entries (create_explicit_rule returns a dict)
        result_1 = self.manager.create_explicit_rule(
            repo="test/repo",
            content="Always use async/await",
            scope="repo",
        )

        result_2 = self.manager.create_explicit_rule(
            repo="test/repo",
            content="Never use async/await",
            scope="repo",
        )

        # Detect conflicts
        conflicts = self.manager.detect_conflicts("test/repo", result_2["memory_id"])
        self.assertGreater(len(conflicts), 0)
        self.assertEqual(conflicts[0]["type"], "contradiction")

        # Conflicts flag but never disable: both entries must stay active
        for result in (result_1, result_2):
            entry = self.db.get_memory_entry(result["memory_id"])
            self.assertEqual(entry["status"], "active")

    def test_get_relevant_memory(self):
        """Test getting relevant memory for files."""
        # Create repo-level memory
        self.manager.create_explicit_rule(
            repo="test/repo",
            content="Repo-wide rule",
            scope="repo",
        )

        # Create file-specific memory
        self.manager.create_explicit_rule(
            repo="test/repo",
            content="File-specific rule",
            scope="auth.py",
        )

        # Get relevant memory
        relevant = self.manager.get_relevant_memory("test/repo", ["auth.py", "other.py"])
        self.assertGreaterEqual(len(relevant), 2)

        # Should include both repo and file-level
        scopes = [m["scope"] for m in relevant]
        self.assertIn("repo", scopes)
        self.assertIn("auth.py", scopes)

    def test_get_relevant_memory_excludes_session_context(self):
        """Session context is ephemeral and excluded from hook pre-load."""
        self.manager.create_explicit_rule(
            repo="test/repo",
            content="Repo rule",
            scope="repo",
        )
        self.manager.create_session_context(
            repo="test/repo",
            content="Working on auth refactor",
            scope="repo",
            evidence=[],
        )

        relevant = self.manager.get_relevant_memory("test/repo", ["auth.py"])
        types = {m["type"] for m in relevant}
        self.assertIn("explicit_rule", types)
        self.assertNotIn("session_context", types)

    def test_get_relevant_memory_path_prefix_match(self):
        """Scoped memories match nested file paths."""
        self.manager.create_explicit_rule(
            repo="test/repo",
            content="Auth module rule",
            scope="src/auth",
        )

        relevant = self.manager.get_relevant_memory(
            "test/repo", ["src/auth/login.py"]
        )
        scopes = [m["scope"] for m in relevant]
        self.assertIn("src/auth", scopes)

        unrelated = self.manager.get_relevant_memory(
            "test/repo", ["lib/utils.py"]
        )
        scoped = [m for m in unrelated if m["scope"] != "repo"]
        self.assertEqual(scoped, [])

    def test_extract_and_persist_repo_facts(self):
        """Bootstrap-style repo fact extraction is idempotent."""
        files = ["package.json", "src/index.ts", "lib/utils.ts", "apps/web/package.json"]
        facts = self.manager.extract_repo_facts("test/repo", files)
        self.assertTrue(any("Node.js" in f["content"] for f in facts))
        self.assertTrue(any("Monorepo" in f["content"] for f in facts))

        created = self.manager.persist_repo_facts("test/repo", facts)
        self.assertGreater(len(created), 0)

        stored = self.db.list_memory_entries(
            repo="test/repo", memory_type="repo_fact", status="active"
        )
        self.assertGreaterEqual(len(stored), len(created))

        again = self.manager.persist_repo_facts("test/repo", facts)
        self.assertEqual(again, [])


if __name__ == "__main__":
    unittest.main()
