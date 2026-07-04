"""
Reconciliation engine — deterministic passes over the observation/memory store.

The engine is a judge, not an author: it corrects the memory graph (promote,
decay, flag) and proposes actions through reconcile_findings, but it never
activates a rule on its own and never touches code. No LLM sits in any pass —
the same inputs always produce the same verdicts, so every run is replayable.

Passes:
    1. Recurrence miner    — recurring pending observations → learned_pattern
                             candidate (status="candidate", queue-approved)
    2. Confidence decay    — unreinforced learned_patterns lose confidence
                             over time instead of staying trusted forever
    3. Conflict aggregator — unresolved memory conflicts become queue findings
    4. Missing-node detector — OBSERVED (ungoverned) L1 nodes become one
                             governance finding

Triggered via POST /api/v2/reconcile, `turingmind reconcile`, or the
background loop in the API server.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .database import MemoryDatabase

logger = logging.getLogger("turingmind-mcp")

# Tunables — deliberately conservative; loosen only with evidence.
RECURRENCE_THRESHOLD = 3        # occurrences before a pattern candidate is mined
SIMILARITY_THRESHOLD = 0.5      # Jaccard token overlap for "same" observation
DECAY_HALF_LIFE_DAYS = 30       # unreinforced pattern loses ~50% in this window
DECAY_FLOOR = 0.1               # confidence never decays below this
STALE_THRESHOLD = 0.3           # below this, a stale-memory finding is emitted
CANDIDATE_CONFIDENCE = 0.6      # starting confidence for mined candidates


def _tokens(text: str) -> frozenset:
    return frozenset(w for w in text.lower().split() if len(w) > 2)


def _similarity(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _dedup_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


class ReconciliationEngine:
    """Runs all deterministic passes for one repo and records stats."""

    def __init__(self, db: MemoryDatabase):
        self.db = db

    def run(self, repo: str) -> Dict[str, Any]:
        stats: Dict[str, Any] = {
            "repo": repo,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        stats.update(self.mine_recurrence(repo))
        stats.update(self.decay_confidence(repo))
        stats.update(self.aggregate_conflicts(repo))
        stats.update(self.detect_missing_nodes(repo))
        run_id = self.db.record_reconcile_run(repo, stats)
        stats["run_id"] = run_id
        logger.info(f"Reconciliation run {run_id} for {repo}: {stats}")
        return stats

    # ── Pass 1: recurrence miner ─────────────────────────────────────────────
    def mine_recurrence(self, repo: str) -> Dict[str, int]:
        """Cluster pending observations by token overlap. Groups that recur
        RECURRENCE_THRESHOLD+ times become a learned_pattern *candidate*
        (never active directly) plus a promotion finding on the queue."""
        pending = self.db.list_observations(repo=repo, status="pending", limit=500)
        # Oldest first so cluster exemplars are stable across runs
        pending = sorted(pending, key=lambda o: o.get("created_at") or "")

        clusters: List[List[dict]] = []
        for obs in pending:
            toks = _tokens(obs["content"])
            for cluster in clusters:
                if _similarity(toks, _tokens(cluster[0]["content"])) >= SIMILARITY_THRESHOLD:
                    cluster.append(obs)
                    break
            else:
                clusters.append([obs])

        candidates = 0
        accepted = 0
        for cluster in clusters:
            if len(cluster) < RECURRENCE_THRESHOLD:
                continue
            exemplar = cluster[0]
            content = (
                f"Recurring activity ({len(cluster)}x): {exemplar['content']}"
            )
            memory_id = self.db.create_memory_entry(
                repo=repo,
                memory_type="learned_pattern",
                content=content,
                scope="repo",
                confidence=CANDIDATE_CONFIDENCE,
                status="candidate",
                created_by="reconcile:recurrence_miner",
                node_id=exemplar.get("node_id"),
            )
            for obs in cluster:
                self.db.resolve_observation(obs["observation_id"], "accepted", memory_id)
                accepted += 1
            self.db.create_finding(
                repo=repo,
                finding_type="promotion_candidate",
                severity="medium",
                action=(
                    f"Pattern recurred {len(cluster)}x — promote to active learned_pattern? "
                    f"Candidate: {content[:200]}"
                ),
                dedup_key=_dedup_key("promotion", repo, exemplar["content"][:120]),
                evidence=[
                    {"type": "observation", "content": o["observation_id"]}
                    for o in cluster[:10]
                ],
                memory_id=memory_id,
            )
            candidates += 1

        return {
            "observations_pending": len(pending),
            "patterns_mined": candidates,
            "observations_accepted": accepted,
        }

    # ── Pass 2: confidence decay ─────────────────────────────────────────────
    def decay_confidence(self, repo: str) -> Dict[str, int]:
        """Exponential decay on learned_patterns that haven't been reinforced
        (updated) recently. Explicit rules never decay — they were human
        decisions and only humans retire them."""
        cursor = self.db.conn.cursor()
        rows = cursor.execute(
            """
            SELECT memory_id, confidence, updated_at FROM memory_entries
            WHERE repo = ? AND type = 'learned_pattern' AND status = 'active'
            """,
            (repo,),
        ).fetchall()

        now = datetime.now(timezone.utc)
        decayed = 0
        stale = 0
        for row in rows:
            try:
                updated = datetime.fromisoformat(str(row["updated_at"]).replace(" ", "T"))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            age_days = (now - updated).total_seconds() / 86400
            if age_days <= DECAY_HALF_LIFE_DAYS:
                continue
            new_confidence = max(
                DECAY_FLOOR,
                row["confidence"] * (0.5 ** (age_days / DECAY_HALF_LIFE_DAYS)),
            )
            if row["confidence"] - new_confidence < 0.01:
                continue
            # Direct SQL keeps updated_at untouched — update_memory_entry would
            # bump it and reset the decay clock every run.
            cursor.execute(
                "UPDATE memory_entries SET confidence = ? WHERE memory_id = ?",
                (new_confidence, row["memory_id"]),
            )
            decayed += 1
            if new_confidence <= STALE_THRESHOLD:
                self.db.create_finding(
                    repo=repo,
                    finding_type="stale_memory",
                    severity="low",
                    action=(
                        f"learned_pattern {row['memory_id']} decayed to "
                        f"{new_confidence:.2f} after {age_days:.0f}d without "
                        f"reinforcement — confirm or deprecate it"
                    ),
                    dedup_key=_dedup_key("stale", repo, row["memory_id"]),
                    memory_id=row["memory_id"],
                )
                stale += 1
        self.db.conn.commit()
        return {"memories_decayed": decayed, "stale_flagged": stale}

    # ── Pass 3: conflict aggregator ──────────────────────────────────────────
    def aggregate_conflicts(self, repo: str) -> Dict[str, int]:
        """Unresolved conflict flags exist in the database but nobody sees
        them. Surface each as a decision-queue finding."""
        conflicts = self.db.get_conflicts(repo, unresolved_only=True)
        surfaced = 0
        for c in conflicts:
            created = self.db.create_finding(
                repo=repo,
                finding_type="memory_conflict",
                severity=c.get("severity", "medium"),
                action=(
                    f"Conflicting memories {c['memory_id_1'][:8]} vs "
                    f"{c['memory_id_2'][:8]}: {c.get('description') or c['conflict_type']} "
                    f"— resolve via turingmind_resolve_conflict ({c['conflict_id']})"
                ),
                dedup_key=_dedup_key("conflict", repo, c["conflict_id"]),
                evidence=[{"type": "conflict", "content": c["conflict_id"]}],
                memory_id=c["memory_id_1"],
            )
            if created:
                surfaced += 1
        return {"conflicts_open": len(conflicts), "conflicts_surfaced": surfaced}

    # ── Pass 4: missing-node detector ────────────────────────────────────────
    def detect_missing_nodes(self, repo: str) -> Dict[str, int]:
        """OBSERVED-tier nodes are files the sync saw but nobody governs.
        Roll them into one finding instead of one nag per file."""
        try:
            from .v2_engine.handlers import _all_nodes_for_repo
            nodes = _all_nodes_for_repo(repo)
        except Exception as e:
            logger.warning(f"Missing-node pass skipped (v2 store unavailable): {e}")
            return {"ungoverned_nodes": 0}

        ungoverned = [
            n for n in nodes
            if str(getattr(getattr(n, "governance_tier", None), "value",
                           getattr(n, "governance_tier", ""))).lower() == "observed"
        ]
        if ungoverned:
            titles = ", ".join(n.title for n in ungoverned[:8])
            self.db.create_finding(
                repo=repo,
                finding_type="ungoverned_files",
                severity="medium",
                action=(
                    f"{len(ungoverned)} OBSERVED node(s) have no governance: {titles}"
                    f"{'…' if len(ungoverned) > 8 else ''} — promote the ones that "
                    f"matter, dismiss the rest"
                ),
                # Count in the key: a new finding appears when the set grows
                dedup_key=_dedup_key("ungoverned", repo, str(len(ungoverned))),
                evidence=[{"type": "node", "content": n.id} for n in ungoverned[:20]],
            )
        return {"ungoverned_nodes": len(ungoverned)}


def reconcile_repo(db: MemoryDatabase, repo: str) -> Dict[str, Any]:
    """Convenience entry point used by the API endpoint and background loop."""
    return ReconciliationEngine(db).run(repo)


def repos_with_activity(db: MemoryDatabase) -> List[str]:
    """Repos that have observations or memories — the background loop's scope."""
    cursor = db.conn.cursor()
    rows = cursor.execute(
        "SELECT DISTINCT repo FROM observations UNION SELECT DISTINCT repo FROM memory_entries"
    ).fetchall()
    return [r[0] for r in rows]
