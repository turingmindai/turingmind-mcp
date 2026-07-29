"""Lightweight memory health snapshot from ~/.turingmind/memory.db."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .db_paths import resolve_primary_db_path


@dataclass
class RepoMemoryStatus:
    repo: str
    pending_observations: int = 0
    findings_pending: int = 0
    explicit_rules: int = 0
    learned_patterns: int = 0
    session_context_active: int = 0
    session_context_pct: float = 0.0
    last_reconcile_at: Optional[str] = None
    last_reconcile_remaining: Optional[int] = None


@dataclass
class MemoryStatusReport:
    db_path: str
    repos: List[RepoMemoryStatus] = field(default_factory=list)
    totals: Dict[str, int] = field(default_factory=dict)

    def summary_table(self) -> str:
        lines = [
            "| Repo | Pending obs | Queue | Rules | Session ctx | Last reconcile |",
            "|------|-------------|-------|-------|-------------|----------------|",
        ]
        for row in self.repos:
            last = row.last_reconcile_at or "—"
            if row.last_reconcile_remaining is not None:
                last = f"{last} ({row.last_reconcile_remaining} pending)"
            lines.append(
                f"| {row.repo} | {row.pending_observations} | {row.findings_pending} "
                f"| {row.explicit_rules} | {row.session_context_active} "
                f"({row.session_context_pct:.0f}%) | {last} |"
            )
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "db_path": self.db_path,
            "repos": [asdict(r) for r in self.repos],
            "totals": self.totals,
        }


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or resolve_primary_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def collect_memory_status(
    *,
    db_path: Optional[str] = None,
    repo: Optional[str] = None,
) -> MemoryStatusReport:
    """Collect pending obs, queue, rules, and last reconcile stats per repo."""
    conn = _connect(db_path)
    try:
        if repo:
            repos = [repo]
        else:
            rows = conn.execute(
                """
                SELECT DISTINCT repo FROM (
                    SELECT repo FROM observations
                    UNION SELECT repo FROM memory_entries
                    UNION SELECT repo FROM reconcile_findings
                ) ORDER BY repo
                """
            ).fetchall()
            repos = [r[0] for r in rows]

        report = MemoryStatusReport(db_path=str(db_path or resolve_primary_db_path()))
        total_pending = 0
        total_findings = 0

        for repo_slug in repos:
            pending = conn.execute(
                "SELECT COUNT(*) FROM observations WHERE repo = ? AND status = 'pending'",
                (repo_slug,),
            ).fetchone()[0]
            findings = conn.execute(
                "SELECT COUNT(*) FROM reconcile_findings WHERE repo = ? AND status = 'pending'",
                (repo_slug,),
            ).fetchone()[0]
            rules = conn.execute(
                """
                SELECT COUNT(*) FROM memory_entries
                WHERE repo = ? AND type = 'explicit_rule' AND status = 'active'
                """,
                (repo_slug,),
            ).fetchone()[0]
            session_active = conn.execute(
                """
                SELECT COUNT(*) FROM memory_entries
                WHERE repo = ? AND type = 'session_context' AND status = 'active'
                """,
                (repo_slug,),
            ).fetchone()[0]
            active_total = conn.execute(
                """
                SELECT COUNT(*) FROM memory_entries
                WHERE repo = ? AND status = 'active'
                """,
                (repo_slug,),
            ).fetchone()[0]
            session_pct = (100.0 * session_active / active_total) if active_total else 0.0

            last_run = conn.execute(
                """
                SELECT started_at, stats FROM reconcile_runs
                WHERE repo = ? ORDER BY started_at DESC LIMIT 1
                """,
                (repo_slug,),
            ).fetchone()
            last_at = None
            last_remaining = None
            if last_run:
                last_at = last_run[0]
                try:
                    stats = json.loads(last_run[1] or "{}")
                    last_remaining = stats.get("observations_remaining")
                except json.JSONDecodeError:
                    pass

            report.repos.append(
                RepoMemoryStatus(
                    repo=repo_slug,
                    pending_observations=pending,
                    findings_pending=findings,
                    explicit_rules=rules,
                    session_context_active=session_active,
                    session_context_pct=session_pct,
                    last_reconcile_at=last_at,
                    last_reconcile_remaining=last_remaining,
                )
            )
            total_pending += pending
            total_findings += findings

        report.totals = {
            "pending_observations": total_pending,
            "findings_pending": total_findings,
            "repos_tracked": len(report.repos),
        }
        return report
    finally:
        conn.close()


def format_status_report(report: MemoryStatusReport, *, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(report.to_dict(), indent=2)
    lines = [
        f"Memory status — {report.db_path}",
        "",
        report.summary_table(),
        "",
        f"Totals: {report.totals.get('pending_observations', 0)} pending observations, "
        f"{report.totals.get('findings_pending', 0)} queue findings "
        f"across {report.totals.get('repos_tracked', 0)} repos.",
    ]
    return "\n".join(lines)
