"""Tests for optional LLM distillation at queue adjudication."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.memory_distillation import (
    DistillationError,
    _gather_context,
    draft_finding_async,
)


class TestMemoryDistillation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.temp_dir) / "test.db")
        self.db = MemoryDatabase(db_path=self.db_path)

    def tearDown(self):
        self.db.close()
        import shutil

        shutil.rmtree(self.temp_dir)

    def test_gather_context_for_promotion_candidate(self):
        memory_id = self.db.create_memory_entry(
            repo="test/repo",
            memory_type="learned_pattern",
            content="Recurring edit_cluster (3x): changed auth module",
            scope="repo",
            status="candidate",
        )
        obs_id = self.db.create_observation(
            repo="test/repo",
            event_type="edit_cluster",
            content="auth.py refactor burst",
        )
        finding_id = self.db.create_finding(
            repo="test/repo",
            finding_type="promotion_candidate",
            severity="medium",
            action="Promote pattern?",
            dedup_key="promo:test",
            evidence=[{"type": "observation", "content": obs_id}],
            memory_id=memory_id,
        )
        finding = self.db.conn.execute(
            "SELECT * FROM reconcile_findings WHERE finding_id = ?",
            (finding_id,),
        ).fetchone()
        import json

        ctx = _gather_context(self.db, {**dict(finding), "evidence": json.loads(finding["evidence"])})
        self.assertEqual(ctx["finding_type"], "promotion_candidate")
        self.assertEqual(len(ctx["observations"]), 1)
        self.assertEqual(len(ctx["memories"]), 1)

    def test_draft_rejects_non_pending(self):
        finding_id = self.db.create_finding(
            repo="test/repo",
            finding_type="promotion_candidate",
            severity="low",
            action="done",
            dedup_key="done:test",
        )
        self.db.resolve_finding(finding_id, "dismissed")

        with self.assertRaises(DistillationError):
            import asyncio

            asyncio.run(draft_finding_async(self.db, finding_id))

    @mock.patch("turingmind_mcp.memory_distillation._call_llm")
    def test_draft_returns_scrubbed_content(self, mock_llm):
        mock_llm.return_value = "Use environment variables for secrets like sk-abcdefghijklmnopqrstuvwxyz1234567890"
        finding_id = self.db.create_finding(
            repo="test/repo",
            finding_type="promotion_candidate",
            severity="medium",
            action="Promote?",
            dedup_key="draft:test",
        )
        import asyncio

        result = asyncio.run(draft_finding_async(self.db, finding_id))
        self.assertTrue(result["review_required"])
        self.assertIn("[REDACTED_SECRET]", result["draft_content"])
        self.assertNotIn("sk-abc", result["draft_content"])
