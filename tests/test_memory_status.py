"""Tests for memory_status snapshot."""

import shutil
import tempfile
import unittest

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.memory_status import collect_memory_status


class TestMemoryStatus(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = f"{self.temp_dir}/test.db"
        self.db = MemoryDatabase(db_path=self.db_path)
        self.repo = "test/status"

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir)

    def test_collect_memory_status(self):
        self.db.create_memory_entry(
            repo=self.repo,
            memory_type="explicit_rule",
            content="always use logging",
            scope="repo",
        )
        self.db.create_observation(
            repo=self.repo, event_type="edit_cluster", content="edit src/a.py",
        )
        self.db.create_finding(
            repo=self.repo,
            finding_type="promotion_candidate",
            severity="low",
            action="review me",
            dedup_key="test-dedup",
        )
        report = collect_memory_status(db_path=self.db_path, repo=self.repo)
        self.assertEqual(len(report.repos), 1)
        row = report.repos[0]
        self.assertEqual(row.pending_observations, 1)
        self.assertEqual(row.findings_pending, 1)
        self.assertEqual(row.explicit_rules, 1)


if __name__ == "__main__":
    unittest.main()
