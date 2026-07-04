"""Extracted spec conformance tests (SPEC-BR-01 … SPEC-BR-11).

Phase 4.1: SPEC-BR-08 active. Later phases: tests are defined but skipped until implemented.
Reference: docs/branch-aware-memory-plan.html#extracted-specs
"""

from __future__ import annotations

import pytest

from turingmind_mcp.git_context import (
    derive_scope_tier,
    git_context_from_payload,
    normalize_scope_tier_write,
)


# ── SPEC-BR-08 (Phase 4.1) ───────────────────────────────────────────────────


@pytest.mark.branch_spec
def test_tc_br_s12_scope_tier_derivation(api_client, tier_repo, sample_git_payload):
    """TC-BR-S12: API returns consistent scope_tier for branch + dirty combos."""
    client, db = api_client

    clean = client.post(
        "/api/v2/memory",
        json={
            "repo": tier_repo,
            "type": "explicit_rule",
            "content": "branch tier",
            "scope": "src/a.py",
            "git": sample_git_payload,
        },
    )
    assert clean.status_code == 200
    assert db.get_memory_entry(clean.json()["memory_id"])["scope_tier"] == "branch"

    dirty = client.post(
        "/api/v2/memory",
        json={
            "repo": tier_repo,
            "type": "explicit_rule",
            "content": "working tree tier",
            "scope": "src/b.py",
            "git": {**sample_git_payload, "dirty": True},
        },
    )
    assert dirty.status_code == 200
    assert db.get_memory_entry(dirty.json()["memory_id"])["scope_tier"] == "working_tree"

    repo_wide = client.post(
        "/api/v2/memory",
        json={
            "repo": tier_repo,
            "type": "explicit_rule",
            "content": "repo tier",
            "scope": "repo",
        },
    )
    assert repo_wide.status_code == 200
    assert db.get_memory_entry(repo_wide.json()["memory_id"])["scope_tier"] == "repo"


@pytest.mark.branch_spec
def test_tc_br_fs08_reject_inconsistent_tier(api_client, tier_repo, sample_git_payload):
    """TC-BR-FS08: scope_tier=working_tree with dirty=false → 400."""
    client, _db = api_client
    response = client.post(
        "/api/v2/memory",
        json={
            "repo": tier_repo,
            "type": "explicit_rule",
            "content": "bad tier",
            "scope": "src/x.py",
            "git": {
                **sample_git_payload,
                "dirty": False,
                "scope_tier": "working_tree",
            },
        },
    )
    assert response.status_code == 400


@pytest.mark.branch_spec
def test_normalize_scope_tier_unit():
    """Unit-level SPEC-BR-08 derivation."""
    assert normalize_scope_tier_write(None, False) == "repo"
    assert normalize_scope_tier_write("feature/x", False) == "branch"
    assert normalize_scope_tier_write("feature/x", True) == "working_tree"
    with pytest.raises(ValueError):
        normalize_scope_tier_write("feature/x", False, "working_tree")


# ── SPEC-BR-01 (Phase 4.2 — Option B) ────────────────────────────────────────


@pytest.mark.branch_spec
def test_tc_br_s01_l2_recall_path_exists(
    branch_recall_enabled, api_client, tier_repo, sample_git_payload
):
    """TC-BR-S01: branch-scoped session_context reachable via include_session_context."""
    client, _db = api_client
    dirty = {**sample_git_payload, "dirty": True}
    client.post(
        "/api/v2/memory",
        json={
            "repo": tier_repo,
            "type": "session_context",
            "content": "L2 path",
            "scope": "repo",
            "git": dirty,
        },
    )
    response = client.get(
        "/api/v2/memory/relevant",
        params={
            "repo": tier_repo,
            "branch": dirty["branch"],
            "head": dirty["head"],
            "dirty": "true",
            "include_session_context": "true",
        },
    )
    assert response.status_code == 200
    assert len(response.json()["entries"]) >= 1


