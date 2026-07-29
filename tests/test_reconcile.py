"""Tests for the deterministic reconciliation engine."""

import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.observation_capture import EVENT_GIT_REVERT, EVENT_VERIFICATION_SUCCESS
from turingmind_mcp.reconcile import (
    RECURRENCE_THRESHOLD,
    SCOPE_CHURN_THRESHOLD,
    ReconciliationEngine,
    is_low_value_promotion_cluster,
    repos_with_activity,
    _extract_revert_files,
    _scope_matches_file,
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
    def _add_observations(self, count: int, content: str, **kwargs):
        for _ in range(count):
            self.db.create_observation(
                repo=REPO, event_type="edit_cluster", content=content,
                source="cursor-hook",
                **kwargs,
            )

    def test_recurring_observations_become_candidate(self):
        """N similar observations mine one candidate pattern + queue finding."""
        content = "targeted_fix/high: jwt validation fix in src/auth/jwt_middleware.py"
        self._add_observations(
            RECURRENCE_THRESHOLD,
            content,
            evidence=[{"type": "files", "content": "src/auth/jwt_middleware.py"}],
        )
        stats = self.engine.mine_recurrence(REPO)
        self.assertEqual(stats["patterns_mined"], 1)
        self.assertEqual(stats["observations_accepted"], RECURRENCE_THRESHOLD)

        # Candidate is NOT active — promotion needs queue approval
        candidates = self.db.list_memory_entries(
            repo=REPO, memory_type="learned_pattern", status="candidate"
        )
        self.assertEqual(len(candidates), 1)
        self.assertIn("Recurring", candidates[0]["content"])
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
        self._add_observations(
            RECURRENCE_THRESHOLD,
            "targeted_fix/high: payment flow fix in src/payments/handler.py",
            evidence=[{"type": "files", "content": "src/payments/handler.py"}],
        )
        self.engine.mine_recurrence(REPO)
        stats2 = self.engine.mine_recurrence(REPO)
        self.assertEqual(stats2["patterns_mined"], 0)
        candidates = self.db.list_memory_entries(
            repo=REPO, memory_type="learned_pattern", status="candidate"
        )
        self.assertEqual(len(candidates), 1)

    def test_refactor_burst_not_promoted(self):
        content = "refactor_burst/low: 42 files across 4 module(s) in 0s — likely a refactor"
        self._add_observations(RECURRENCE_THRESHOLD, content)
        stats = self.engine.mine_recurrence(REPO)
        self.assertEqual(stats["patterns_mined"], 0)
        self.assertEqual(stats["observations_rejected_noise"], RECURRENCE_THRESHOLD)
        self.assertEqual(self.db.list_findings(repo=REPO), [])

    def test_non_code_not_promoted(self):
        content = "non_code/low: non-code edit: README.md"
        self._add_observations(RECURRENCE_THRESHOLD, content)
        stats = self.engine.mine_recurrence(REPO)
        self.assertEqual(stats["patterns_mined"], 0)
        self.assertEqual(stats["observations_rejected_noise"], RECURRENCE_THRESHOLD)

    def test_bulk_edit_not_promoted(self):
        content = "cross_module/high: 8 files across 3 module(s) — cross-cutting change"
        self._add_observations(RECURRENCE_THRESHOLD, content)
        stats = self.engine.mine_recurrence(REPO)
        self.assertEqual(stats["patterns_mined"], 0)
        self.assertGreaterEqual(stats["observations_rejected_noise"], RECURRENCE_THRESHOLD)

    def test_git_churn_not_promoted(self):
        content = "git churn: path 'src/hot.py' modified since last reconcile (HEAD abc12345)"
        for _ in range(RECURRENCE_THRESHOLD):
            self.db.create_observation(
                repo=REPO, event_type="git_churn", content=content, source="git-churn",
            )
        stats = self.engine.mine_recurrence(REPO)
        self.assertEqual(stats["patterns_mined"], 0)
        self.assertEqual(stats["observations_rejected_noise"], RECURRENCE_THRESHOLD)

    def test_generic_edit_cluster_not_promoted(self):
        content = "targeted_fix/high: 1 code file(s) changed — likely a targeted fix or remediation"
        self._add_observations(RECURRENCE_THRESHOLD, content)
        stats = self.engine.mine_recurrence(REPO)
        self.assertEqual(stats["patterns_mined"], 0)
        self.assertEqual(stats["observations_rejected_noise"], RECURRENCE_THRESHOLD)

    def test_same_file_recurrence_still_promoted(self):
        """Same file in evidence across cluster → valid promotion candidate."""
        content = "targeted_fix/high: 1 code file(s) changed — likely a targeted fix or remediation"
        self._add_observations(
            RECURRENCE_THRESHOLD,
            content,
            evidence=[{"type": "files", "content": "src/auth/jwt_middleware.py"}],
        )
        stats = self.engine.mine_recurrence(REPO)
        self.assertEqual(stats["patterns_mined"], 1)
        self.assertEqual(stats.get("observations_rejected_noise", 0), 0)

    def test_is_low_value_promotion_cluster_unit(self):
        exemplar = {"event_type": "edit_cluster", "content": "refactor_burst/low: big refactor"}
        self.assertTrue(is_low_value_promotion_cluster(exemplar, [exemplar]))
        good = {
            "event_type": "edit_cluster",
            "content": "targeted_fix/high: fix",
            "evidence": [{"type": "files", "content": "src/a.py"}],
        }
        cluster = [good] * RECURRENCE_THRESHOLD
        self.assertFalse(is_low_value_promotion_cluster(good, cluster))


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

    def test_run_batches_pending_observations(self):
        batch_repo = "test/batch"
        for _ in range(60):
            self.db.create_observation(
                repo=batch_repo,
                event_type="edit_cluster",
                content=uuid.uuid4().hex,
            )
        with mock.patch.dict(os.environ, {"TURINGMIND_RECONCILE_OBS_BATCH": "10"}):
            stats = self.engine.run(batch_repo)
        self.assertEqual(stats["observations_total_pending"], 60)
        self.assertEqual(stats["observations_batch_limit"], 10)
        self.assertEqual(stats["observations_batch_processed"], 10)
        self.assertEqual(stats["observations_remaining"], 60)

    def test_mine_recurrence_respects_batch_limit(self):
        batch_repo = "test/fifo"
        for _ in range(15):
            self.db.create_observation(
                repo=batch_repo,
                event_type="edit_cluster",
                content=f"{uuid.uuid4().hex}-fifo",
            )
        stats = self.engine.mine_recurrence(batch_repo, batch_limit=5)
        self.assertEqual(stats["observations_batch_processed"], 5)
        self.assertEqual(self.db.count_observations(batch_repo, "pending"), 15)


class TestStalePendingObservations(ReconcileTestCase):
    def test_expire_stale_pending_observations(self):
        repo = "test/stale"
        oid = self.db.create_observation(
            repo=repo, event_type="edit_cluster", content="old singleton",
        )
        self.db.conn.execute(
            "UPDATE observations SET created_at = datetime('now', '-48 hours') WHERE observation_id = ?",
            (oid,),
        )
        self.db.conn.commit()
        expired = self.db.expire_stale_pending_observations(repo, 24)
        self.assertEqual(expired, 1)
        self.assertEqual(self.db.count_observations(repo, "pending"), 0)

    def test_unclustered_pending_rejected_after_cutoff(self):
        repo = "test/unclustered"
        oid = self.db.create_observation(
            repo=repo, event_type="edit_cluster", content="solo old obs",
        )
        self.db.conn.execute(
            "UPDATE observations SET created_at = datetime('now', '-48 hours') WHERE observation_id = ?",
            (oid,),
        )
        self.db.conn.commit()
        with mock.patch.dict(os.environ, {"TURINGMIND_UNCLUSTERED_PENDING_HOURS": "24"}):
            stats = self.engine.mine_recurrence(repo, batch_limit=10)
        self.assertEqual(stats["observations_rejected_unclustered"], 1)
        self.assertEqual(self.db.count_observations(repo, "pending"), 0)

    def test_prune_stale_in_full_run(self):
        repo = "test/prune-run"
        oid = self.db.create_observation(
            repo=repo, event_type="edit_cluster", content="week old",
        )
        self.db.conn.execute(
            "UPDATE observations SET created_at = datetime('now', '-200 hours') WHERE observation_id = ?",
            (oid,),
        )
        self.db.conn.commit()
        with mock.patch.dict(os.environ, {"TURINGMIND_STALE_PENDING_OBS_HOURS": "168"}):
            stats = self.engine.run(repo)
        self.assertEqual(stats["observations_expired_stale"], 1)
        self.assertEqual(self.db.count_observations(repo, "pending"), 0)


class TestScopeMatching(unittest.TestCase):
    def test_scope_matches_file_paths(self):
        self.assertTrue(_scope_matches_file("src/auth/jwt.py", "src/auth/jwt.py"))
        self.assertTrue(_scope_matches_file("jwt.py", "src/auth/jwt.py"))
        self.assertFalse(_scope_matches_file("repo", "src/auth/jwt.py"))

    def test_extract_revert_files_from_evidence(self):
        obs = {
            "content": 'Git revert abc12345: Revert "bad"',
            "evidence": [{"type": "files", "content": "src/a.py, src/b.py"}],
        }
        files = _extract_revert_files(obs)
        self.assertEqual(files, ["src/a.py", "src/b.py"])


class TestRevertPenalty(ReconcileTestCase):
    def test_revert_penalizes_scope_matched_memory(self):
        mem_id = self.db.create_memory_entry(
            repo=REPO, memory_type="learned_pattern",
            content="always validate jwt", scope="src/auth/jwt.py", confidence=0.8,
        )
        self.db.create_observation(
            repo=REPO,
            event_type=EVENT_GIT_REVERT,
            content='Git revert deadbeef: Revert "jwt change"',
            source="antigravity-hook",
            evidence=[{"type": "files", "content": "src/auth/jwt.py"}],
        )
        stats = self.engine.apply_revert_penalties(REPO)
        self.assertEqual(stats["revert_observations"], 1)
        self.assertEqual(stats["revert_memories_penalized"], 1)
        self.assertLess(self.db.get_memory_entry(mem_id)["confidence"], 0.8)
        findings = self.db.list_findings(repo=REPO)
        self.assertEqual(findings[0]["finding_type"], "revert_penalty")
        self.assertEqual(len(self.db.list_observations(repo=REPO)), 0)


class TestInvalidationDecay(ReconcileTestCase):
    def test_missing_scope_file_decays_memory(self):
        workspace = Path(self.temp_dir) / "ws"
        (workspace / "src").mkdir(parents=True)
        stale = workspace / "src" / "removed.py"
        stale.write_text("gone", encoding="utf-8")
        mem_id = self.db.create_memory_entry(
            repo=REPO, memory_type="learned_pattern",
            content="pattern on removed file", scope="src/removed.py", confidence=0.7,
        )
        stale.unlink()
        with mock.patch.dict(os.environ, {"TURINGMIND_WORKSPACE_DIR": str(workspace)}):
            stats = self.engine.apply_invalidation_decay(REPO)
        self.assertEqual(stats["invalidation_missing_file"], 1)
        self.assertLess(self.db.get_memory_entry(mem_id)["confidence"], 0.7)
        findings = self.db.list_findings(repo=REPO)
        self.assertEqual(findings[0]["finding_type"], "invalidation_decay")

    def test_churn_decay_is_idempotent(self):
        mem_id = self.db.create_memory_entry(
            repo=REPO, memory_type="learned_pattern",
            content="hot file pattern", scope="src/hot.py", confidence=0.8,
        )
        workspace = Path(self.temp_dir) / "ws2"
        (workspace / "src").mkdir(parents=True)
        (workspace / "src" / "hot.py").write_text("x", encoding="utf-8")
        for i in range(SCOPE_CHURN_THRESHOLD):
            self.db.create_observation(
                repo=REPO, event_type="edit_cluster",
                content=f"targeted_fix: edit src/hot.py iteration {i}",
            )
        with mock.patch.dict(os.environ, {"TURINGMIND_WORKSPACE_DIR": str(workspace)}):
            stats1 = self.engine.apply_invalidation_decay(REPO)
            first_conf = self.db.get_memory_entry(mem_id)["confidence"]
            stats2 = self.engine.apply_invalidation_decay(REPO)
            second_conf = self.db.get_memory_entry(mem_id)["confidence"]
        self.assertEqual(stats1["invalidation_churn"], 1)
        self.assertEqual(stats2["invalidation_churn"], 0)
        self.assertLess(first_conf, 0.8)
        self.assertEqual(first_conf, second_conf)


class TestVerificationReinforcement(ReconcileTestCase):
    def _seed_spec_node(self, node_id: str = "node-1", repo: str = REPO) -> None:
        from turingmind_mcp.unified_schema import initialize_v2_schema

        cursor = self.db.conn.cursor()
        initialize_v2_schema(cursor)
        cursor.execute(
            """
            INSERT OR IGNORE INTO spec_nodes (
                id, repo, level, surface_type, status, stage, confidence,
                data, created_at, updated_at
            ) VALUES (?, ?, 'L1_FILE', 'internal', 'verified', 'verified', 1.0, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (node_id, repo),
        )
        self.db.conn.commit()

    def test_success_obs_reinforces_node_memory(self):
        self._seed_spec_node()
        mem_id = self.db.create_memory_entry(
            repo=REPO, memory_type="learned_pattern",
            content="failure then fix", scope="src/x.py", confidence=0.5,
            node_id="node-1",
        )
        self.db.create_observation(
            repo=REPO,
            event_type=EVENT_VERIFICATION_SUCCESS,
            content="Verification succeeded on 'X'",
            node_id="node-1",
        )
        stats = self.engine.reinforce_verification_success(REPO)
        self.assertEqual(stats["verification_success_processed"], 1)
        self.assertEqual(stats["memories_reinforced"], 1)
        self.assertGreater(self.db.get_memory_entry(mem_id)["confidence"], 0.5)
        self.assertEqual(len(self.db.list_observations(repo=REPO)), 0)


class TestEventTypeClustering(ReconcileTestCase):
    def test_different_event_types_not_clustered(self):
        for _ in range(2):
            self.db.create_observation(
                repo=REPO, event_type="edit_cluster",
                content="targeted_fix/high: change in src/payment.py",
            )
        self.db.create_observation(
            repo=REPO, event_type="edit_cluster",
            content="targeted_fix/high: change in src/payment.py",
        )
        self.db.create_observation(
            repo=REPO, event_type=EVENT_GIT_REVERT,
            content="targeted_fix/high: change in src/payment.py",
        )
        stats = self.engine.mine_recurrence(REPO)
        self.assertEqual(stats["patterns_mined"], 1)
        self.assertEqual(stats["observations_accepted"], 3)
        self.assertEqual(len(self.db.list_observations(repo=REPO)), 1)


class TestDuplicateMergeSuggestions(ReconcileTestCase):
    def test_paraphrase_pair_creates_semantic_duplicate_finding(self):
        self.db.create_memory_entry(
            repo=REPO,
            memory_type="learned_pattern",
            content="Always use async await for IO operations in handlers",
            scope="repo",
        )
        self.db.create_memory_entry(
            repo=REPO,
            memory_type="learned_pattern",
            content="Use async await for all IO operations in handlers",
            scope="repo",
        )
        stats = self.engine.suggest_duplicate_merges(REPO)
        self.assertGreaterEqual(stats["duplicate_pairs_suggested"], 1)
        findings = [
            f for f in self.db.list_findings(repo=REPO)
            if f["finding_type"] == "semantic_duplicate"
        ]
        self.assertGreaterEqual(len(findings), 1)

    def test_identical_content_not_flagged(self):
        content = "Never commit secrets to the repository"
        self.db.create_memory_entry(
            repo=REPO, memory_type="explicit_rule", content=content, scope="repo",
        )
        self.db.create_memory_entry(
            repo=REPO, memory_type="explicit_rule", content=content, scope="repo",
        )
        stats = self.engine.suggest_duplicate_merges(REPO)
        self.assertEqual(stats["duplicate_pairs_suggested"], 0)


if __name__ == "__main__":
    unittest.main()
