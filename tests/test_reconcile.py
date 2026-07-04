"""Tests for the deterministic reconciliation engine."""

import shutil
import tempfile
import unittest
from pathlib import Path

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.reconcile import (
    RECURRENCE_THRESHOLD,
    ReconciliationEngine,
    repos_with_activity,
)

REPO = "test/repo"


class ReconcileTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db = MemoryDatabase(db_path=str(Path(self.temp_dir) / "test.db"))
        self.engine = ReconciliationEngine(self.db)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir)


class TestRecurrenceMiner(ReconcileTestCase):
    def _add_observations(self, count: int, content: str):
        for _ in range(count):
            self.db.create_observation(
                repo=REPO, event_type="edit_cluster", content=content,
                source="cursor-hook",
            )

    def test_recurring_observations_become_candidate(self):
        """N similar observations mine one candidate pattern + queue finding."""
        self._add_observations(
            RECURRENCE_THRESHOLD,
            "targeted_fix/high: 1 code file changed in src/auth/jwt_middleware.py",
        )
        stats = self.engine.mine_recurrence(REPO)
        self.assertEqual(stats["patterns_mined"], 1)
        self.assertEqual(stats["observations_accepted"], RECURRENCE_THRESHOLD)

        # Candidate is NOT active — promotion needs queue approval
        candidates = self.db.list_memory_entries(
            repo=REPO, memory_type="learned_pattern", status="candidate"
        )
        self.assertEqual(len(candidates), 1)
        self.assertIn("Recurring activity", candidates[0]["content"])
        active = self.db.list_memory_entries(
            repo=REPO, memory_type="learned_pattern", status="active"
        )
        self.assertEqual(active, [])

        # Finding on the queue, observations consumed
        findings = self.db.list_findings(repo=REPO)
        self.assertEqual(findings[0]["finding_type"], "promotion_candidate")
        self.assertEqual(self.db.list_observations(repo=REPO), [])

    def test_below_threshold_stays_pending(self):
        self._add_observations(RECURRENCE_THRESHOLD - 1, "some rare event")
        stats = self.engine.mine_recurrence(REPO)
        self.assertEqual(stats["patterns_mined"], 0)
        self.assertEqual(
            len(self.db.list_observations(repo=REPO)), RECURRENCE_THRESHOLD - 1
        )

    def test_dissimilar_observations_not_clustered(self):
        self._add_observations(2, "refactor burst in frontend components")
        self._add_observations(1, "database migration script added for billing")
        stats = self.engine.mine_recurrence(REPO)
        self.assertEqual(stats["patterns_mined"], 0)

    def test_rerun_is_idempotent(self):
        """A second run must not mine the same pattern twice."""
        self._add_observations(RECURRENCE_THRESHOLD, "repeated fix in payment flow")
        self.engine.mine_recurrence(REPO)
        stats2 = self.engine.mine_recurrence(REPO)
        self.assertEqual(stats2["patterns_mined"], 0)
        candidates = self.db.list_memory_entries(
            repo=REPO, memory_type="learned_pattern", status="candidate"
        )
        self.assertEqual(len(candidates), 1)


