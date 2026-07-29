#!/usr/bin/env python3
"""One-shot ops: seed plugin rules, dismiss smoke findings, drain pending backlog."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.reconcile import reconcile_repo

COMMON_RULES = [
    "All memory is stored in ~/.turingmind/memory.db — always filter by repo slug (owner/repo) when querying or saving.",
    "Never store API keys, tokens, passwords, or PII in memory content.",
    "Recall priority: explicit_rule > learned_pattern > repo_fact > session_context. Honor explicit_rule over session blobs.",
    "Reconcile per repo via POST /api/v2/reconcile {\"repo\":\"owner/repo\"} — not workspace-level.",
    "session_context entries expire in ~20h — only explicit_rule and learned_pattern are durable recall targets.",
    "Agents must call turingmind_list_memory with the repo slug matching files being touched — not workspace root.",
    "Do not promote refactor_burst, non_code, or >5-file edit clusters — they are noise, not patterns.",
]

PLUGIN_RULES = {
    "turingmindai/turingmind-antigravity-plugin": [
        "Git hooks in hooks/ capture pre-push and post-commit git context — not per-file edit clusters.",
        "Multi-repo workspaces: each sub-repo has its own .turingmind/hook.log when present.",
    ],
    "turingmindai/turingmind-codex-plugin": [
        "Hooks in hooks/scripts/ run on SessionStart/End and Bash Pre/PostToolUse — attribute observations to the repo being edited.",
        "Multi-repo workspaces: each sub-repo has its own .turingmind/ config when present.",
    ],
    "turingmindai/turingmind-claude-plugin": [
        "Git hooks in hooks/ capture pre-push and post-commit git context — not per-file edit clusters.",
        "Multi-repo workspaces: each sub-repo has its own .turingmind/hook.log when present.",
    ],
}


def seed_rules(db: MemoryDatabase) -> int:
    created = 0
    for repo, lead_rules in PLUGIN_RULES.items():
        existing = db.conn.execute(
            """
            SELECT COUNT(*) FROM memory_entries
            WHERE repo = ? AND type = 'explicit_rule' AND status = 'active'
            """,
            (repo,),
        ).fetchone()[0]
        if existing >= 9:
            print(f"  skip {repo}: already has {existing} rules")
            continue
        for content in lead_rules + COMMON_RULES:
            dup = db.conn.execute(
                """
                SELECT 1 FROM memory_entries
                WHERE repo = ? AND type = 'explicit_rule' AND content = ? AND status = 'active'
                """,
                (repo, content),
            ).fetchone()
            if dup:
                continue
            db.create_memory_entry(
                repo=repo,
                memory_type="explicit_rule",
                content=content,
                scope="repo",
                confidence=1.0,
                created_by="seed:plugin-rollout",
            )
            created += 1
        print(f"  seeded {repo}")
    return created


def dismiss_smoke_findings(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE reconcile_findings
        SET status = 'dismissed', resolved_at = CURRENT_TIMESTAMP
        WHERE status = 'pending'
            AND (repo LIKE 'smoke/%' OR repo = 'test-org/tier-sandbox')
        """
    )
    conn.commit()
    count = cur.rowcount
    conn.close()
    return count


def drain_backlog(db: MemoryDatabase, repo: str, *, hours: int = 1, rounds: int = 8) -> None:
    os.environ["TURINGMIND_UNCLUSTERED_PENDING_HOURS"] = str(hours)
    before = db.count_observations(repo, "pending")
    print(f"  draining {repo}: {before} pending (unclustered>{hours}h)")
    for i in range(rounds):
        pending = db.count_observations(repo, "pending")
        if pending == 0:
            break
        stats = reconcile_repo(db, repo)
        rejected = stats.get("observations_rejected_unclustered", 0)
        stale = stats.get("observations_expired_stale", 0)
        remaining = stats.get("observations_remaining", pending)
        print(f"    round {i + 1}: -{rejected + stale} -> {remaining} remaining")
        if rejected == 0 and stale == 0:
            break
    after = db.count_observations(repo, "pending")
    print(f"  done: {before} -> {after}")


def main() -> int:
    db = MemoryDatabase()
    print("=== Seed plugin rules ===")
    created = seed_rules(db)
    print(f"  {created} new rules created")

    print("=== Dismiss smoke findings ===")
    dismissed = dismiss_smoke_findings(str(db.db_path))
    print(f"  dismissed {dismissed}")

    print("=== Drain mcp backlog ===")
    drain_backlog(db, "turingmindai/turingmind-mcp")

    print("=== Drain cursor-plugin backlog ===")
    drain_backlog(db, "turingmindai/turingmind-cursor-plugin", rounds=2)

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
