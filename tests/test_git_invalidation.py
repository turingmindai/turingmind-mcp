"""Tests for git-aware invalidation churn."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.git_churn import GitChurnSnapshot
from turingmind_mcp.reconcile import ReconciliationEngine

REPO = "test/repo"


class TestGitInvalidation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db = MemoryDatabase(db_path=str(Path(self.temp_dir) / "test.db"))
        self.engine = ReconciliationEngine(self.db)
        self.workspace = Path(self.temp_dir) / "ws"
        (self.workspace / "src").mkdir(parents=True)
        (self.workspace / "src" / "hot.py").write_text("x", encoding="utf-8")

    def tearDown(self):
        self.db.close()
        import shutil

        shutil.rmtree(self.temp_dir)

    @mock.patch("turingmind_mcp.reconcile.collect_git_churn")
    def test_git_touch_triggers_churn_without_editor_observations(self, mock_git):
        mem_id = self.db.create_memory_entry(
            repo=REPO,
            memory_type="learned_pattern",
            content="cli-only churn pattern",
            scope="src/hot.py",
            confidence=0.8,
        )
        mock_git.return_value = GitChurnSnapshot(
            head="abc123",
            modified=frozenset(["src/hot.py"]),
            deleted=frozenset(),
        )
        with mock.patch.dict(os.environ, {"TURINGMIND_WORKSPACE_DIR": str(self.workspace)}):
            stats = self.engine.apply_invalidation_decay(REPO)
        self.assertEqual(stats["invalidation_git_churn"], 1)
        self.assertLess(self.db.get_memory_entry(mem_id)["confidence"], 0.8)
        state = self.db.get_repo_sync_state(REPO)
        self.assertEqual(state["last_git_head"], "abc123")
