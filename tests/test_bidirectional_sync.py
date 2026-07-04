"""Tests for bidirectional Postgres memory sync."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.v2_engine import postgres


class TestBidirectionalMemorySync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db = MemoryDatabase(db_path=str(Path(self.temp_dir) / "test.db"))

    def tearDown(self):
        self.db.close()
        import shutil

        shutil.rmtree(self.temp_dir)

    def test_apply_cloud_tombstone_deprecates_local(self):
        mem_id = self.db.create_memory_entry(
            repo="owner/repo",
            memory_type="explicit_rule",
            content="Always validate JWT",
            scope="repo",
            confidence=0.9,
        )
        stats = self.db.apply_cloud_memory_rows(
            "owner/repo",
            [{
                "memory_id": mem_id,
                "repo": "owner/repo",
                "type": "explicit_rule",
                "content": "Always validate JWT",
                "scope": "repo",
                "confidence": 0.9,
                "status": "deprecated",
                "updated_at": "2099-01-01T00:00:00+00:00",
            }],
        )
        self.assertEqual(stats["tombstones_applied"], 1)
        self.assertEqual(self.db.get_memory_entry(mem_id)["status"], "deprecated")

    def test_cloud_tombstone_wins_over_newer_local_active(self):
        mem_id = self.db.create_memory_entry(
            repo="owner/repo",
            memory_type="explicit_rule",
            content="Always validate JWT",
            scope="repo",
            confidence=0.9,
        )
        # Simulate local touch after remote deprecation
        self.db.update_memory_entry(mem_id, content="Always validate JWT")
        stats = self.db.apply_cloud_memory_rows(
            "owner/repo",
            [{
                "memory_id": mem_id,
                "repo": "owner/repo",
                "type": "explicit_rule",
                "content": "Always validate JWT",
                "scope": "repo",
                "confidence": 0.9,
                "status": "deprecated",
                "updated_at": "2000-01-01T00:00:00+00:00",
            }],
        )
        self.assertEqual(stats["tombstones_applied"], 1)
        self.assertEqual(self.db.get_memory_entry(mem_id)["status"], "deprecated")

    @mock.patch.object(postgres, "sync_memory_entries", return_value=1)
    @mock.patch.object(postgres, "pull_memory_entries", return_value=[])
    def test_sync_memories_bidirectional_updates_pull_cursor(self, _pull, _push):
        stats = postgres.sync_memories_bidirectional(self.db, "owner/repo")
        self.assertEqual(stats["memories_pushed"], 1)
        state = self.db.get_repo_sync_state("owner/repo")
        self.assertIsNotNone(state["last_cloud_pull_at"])