class TestConfidenceDecay(ReconcileTestCase):
    def _backdate(self, memory_id: str, days: int):
        self.db.conn.execute(
            "UPDATE memory_entries SET updated_at = datetime('now', ?) WHERE memory_id = ?",
            (f"-{days} days", memory_id),
        )
        self.db.conn.commit()

    def test_old_pattern_decays(self):
        memory_id = self.db.create_memory_entry(
            repo=REPO, memory_type="learned_pattern",
            content="old wisdom", scope="repo", confidence=0.8,
        )
        self._backdate(memory_id, 60)  # two half-lives
        stats = self.engine.decay_confidence(REPO)
        self.assertEqual(stats["memories_decayed"], 1)
        entry = self.db.get_memory_entry(memory_id)
        self.assertLess(entry["confidence"], 0.3)
        # Stale finding emitted once it crossed the threshold
        findings = self.db.list_findings(repo=REPO)
        self.assertEqual(findings[0]["finding_type"], "stale_memory")

    def test_recent_pattern_untouched(self):
        memory_id = self.db.create_memory_entry(
            repo=REPO, memory_type="learned_pattern",
            content="fresh wisdom", scope="repo", confidence=0.8,
        )
        stats = self.engine.decay_confidence(REPO)
        self.assertEqual(stats["memories_decayed"], 0)
        self.assertEqual(self.db.get_memory_entry(memory_id)["confidence"], 0.8)

    def test_explicit_rules_never_decay(self):
        memory_id = self.db.create_memory_entry(
            repo=REPO, memory_type="explicit_rule",
            content="humans decided this", scope="repo", confidence=0.9,
        )
        self._backdate(memory_id, 365)
        self.engine.decay_confidence(REPO)
        self.assertEqual(self.db.get_memory_entry(memory_id)["confidence"], 0.9)

    def test_decay_does_not_reset_clock(self):
        """Decay must not bump updated_at, or it would only ever decay once."""
        memory_id = self.db.create_memory_entry(
            repo=REPO, memory_type="learned_pattern",
            content="aging wisdom", scope="repo", confidence=0.8,
        )
        self._backdate(memory_id, 45)
        self.engine.decay_confidence(REPO)
        first = self.db.get_memory_entry(memory_id)["confidence"]
        self._backdate(memory_id, 90)
        self.engine.decay_confidence(REPO)
        second = self.db.get_memory_entry(memory_id)["confidence"]
        self.assertLess(second, first)


class TestConflictAggregator(ReconcileTestCase):
    def test_unresolved_conflict_surfaces_once(self):
        m1 = self.db.create_memory_entry(
            repo=REPO, memory_type="explicit_rule", content="always use tabs", scope="repo"
        )
        m2 = self.db.create_memory_entry(
            repo=REPO, memory_type="explicit_rule", content="never use tabs", scope="repo"
        )
        self.db.create_conflict(REPO, m1, m2, "contradiction", "high", "tabs vs spaces")

        stats = self.engine.aggregate_conflicts(REPO)
        self.assertEqual(stats["conflicts_surfaced"], 1)
        findings = self.db.list_findings(repo=REPO)
        self.assertEqual(findings[0]["finding_type"], "memory_conflict")
        self.assertEqual(findings[0]["severity"], "high")

        # Re-run: still open, but not duplicated on the queue
        stats2 = self.engine.aggregate_conflicts(REPO)
        self.assertEqual(stats2["conflicts_surfaced"], 0)
        self.assertEqual(len(self.db.list_findings(repo=REPO)), 1)


class TestFindingLifecycle(ReconcileTestCase):
    def test_resolve_clears_pending(self):
        fid = self.db.create_finding(
            repo=REPO, finding_type="promotion_candidate", severity="medium",
            action="promote?", dedup_key="k1",
        )
        self.assertTrue(self.db.resolve_finding(fid, "dismissed"))
        self.assertEqual(self.db.list_findings(repo=REPO), [])

    def test_invalid_status_rejected(self):
        fid = self.db.create_finding(
            repo=REPO, finding_type="stale_memory", severity="low",
            action="x", dedup_key="k2",
        )
        with self.assertRaises(ValueError):
            self.db.resolve_finding(fid, "maybe")


class TestRunAndStats(ReconcileTestCase):
    def test_full_run_records_stats(self):
        for _ in range(RECURRENCE_THRESHOLD):
            self.db.create_observation(
                repo=REPO, event_type="edit_cluster",
                content="repeated targeted fix in src/api/session.py",
            )
        stats = self.engine.run(REPO)
        self.assertIn("run_id", stats)
        self.assertEqual(stats["patterns_mined"], 1)
        rows = self.db.conn.execute(
            "SELECT stats FROM reconcile_runs WHERE repo = ?", (REPO,)
        ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_repos_with_activity(self):
        self.db.create_observation(repo="repo/a", event_type="intent", content="x")
        self.db.create_memory_entry(
            repo="repo/b", memory_type="explicit_rule", content="y", scope="repo"
        )
        self.assertEqual(set(repos_with_activity(self.db)), {"repo/a", "repo/b"})


if __name__ == "__main__":
    unittest.main()
