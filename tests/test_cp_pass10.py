from __future__ import annotations

import pytest
from turingmind_mcp.reconcile import ReconciliationEngine, apply_finding_resolution

def test_cp_pass10_mines_marker_phrases(memory_db, tier_repo):
    """TC-CP-10: Verify that marker phrases produce promotion_candidate findings."""
    db = memory_db

    # Seed an observation with a rule-steering marker phrase
    db.create_observation(
        repo=tier_repo,
        event_type="chat_exchange",
        content="user: You should always use python3.11 for builds.\nassistant: OK, I will.",
        confidence=0.3,
    )

    engine = ReconciliationEngine(db)
    stats = engine.run(tier_repo)

    assert stats.get("chat_rules_suggested", 0) == 1

    findings = db.list_findings(tier_repo, status="pending")
    assert len(findings) == 1
    assert findings[0]["finding_type"] == "promotion_candidate"
    assert "You should always use python3.11 for builds." in findings[0]["action"]


def test_cp_pass10_ignores_chat_noise(memory_db, tier_repo):
    """TC-CP-11: Verify that chat noise produces 0 findings."""
    db = memory_db

    # Seed a chat observation with no rule-steering words
    db.create_observation(
        repo=tier_repo,
        event_type="chat_exchange",
        content="user: Hello, how do I print hello world?\nassistant: print('hello world')",
        confidence=0.3,
    )

    engine = ReconciliationEngine(db)
    stats = engine.run(tier_repo)

    assert stats.get("chat_rules_suggested", 0) == 0

    findings = db.list_findings(tier_repo, status="pending")
    assert len(findings) == 0


def test_cp_pass10_e2e_promotion(memory_db, tier_repo):
    """TC-CP-14: E2E check verifying chat observation is promoted to rule and resolved."""
    db = memory_db

    db.create_observation(
        repo=tier_repo,
        event_type="chat_exchange",
        content="user: make sure to never import direct dependencies without verification.",
        confidence=0.3,
    )

    engine = ReconciliationEngine(db)
    engine.run(tier_repo)

    findings = db.list_findings(tier_repo, status="pending")
    assert len(findings) == 1
    finding_id = findings[0]["finding_id"]

    # Resolve candidate
    ok = apply_finding_resolution(db, finding_id, "actioned")
    assert ok is True

    resolved_findings = db.list_findings(tier_repo, status="actioned")
    assert len(resolved_findings) == 1
    assert resolved_findings[0]["finding_id"] == finding_id