@pytest.mark.branch_spec
def test_tc_br_s02_default_relevant_unchanged_or_explicit(
    branch_recall_enabled, api_client, tier_repo, sample_git_payload
):
    """TC-BR-S02: default relevant excludes session_context (Option B)."""
    client, _db = api_client
    client.post(
        "/api/v2/memory",
        json={
            "repo": tier_repo,
            "type": "session_context",
            "content": "not by default",
            "scope": "repo",
            "git": sample_git_payload,
        },
    )
    response = client.get(
        "/api/v2/memory/relevant",
        params={"repo": tier_repo, "branch": sample_git_payload["branch"], "head": sample_git_payload["head"]},
    )
    assert response.json()["entries"] == []


@pytest.mark.branch_spec
def test_tc_br_fs01_no_phantom_l2_boost(branch_recall_enabled, api_client, tier_repo, sample_git_payload):
    """TC-BR-FS01: excluded session_context must not appear in default relevant."""
    client, _db = api_client
    client.post(
        "/api/v2/memory",
        json={
            "repo": tier_repo,
            "type": "session_context",
            "content": "phantom",
            "scope": "repo",
            "git": {**sample_git_payload, "dirty": True},
        },
    )
    response = client.get(
        "/api/v2/memory/relevant",
        params={
            "repo": tier_repo,
            "branch": sample_git_payload["branch"],
            "head": sample_git_payload["head"],
            "dirty": "true",
        },
    )
    assert response.json()["entries"] == []


# ── SPEC-BR-02 (Phase 4.3) ───────────────────────────────────────────────────


RECURRENCE_CONTENT = (
    "targeted_fix/high: 1 code file changed in src/auth/jwt_middleware.py"
)


@pytest.mark.branch_spec
def test_tc_br_s03_recurrence_same_branch(memory_db, tier_repo):
    """TC-BR-S03: recurrence candidate inherits branch."""
    from turingmind_mcp.reconcile import RECURRENCE_THRESHOLD, ReconciliationEngine

    engine = ReconciliationEngine(memory_db)
    for _ in range(RECURRENCE_THRESHOLD):
        memory_db.create_observation(
            repo=tier_repo,
            event_type="edit_cluster",
            content=RECURRENCE_CONTENT,
            source="cursor-hook",
            branch="feature/a",
        )
    stats = engine.mine_recurrence(tier_repo)
    assert stats["patterns_mined"] == 1

    candidates = memory_db.list_memory_entries(
        repo=tier_repo, memory_type="learned_pattern", status="candidate"
    )
    assert len(candidates) == 1
    assert candidates[0]["branch"] == "feature/a"
    assert candidates[0]["scope"] != "repo"

    findings = memory_db.list_findings(repo=tier_repo)
    assert findings[0]["finding_type"] == "promotion_candidate"


@pytest.mark.branch_spec
def test_tc_br_s04_recurrence_no_cross_branch(memory_db, tier_repo):
    """TC-BR-S04: paraphrase obs on two branches → two candidates."""
    from turingmind_mcp.reconcile import RECURRENCE_THRESHOLD, ReconciliationEngine

    engine = ReconciliationEngine(memory_db)
    for branch in ("feature/a", "feature/b"):
        for _ in range(RECURRENCE_THRESHOLD):
            memory_db.create_observation(
                repo=tier_repo,
                event_type="edit_cluster",
                content=RECURRENCE_CONTENT,
                source="cursor-hook",
                branch=branch,
            )
    stats = engine.mine_recurrence(tier_repo)
    assert stats["patterns_mined"] == 2

    candidates = memory_db.list_memory_entries(
        repo=tier_repo, memory_type="learned_pattern", status="candidate"
    )
    branches = {c["branch"] for c in candidates}
    assert branches == {"feature/a", "feature/b"}


@pytest.mark.branch_spec
def test_tc_br_fs02_no_flatten_to_l4(memory_db, tier_repo):
    """TC-BR-FS02: cross-branch obs must not become branch=NULL candidate."""
    from turingmind_mcp.reconcile import RECURRENCE_THRESHOLD, ReconciliationEngine

    engine = ReconciliationEngine(memory_db)
    # 2 per branch: below threshold per branch, but would merge if branch ignored
    per_branch = RECURRENCE_THRESHOLD - 1
    for branch in ("feature/a", "feature/b"):
        for _ in range(per_branch):
            memory_db.create_observation(
                repo=tier_repo,
                event_type="edit_cluster",
                content=RECURRENCE_CONTENT,
                source="cursor-hook",
                branch=branch,
            )
    stats = engine.mine_recurrence(tier_repo)
    assert stats["patterns_mined"] == 0

    candidates = memory_db.list_memory_entries(
        repo=tier_repo, memory_type="learned_pattern", status="candidate"
    )
    assert not any(c.get("branch") is None and c.get("scope_tier") == "repo" for c in candidates)


