"""Tests for optional sqlite-vec ANN duplicate detection."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.memory_embeddings import (
    HASH_BOW_METHOD,
    duplicate_threshold_for,
    embed_text,
    index_memory_embeddings,
)
from turingmind_mcp.memory_vec_index import (
    ANN_MIN_ROWS,
    find_embedding_duplicate_pairs,
    sqlite_vec_enabled,
)


class TestMemoryVecIndex(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.temp_dir) / "test.db")
        self.db = MemoryDatabase(db_path=self.db_path)

    def tearDown(self):
        self.db.close()
        import shutil

        shutil.rmtree(self.temp_dir)

    def _seed_similar_pair(self):
        for content in (
            "Always use async await for database IO operations",
            "Use async await for all database IO operations",
        ):
            self.db.create_memory_entry(
                repo="test/repo",
                memory_type="learned_pattern",
                content=content,
                scope="repo",
                status="active",
            )
        entries = self.db.list_memory_entries(repo="test/repo", status="active")
        index_memory_embeddings(self.db, entries)
        return self.db.list_memory_embeddings("test/repo")

    def test_brute_force_finds_similar_pair(self):
        rows = self._seed_similar_pair()
        pairs = find_embedding_duplicate_pairs(
            self.db.conn,
            HASH_BOW_METHOD,
            rows,
            duplicate_threshold_for(HASH_BOW_METHOD),
            ann_min_rows=9999,
        )
        self.assertEqual(len(pairs), 1)
        self.assertGreaterEqual(pairs[0][2], duplicate_threshold_for(HASH_BOW_METHOD))

    @mock.patch("turingmind_mcp.memory_vec_index.sqlite_vec_enabled", return_value=True)
    @mock.patch("turingmind_mcp.memory_vec_index._sync_vec_table", return_value=True)
    @mock.patch("turingmind_mcp.memory_vec_index._ann_pairs")
    def test_ann_path_when_enabled(self, mock_ann, _mock_sync, _mock_enabled):
        mock_ann.return_value = [("a", "b", 0.95)]
        rows = [
            {"memory_id": "a", "embedding": embed_text("async io one"), "content": "x"},
            {"memory_id": "b", "embedding": embed_text("async io two"), "content": "y"},
        ]
        pairs = find_embedding_duplicate_pairs(
            self.db.conn,
            HASH_BOW_METHOD,
            rows,
            0.8,
            ann_min_rows=2,
        )
        self.assertEqual(pairs, [("a", "b", 0.95)])
        mock_ann.assert_called_once()

    def test_sqlite_vec_disabled_by_env(self):
        with mock.patch.dict("os.environ", {"TURINGMIND_VEC_INDEX": "0"}):
            self.assertFalse(sqlite_vec_enabled())
