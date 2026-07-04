"""Branch lifecycle tests (TC-BR-21 … TC-BR-27)."""

from __future__ import annotations

import json

import pytest

from turingmind_mcp.reconcile import (
    ReconciliationEngine,
    _merged_source_branch,
    apply_finding_resolution,
)


@pytest.fixture
def tier_repo() -> str:
    return "test-org/lifecycle-sandbox"


def _merge_observation(db, repo: str, *, branch: str = "main", merged: str = "feature/x"):
    return db.create_observation(
        repo=repo,
        event_type="merge_commit",
        content=f"Merge commit: merged {merged} into {branch}",
        source="cursor-hook",
        confidence=0.5,
        branch=branch,
        head_sha="a" * 40,
        git_context=json.dumps(
            {"branch": branch, "head": "a" * 40, "dirty": False, "default_branch": branch}
        ),
        evidence=[
            {"type": "commit_sha", "content": "a" * 40},
            {"type": "merge_branches", "content": f"{branch},{merged}"},
        ],
    )


@pytest.mark.branch_lifecycle
def test_tc_br_21_merge_observation_branch_parsing(tier_repo):
    """TC-BR-21: merge_commit obs yields merged-away branch name."""
    obs = {
        "branch": "main",
        "git_context": json.dumps({"default_branch": "main"}),
        "evidence": [{"type": "merge_branches", "content": "main,feature/memory-loop"}],
    }
    assert _merged_source_branch(obs) == "feature/memory-loop"


@pytest.mark.branch_lifecycle
def test_tc_br_22_merge_promotion_finding(memory_db, tier_repo):
    """TC-BR-22: reconcile proposes branch_promotion after merge_commit obs."""
    source_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="Feature-only JWT validation pattern",
        scope="src/auth/jwt.py",
        confidence=0.85,
        status="active",
        branch="feature/x",
        scope_tier="branch",
    )
    _merge_observation(memory_db, tier_repo, merged="feature/x")

    engine = ReconciliationEngine(memory_db)
    stats = engine.branch_lifecycle(tier_repo)

    assert stats["branch_promotions_suggested"] == 1
    findings = memory_db.list_findings(repo=tier_repo, status="pending")
    promo = next(f for f in findings if f["finding_type"] == "branch_promotion")
    assert promo["memory_id"] == source_id


@pytest.mark.branch_lifecycle
def test_tc_br_23_promotion_no_auto_activate(memory_db, tier_repo):
    """TC-BR-23: branch_promotion finding does not auto-create L4 memory."""
    memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="Needs consent",
        scope="src/a.py",
        status="active",
        branch="feature/y",
        scope_tier="branch",
    )
    _merge_observation(memory_db, tier_repo, merged="feature/y")

    ReconciliationEngine(memory_db).branch_lifecycle(tier_repo)

    promoted = [
        m
        for m in memory_db.list_memory_entries(repo=tier_repo, status="active")
        if m.get("promoted_from")
    ]
    assert promoted == []


@pytest.mark.branch_lifecycle
def test_tc_br_24_promotion_accept_l4_copy(memory_db, tier_repo):
    """TC-BR-24: accepting branch_promotion creates L4 copy with lineage."""
    source_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="Always validate JWT issuer",
        scope="src/auth/jwt.py",
        status="active",
        branch="feature/auth",
        scope_tier="branch",
    )
    _merge_observation(memory_db, tier_repo, merged="feature/auth")
    engine = ReconciliationEngine(memory_db)
    engine.branch_lifecycle(tier_repo)

    finding = next(
        f
        for f in memory_db.list_findings(repo=tier_repo, status="pending")
        if f["finding_type"] == "branch_promotion"
    )
    apply_finding_resolution(memory_db, finding["finding_id"], "actioned")

    l4 = [
        m
        for m in memory_db.list_memory_entries(repo=tier_repo, status="active")
        if m.get("promoted_from") == source_id
    ]
    assert len(l4) == 1
    assert l4[0]["branch"] is None
    assert l4[0]["scope_tier"] == "repo"
    assert memory_db.get_memory_entry(source_id)["status"] == "deprecated"


@pytest.mark.branch_lifecycle
def test_tc_br_25_stale_branch_archive_finding(memory_db, tier_repo):
    """TC-BR-25: inactive branch surfaces archive_branch_memories finding."""
    from datetime import datetime, timedelta, timezone

    mem_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="stale branch wisdom",
        scope="src/old.py",
        status="active",
        branch="feature/stale",
        scope_tier="branch",
    )
    stale_ts = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    memory_db.conn.execute(
        "UPDATE memory_entries SET updated_at = ? WHERE memory_id = ?",
        (stale_ts, mem_id),
    )
    memory_db.conn.commit()

    stats = ReconciliationEngine(memory_db).branch_lifecycle(tier_repo)
    assert stats["branch_archives_suggested"] == 1
    findings = memory_db.list_findings(repo=tier_repo, status="pending")
    assert any(f["finding_type"] == "archive_branch_memories" for f in findings)


@pytest.mark.branch_lifecycle
def test_tc_br_27_e2e_merge_promotion_funnel(memory_db, tier_repo):
    """TC-BR-27: merge obs → queue → accept promotes to L4 only after consent."""
    source_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="Billing decimal rule on feature branch",
        scope="src/billing/invoice.py",
        status="candidate",
        branch="feature/billing",
        scope_tier="branch",
    )
    _merge_observation(memory_db, tier_repo, merged="feature/billing")

    engine = ReconciliationEngine(memory_db)
    stats = engine.run(tier_repo)
    assert stats.get("branch_promotions_suggested", 0) >= 1

    finding = next(
        f
        for f in memory_db.list_findings(repo=tier_repo, status="pending")
        if f["finding_type"] == "branch_promotion" and f["memory_id"] == source_id
    )
    apply_finding_resolution(memory_db, finding["finding_id"], "actioned")

    l4 = memory_db.get_memory_entry(source_id)
    assert l4["status"] == "deprecated"
    promoted = [
        m
        for m in memory_db.list_memory_entries(repo=tier_repo, status="active")
        if m.get("promoted_from") == source_id
    ]
    assert len(promoted) == 1