# ── SPEC-BR-03 (Phase 4.3) ───────────────────────────────────────────────────


@pytest.mark.branch_spec
def test_tc_br_s05_per_branch_git_cursor(memory_db, tier_repo, git_sandbox, monkeypatch):
    """TC-BR-S05: churn on branch B does not use branch A cursor."""
    from unittest import mock

    from turingmind_mcp.git_churn import GitChurnSnapshot
    from turingmind_mcp.reconcile import ReconciliationEngine

    mem_a = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="feature a pattern",
        scope="src/hot.py",
        confidence=0.8,
        branch="feature/a",
        scope_tier="branch",
    )
    mem_b = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="feature b pattern",
        scope="src/hot.py",
        confidence=0.8,
        branch="feature/b",
        scope_tier="branch",
    )
    memory_db.set_branch_git_cursor(tier_repo, "feature/a", "a" * 40)
    memory_db.set_branch_git_cursor(tier_repo, "feature/b", "b" * 40)

    hot_file = git_sandbox / "src" / "hot.py"
    hot_file.parent.mkdir(parents=True, exist_ok=True)
    hot_file.write_text("print('hot')", encoding="utf-8")

    engine = ReconciliationEngine(memory_db)
    monkeypatch.setenv("TURINGMIND_WORKSPACE_DIR", str(git_sandbox))

    with mock.patch("turingmind_mcp.reconcile.collect_git_context") as mock_ctx:
        from turingmind_mcp.git_context import GitContext

        mock_ctx.return_value = GitContext(
            branch="feature/b", head="c" * 40, dirty=False
        )
        with mock.patch("turingmind_mcp.reconcile.collect_git_churn") as mock_churn:
            mock_churn.return_value = GitChurnSnapshot(
                head="c" * 40,
                modified=frozenset(["src/hot.py"]),
                deleted=frozenset(),
            )
            stats = engine.apply_invalidation_decay(tier_repo)

    assert stats["invalidation_git_churn"] == 1
    assert memory_db.get_memory_entry(mem_b)["confidence"] < 0.8
    assert memory_db.get_memory_entry(mem_a)["confidence"] == 0.8
    mock_churn.assert_called_once()
    assert mock_churn.call_args.kwargs.get("since_ref") == "b" * 40


@pytest.mark.branch_spec
def test_tc_br_s06_cursor_restored_per_branch(memory_db, tier_repo):
    """TC-BR-S06: returning to branch A uses A's last head."""
    memory_db.set_branch_git_cursor(tier_repo, "feature/a", "old_a_head" + "0" * 32)
    memory_db.set_branch_git_cursor(tier_repo, "feature/b", "old_b_head" + "0" * 32)

    assert memory_db.get_branch_git_cursor(tier_repo, "feature/a") == "old_a_head" + "0" * 32
    memory_db.set_branch_git_cursor(tier_repo, "feature/a", "new_a_head" + "0" * 32)
    assert memory_db.get_branch_git_cursor(tier_repo, "feature/a") == "new_a_head" + "0" * 32
    assert memory_db.get_branch_git_cursor(tier_repo, "feature/b") == "old_b_head" + "0" * 32


