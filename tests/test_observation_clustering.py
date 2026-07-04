"""Tests for semantic observation clustering in Pass 1."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.reconcile import RECURRENCE_THRESHOLD, ReconciliationEngine


REPO = "test/repo"


class TestSemanticRecurrenceMining(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db = MemoryDatabase(db_path=str(Path(self.temp_dir) / "test.db"))
        self.engine = ReconciliationEngine(self.db)

    def tearDown(self):
        self.db.close()
        import shutil

        shutil.rmtree(self.temp_dir)

    @mock.patch("turingmind_mcp.reconcile.observations_semantically_similar", return_value=True)
    @mock.patch(
        "turingmind_mcp.reconcile.build_observation_vectors",
        return_value=("hash_bow_v1", {}),
    )
    def test_paraphrase_observations_cluster_for_mining(self, _mock_vec, _mock_sim):
        phrases = [
            "auth token expired during jwt validation in middleware",
            "session jwt token expired validation failure in middleware",
            "jwt validation failed because auth token expired in middleware",
        ]
        for phrase in phrases:
            self.db.create_observation(
                repo=REPO,
                event_type="edit_cluster",
                content=phrase,
            )
        stats = self.engine.mine_recurrence(REPO)
        self.assertEqual(stats["patterns_mined"], 1)
        self.assertEqual(stats["observations_accepted"], RECURRENCE_THRESHOLD)
