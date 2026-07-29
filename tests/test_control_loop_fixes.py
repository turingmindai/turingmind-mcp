"""P0/P1 control-loop fixes: conflict gate, queue warn, hygiene, ground, commit distill."""

from __future__ import annotations

import pytest

from turingmind_mcp.grounding import compose_ground
from turingmind_mcp.memory_distillation import propose_commit_candidates
from turingmind_mcp.memory_manager import MemoryManager
from turingmind_mcp.profile_config import filter_decision_queue_gaps
from turingmind_mcp.reconcile import ReconciliationEngine
from turingmind_mcp.server import check_control_plane_policies


def test_detect_conflicts_ignores_session_context(memory_db, tier_repo):
    mgr = MemoryManager(memory_db)
    rule = mgr.create_explicit_rule(
        repo=tier_repo,
        content="Always use async/await for network calls",
        scope="repo",
    )
    session_id, _ = mgr.create_session_context(
        repo=tier_repo,
        content="non-code edit: 5 files — no graph impact. Always use async/await",
        scope="repo",
        evidence=[{"type": "edit_cluster", "content": "non_code/low"}],
    )
    # Session vs rule must not conflict
    assert mgr.detect_conflicts(tier_repo, session_id) == []

    # Rule vs pattern still can
    pat_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="Never use async/await for network calls",
        scope="repo",
        confidence=0.8,
        status="active",
    )
    conflicts = mgr.detect_conflicts(tier_repo, pat_id)
    assert len(conflicts) > 0
    assert rule["memory_id"]


def test_tm_queue_003_only_actionable_critical(memory_db, tier_repo):
    # Flood of low/medium promotions must not warn
    for i in range(55):
        memory_db.create_finding(
            repo=tier_repo,
            finding_type="promotion_candidate",
            severity="medium",
            action=f"Promote noise {i}",
            dedup_key=f"noise-{i}",
        )
    assert check_control_plane_policies(tier_repo, memory_db) is None

    memory_db.create_finding(
        repo=tier_repo,
        finding_type="security_regression",
        severity="critical",
        action="Unresolved critical SQL injection risk",
        dedup_key="sec-sql",
    )
    warn = check_control_plane_policies(tier_repo, memory_db)
    assert warn is not None
    assert "TM-QUEUE-003" in warn


def test_filter_hides_low_promotion_noise(monkeypatch):
    monkeypatch.setenv("TURINGMIND_PROFILE", "memory")
    gaps = [
        {
            "gap_type": "promotion_candidate",
            "severity": "low",
            "action": "non-code edit: junk",
        },
        {
            "gap_type": "memory_conflict",
            "severity": "high",
            "action": "Resolve rule conflict",
        },
        {
            "gap_type": "promotion_candidate",
            "severity": "medium",
            "action": "Promote commit-derived pattern? Prefer ground wrapper",
        },
    ]
    filtered = filter_decision_queue_gaps(gaps, scope="memory")
    types = {g["gap_type"] for g in filtered}
    assert "memory_conflict" in types
    assert not any(g.get("severity") == "low" for g in filtered)


def test_expire_cluster_session_context(memory_db, tier_repo):
    mgr = MemoryManager(memory_db)
    junk_id, _ = mgr.create_session_context(
        repo=tier_repo,
        content="non-code edit: README.md — no graph impact",
        scope="repo",
        evidence=[],
    )
    good_id, _ = mgr.create_session_context(
        repo=tier_repo,
        content="Working on grounding control loop; blocked on Cursor inject bug",
        scope="repo",
        evidence=[],
    )
    engine = ReconciliationEngine(memory_db)
    stats = engine.expire_cluster_session_context(tier_repo)
    assert stats["cluster_session_context_expired"] >= 1
    assert memory_db.get_memory_entry(junk_id)["status"] == "deprecated"
    assert memory_db.get_memory_entry(good_id)["status"] == "active"


def test_compose_ground(memory_db, tier_repo):
    mgr = MemoryManager(memory_db)
    mgr.create_explicit_rule(
        repo=tier_repo,
        content="Always call turingmind_ground at session start when searching",
        scope="repo",
    )
    memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="Cluster capture is observations-only",
        scope="hooks",
        confidence=0.9,
        status="active",
    )
    payload = compose_ground(
        mgr,
        repo=tier_repo,
        query="ground",
        limit=5,
        decision_queue_builder=lambda **kw: {"queue": []},
    )
    assert payload["rules"]
    assert payload["patterns"]
    assert "digest" in payload
    assert "TuringMind" in payload["digest"]


def test_propose_commit_candidates(memory_db, tier_repo):
    result = propose_commit_candidates(
        memory_db,
        repo=tier_repo,
        message="fix: stop cluster session_context dual-write in capture path",
        files=["plugins/turingmind-memory/hooks/scripts/lib/cluster-capture.js"],
    )
    assert result["skipped"] is False
    assert 1 <= len(result["candidates"]) <= 2
    mid = result["candidates"][0]["memory_id"]
    entry = memory_db.get_memory_entry(mid)
    assert entry["status"] == "candidate"
    assert entry["type"] == "learned_pattern"

    skip = propose_commit_candidates(
        memory_db,
        repo=tier_repo,
        message="wip",
        files=["README.md"],
    )
    assert skip["skipped"] is True