@pytest.mark.branch_spec
def test_tc_br_fs03_no_cross_branch_churn_decay(memory_db, tier_repo, monkeypatch):
    """TC-BR-FS03: main churn must not decay feature-branch memories."""
    from unittest import mock

    from turingmind_mcp.git_churn import GitChurnSnapshot
    from turingmind_mcp.reconcile import ReconciliationEngine

    mem_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="feature pattern",
        scope="src/hot.py",
        confidence=0.8,
        branch="feature/x",
        scope_tier="branch",
    )
    engine = ReconciliationEngine(memory_db)

    with mock.patch("turingmind_mcp.reconcile.collect_git_context") as mock_ctx:
        from turingmind_mcp.git_context import GitContext

        mock_ctx.return_value = GitContext(branch="main", head="d" * 40, dirty=False)
        with mock.patch("turingmind_mcp.reconcile.collect_git_churn") as mock_churn:
            mock_churn.return_value = GitChurnSnapshot(
                head="d" * 40,
                modified=frozenset(["src/hot.py"]),
                deleted=frozenset(),
            )
            engine.apply_invalidation_decay(tier_repo)

    assert memory_db.get_memory_entry(mem_id)["confidence"] == 0.8


# ── SPEC-BR-04 (Phase 4.2) ───────────────────────────────────────────────────


@pytest.mark.branch_spec
def test_tc_br_s07_branch_memory_not_truncated(branch_recall_enabled, memory_db, tier_repo):
    """TC-BR-S07: feature memory survives when many main-branch rows exist."""
    for i in range(60):
        memory_db.create_memory_entry(
            repo=tier_repo,
            memory_type="explicit_rule",
            content=f"main rule {i}",
            scope="repo",
            status="active",
            branch="main",
            scope_tier="branch",
        )
    target_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="needle feature memory",
        scope="repo",
        status="active",
        branch="feature/x",
        scope_tier="branch",
    )
    from turingmind_mcp.memory_manager import MemoryManager

    results = MemoryManager(memory_db).get_relevant_memory(
        tier_repo, [], branch="feature/x", head="a" * 40, limit=50
    )
    assert any(r["memory_id"] == target_id for r in results)


@pytest.mark.branch_spec
def test_tc_br_s08_sql_branch_predicate_before_limit(
    branch_recall_enabled, memory_db, tier_repo
):
    """TC-BR-S08: branch-filtered SQL returns feature row (same as S07 integration)."""
    test_tc_br_s07_branch_memory_not_truncated(branch_recall_enabled, memory_db, tier_repo)


@pytest.mark.branch_spec
def test_tc_br_fs04_no_limit_starvation(branch_recall_enabled, memory_db, tier_repo):
    """TC-BR-FS04: old current-branch row not excluded by LIMIT."""
    target_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="old feature memory",
        scope="repo",
        status="active",
        branch="feature/x",
        scope_tier="branch",
    )
    for i in range(100):
        memory_db.create_memory_entry(
            repo=tier_repo,
            memory_type="explicit_rule",
            content=f"new main {i}",
            scope="repo",
            status="active",
            branch="main",
            scope_tier="branch",
        )

    from turingmind_mcp.memory_manager import MemoryManager

    results = MemoryManager(memory_db).get_relevant_memory(
        tier_repo, [], branch="feature/x", head="a" * 40, limit=10
    )
    assert any(r["memory_id"] == target_id for r in results)


# ── SPEC-BR-05 (Phase 4.5) ───────────────────────────────────────────────────


@pytest.mark.branch_spec
def test_tc_br_s09_tombstone_pull_ignores_branch_filter(memory_db, tier_repo):
    """TC-BR-S09: tombstone on feature branch applies when pulled on main."""
    mem_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="rule on main",
        scope="repo",
        status="active",
        branch="main",
        scope_tier="branch",
    )
    stats = memory_db.apply_cloud_memory_rows(
        tier_repo,
        [{
            "memory_id": mem_id,
            "repo": tier_repo,
            "type": "explicit_rule",
            "content": "rule on main",
            "scope": "repo",
            "confidence": 0.9,
            "status": "deprecated",
            "branch": "feature/old",
            "updated_at": "2099-01-01T00:00:00+00:00",
            "deleted_at": "2099-01-01T00:00:00+00:00",
        }],
    )
    assert stats["tombstones_applied"] == 1
    row = memory_db.get_memory_entry(mem_id)
    assert row["status"] == "deprecated"
    assert row.get("deleted_at") is not None


