from __future__ import annotations

import pytest
from turingmind_mcp.reconcile import ReconciliationEngine

def test_cp_queue_triage_auto_dismiss(memory_db, tier_repo):
    """TC-CP-13: Verify auto-dismiss logic when findings queue exceeds 50."""
    db = memory_db

    # Seed 60 pending findings
    for i in range(60):
        db.create_finding(
            repo=tier_repo,
            finding_type="promotion_candidate",
            severity="medium",
            action=f"Mined rule {i}",
            dedup_key=f"dedup-{i}",
        )

    # Verify we have 60 pending findings initially
    initial_findings = db.list_findings(tier_repo, status="pending", limit=100)
    assert len(initial_findings) == 60

    # Run ReconciliationEngine
    engine = ReconciliationEngine(db)
    engine.run(tier_repo)

    # After run, the flood protection should have resolved/dismissed findings to bring queue <= 50
    post_findings = db.list_findings(tier_repo, status="pending", limit=100)
    assert len(post_findings) <= 50

    # Check that dismissed findings exist
    dismissed = db.list_findings(tier_repo, status="dismissed", limit=100)
    assert len(dismissed) > 0