@pytest.mark.branch_spec
def test_tc_br_fs05_no_branch_filtered_tombstone_gap(memory_db, tier_repo):
    """TC-BR-FS05: tombstone rows must merge even when branch differs from sync branch."""
    mem_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="stale feature knowledge",
        scope="branch:feature/y",
        status="active",
        branch="feature/y",
        scope_tier="branch",
    )
    tombstone_row = {
        "memory_id": mem_id,
        "repo": tier_repo,
        "type": "learned_pattern",
        "content": "stale feature knowledge",
        "scope": "branch:feature/y",
        "confidence": 0.7,
        "status": "deprecated",
        "branch": "feature/y",
        "updated_at": "2099-06-01T00:00:00+00:00",
        "deleted_at": "2099-06-01T00:00:00+00:00",
    }
    stats = memory_db.apply_cloud_memory_rows(tier_repo, [tombstone_row])
    assert stats["tombstones_applied"] == 1
    assert memory_db.get_memory_entry(mem_id)["status"] == "deprecated"


# ── SPEC-BR-06 (Phase 4.3) ───────────────────────────────────────────────────


@pytest.mark.branch_spec
def test_tc_br_s10_promotion_skips_pass8_dup(memory_db, tier_repo, api_client):
    """TC-BR-S10: branch_promotion accept → no semantic_duplicate finding."""
    from turingmind_mcp.reconcile import ReconciliationEngine, apply_finding_resolution

    source_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="Branch-only auth middleware pattern for JWT validation",
        scope="src/auth/jwt.py",
        confidence=0.85,
        status="active",
        branch="feature/auth",
        scope_tier="branch",
    )
    finding_id = memory_db.create_finding(
        repo=tier_repo,
        finding_type="branch_promotion",
        severity="medium",
        action="Promote branch memory to repo-wide?",
        dedup_key="bp-test-1",
        memory_id=source_id,
        evidence=[{"type": "branch", "content": "feature/auth"}],
    )
    apply_finding_resolution(memory_db, finding_id, "actioned")

    promoted = [
        m
        for m in memory_db.list_memory_entries(repo=tier_repo, status="active")
        if m.get("promoted_from") == source_id
    ]
    assert len(promoted) == 1
    assert promoted[0]["branch"] is None
    assert promoted[0]["scope_tier"] == "repo"

    engine = ReconciliationEngine(memory_db)
    stats = engine.suggest_duplicate_merges(tier_repo)
    dupes = [
        f
        for f in memory_db.list_findings(repo=tier_repo, status="pending")
        if f["finding_type"] == "semantic_duplicate"
    ]
    assert stats["duplicate_pairs_suggested"] == 0
    assert dupes == []


@pytest.mark.branch_spec
def test_tc_br_fs06_promotion_requires_lineage(memory_db, tier_repo):
    """TC-BR-FS06: promotion creates lineage and deprecates branch copy."""
    from turingmind_mcp.reconcile import apply_finding_resolution

    source_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="Needs lineage",
        scope="branch:feature/x",
        confidence=0.7,
        status="active",
        branch="feature/x",
        scope_tier="branch",
    )
    finding_id = memory_db.create_finding(
        repo=tier_repo,
        finding_type="branch_promotion",
        severity="medium",
        action="Promote?",
        dedup_key="bp-test-2",
        memory_id=source_id,
    )
    apply_finding_resolution(memory_db, finding_id, "actioned")

    source = memory_db.get_memory_entry(source_id)
    assert source["status"] == "deprecated"
    l4 = [
        m
        for m in memory_db.list_memory_entries(repo=tier_repo, status="active")
        if m.get("promoted_from") == source_id
    ]
    assert len(l4) == 1


# ── SPEC-BR-07 (Phase 4.3) ───────────────────────────────────────────────────


@pytest.mark.branch_spec
def test_tc_br_s11_finding_dedup_includes_branch(memory_db, tier_repo):
    """TC-BR-S11: branch_promotion not deduped against promotion_candidate."""
    from turingmind_mcp.reconcile import RECURRENCE_THRESHOLD, ReconciliationEngine

    content = RECURRENCE_CONTENT
    for _ in range(RECURRENCE_THRESHOLD):
        memory_db.create_observation(
            repo=tier_repo,
            event_type="edit_cluster",
            content=content,
            source="cursor-hook",
            branch="feature/a",
        )
    engine = ReconciliationEngine(memory_db)
    engine.mine_recurrence(tier_repo)

    candidate = memory_db.list_memory_entries(
        repo=tier_repo, memory_type="learned_pattern", status="candidate"
    )[0]
    memory_db.create_finding(
        repo=tier_repo,
        finding_type="branch_promotion",
        severity="medium",
        action="Merge promote",
        dedup_key="branch_promotion|feature/a|extra",
        memory_id=candidate["memory_id"],
        evidence=[{"type": "branch", "content": "feature/a"}],
    )

    pending = memory_db.list_findings(repo=tier_repo, status="pending")
    types = {f["finding_type"] for f in pending}
    assert "promotion_candidate" in types
    assert "branch_promotion" in types


@pytest.mark.branch_spec
def test_tc_br_fs07_unknown_finding_rejected(memory_db, tier_repo, api_client):
    """TC-BR-FS07: resolve bogus lifecycle finding does not orphan memories."""
    client, db = api_client
    source_id = db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="Should stay single",
        scope="repo",
        status="active",
    )
    finding_id = db.create_finding(
        repo=tier_repo,
        finding_type="promote_to_repo",
        severity="low",
        action="bogus",
        dedup_key="bogus-1",
        memory_id=source_id,
    )
    response = client.post(
        f"/api/v2/reconcile/findings/{finding_id}/resolve",
        json={"status": "actioned"},
    )
    assert response.status_code == 200
    active = db.list_memory_entries(repo=tier_repo, status="active")
    assert len(active) == 1
    assert active[0]["memory_id"] == source_id


# ── SPEC-BR-09 (Phase 4.4) ───────────────────────────────────────────────────


@pytest.mark.branch_spec
def test_tc_br_s13_ci_pass1_isolation(memory_db, tier_repo):
    """TC-BR-S13: CI obs not clustered with main-branch edit obs."""
    from turingmind_mcp.reconcile import RECURRENCE_THRESHOLD, ReconciliationEngine

    shared = "CI check failed on auth middleware"
    engine = ReconciliationEngine(memory_db)
    for _ in range(RECURRENCE_THRESHOLD):
        memory_db.create_observation(
            repo=tier_repo,
            event_type="ci_check",
            content=shared,
            source="ci-webhook",
            branch="feature/pr",
            head_sha="b" * 40,
            git_dirty=0,
        )
        memory_db.create_observation(
            repo=tier_repo,
            event_type="edit_cluster",
            content=shared,
            source="cursor-hook",
            branch="main",
        )

    stats = engine.mine_recurrence(tier_repo)
    assert stats["patterns_mined"] == 2
    candidates = memory_db.list_memory_entries(
        repo=tier_repo, memory_type="learned_pattern", status="candidate"
    )
    assert len(candidates) == 2
    assert {c["branch"] for c in candidates} == {"feature/pr", "main"}


@pytest.mark.branch_spec
def test_tc_br_fs09_ci_requires_branch(api_client, tier_repo, monkeypatch):
    """TC-BR-FS09: CI ingest without branch → 400 (once route requires branch)."""
    monkeypatch.setenv("TURINGMIND_INGEST_KEY", "test-ingest-key")
    client, db = api_client
    before = len(db.list_observations(repo=tier_repo, status="all"))
    response = client.post(
        "/api/v2/observations/ci",
        headers={"X-TuringMind-Ingest-Key": "test-ingest-key"},
        json={
            "repo": tier_repo,
            "conclusion": "failure",
            "head_sha": "a" * 40,
        },
    )
    assert response.status_code == 400
    after = len(db.list_observations(repo=tier_repo, status="all"))
    assert after == before


# ── SPEC-BR-10 (Phase 4.2) ───────────────────────────────────────────────────


@pytest.mark.branch_spec
def test_tc_br_s14_server_infer_branch(
    branch_recall_enabled, monkeypatch, api_client, tier_repo, git_sandbox, memory_db
):
    """TC-BR-S14: infer branch from TURINGMIND_WORKSPACE_DIR when omitted."""
    from turingmind_mcp.git_context import collect_git_context

    ctx = collect_git_context(git_sandbox)
    assert ctx is not None
    memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="inferred branch rule",
        scope="repo",
        status="active",
        branch=ctx.branch,
        scope_tier="branch",
    )
    monkeypatch.setenv("TURINGMIND_WORKSPACE_DIR", str(git_sandbox))
    client, _db = api_client
    response = client.get("/api/v2/memory/relevant", params={"repo": tier_repo})
    assert response.status_code == 200
    assert any(e["content"] == "inferred branch rule" for e in response.json()["entries"])


@pytest.mark.branch_spec
def test_tc_br_fs10_infer_fallback_safe(branch_recall_enabled, monkeypatch, api_client, tier_repo, memory_db):
    """TC-BR-FS10: no crash when branch omitted and workspace unset."""
    monkeypatch.delenv("TURINGMIND_WORKSPACE_DIR", raising=False)
    memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="repo wide fallback",
        scope="repo",
        status="active",
        branch=None,
        scope_tier="repo",
    )
    client, _db = api_client
    response = client.get("/api/v2/memory/relevant", params={"repo": tier_repo})
    assert response.status_code == 200
    assert any(e["content"] == "repo wide fallback" for e in response.json()["entries"])


# ── SPEC-BR-11 (Phase 4.3 + 4.5) ─────────────────────────────────────────────


@pytest.mark.branch_spec
def test_tc_br_s15_lifecycle_dedup_separation(memory_db, tier_repo):
    """TC-BR-S15: archive and promotion findings use distinct dedup keys."""
    from datetime import datetime, timedelta, timezone

    from turingmind_mcp.reconcile import ReconciliationEngine

    mem_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="stale branch memory",
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

    memory_db.create_finding(
        repo=tier_repo,
        finding_type="branch_promotion",
        severity="medium",
        action="Promote stale branch memory",
        dedup_key="branch_promotion|feature/stale",
        memory_id=mem_id,
    )

    engine = ReconciliationEngine(memory_db)
    stats = engine.branch_lifecycle(tier_repo)
    assert stats["branch_archives_suggested"] == 1

    pending = memory_db.list_findings(repo=tier_repo, status="pending")
    types = [f["finding_type"] for f in pending]
    assert types.count("branch_promotion") >= 1
    assert types.count("archive_branch_memories") >= 1


@pytest.mark.branch_spec
def test_tc_br_fs11_archive_idempotent(memory_db, tier_repo, api_client):
    """TC-BR-FS11: second archive resolve is no-op for tombstones."""
    from turingmind_mcp.reconcile import apply_finding_resolution

    mem_id = memory_db.create_memory_entry(
        repo=tier_repo,
        memory_type="learned_pattern",
        content="to archive",
        scope="src/x.py",
        status="active",
        branch="feature/done",
        scope_tier="branch",
    )
    finding_id = memory_db.create_finding(
        repo=tier_repo,
        finding_type="archive_branch_memories",
        severity="low",
        action="Archive branch",
        dedup_key="archive|feature/done",
        evidence=[{"type": "branch", "content": "feature/done"}],
        memory_id=mem_id,
    )
    apply_finding_resolution(memory_db, finding_id, "actioned")
    first = memory_db.get_memory_entry(mem_id)
    assert first["status"] == "deprecated"
    deleted_at = first["deleted_at"]

    memory_db.conn.execute(
        "UPDATE reconcile_findings SET status = 'pending', resolved_at = NULL WHERE finding_id = ?",
        (finding_id,),
    )
    memory_db.conn.commit()
    apply_finding_resolution(memory_db, finding_id, "actioned")
    second = memory_db.get_memory_entry(mem_id)
    assert second["status"] == "deprecated"
    assert second["deleted_at"] == deleted_at


@pytest.mark.branch_spec
def test_git_payload_validation_rejects_bad_head():
    """Supporting validation for TC-BR-F01 / FS09."""
    with pytest.raises(ValueError):
        git_context_from_payload({"branch": "main", "head": "not-a-sha", "dirty": False})
