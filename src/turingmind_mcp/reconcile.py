"""
Reconciliation engine — deterministic passes over the observation/memory store.

The engine is a judge, not an author: it corrects the memory graph (promote,
decay, flag) and proposes actions through reconcile_findings, but it never
activates a rule on its own and never touches code. No LLM sits in any pass —
the same inputs always produce the same verdicts, so every run is replayable.

Passes:
    1. Recurrence miner         — recurring pending observations → learned_pattern
                                  candidate (Jaccard + embedding similarity per event_type)
    2. Revert scope penalty     — git_revert observations → confidence penalty on
                                  scope-matched active memories
    3. Invalidation decay       — missing scope files + edit/git churn → penalty
    4. Verification reinforcement — verification_success obs → small confidence
                                  boost on node-linked failure patterns
    5. Confidence decay (age)   — unreinforced learned_patterns lose confidence
                                  over time instead of staying trusted forever
    6. Conflict aggregator      — unresolved memory conflicts → queue findings
    7. Missing-node detector    — OBSERVED (ungoverned) L1 nodes → governance finding
    8. Duplicate merge suggester — embedding similarity → semantic_duplicate findings
                                  (Azure text-embedding-3-small when configured,
                                   else hash-bow fallback)

LLM distillation belongs at the adjudication gate (agent/human reviews queue
findings) — not in this module. See Tier D optional adjudication workflow.

Triggered via POST /api/v2/reconcile, `turingmind reconcile`, or the
background loop in the API server.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .database import MemoryDatabase
from .git_churn import collect_git_churn, count_scope_git_hits, resolve_git_workspace
from .memory_embeddings import (
    duplicate_threshold_for,
    index_memory_embeddings,
)
from .memory_vec_index import find_embedding_duplicate_pairs, sqlite_vec_enabled
from .observation_capture import EVENT_GIT_REVERT, EVENT_VERIFICATION_SUCCESS
from .observation_clustering import (
    build_observation_vectors,
    observations_semantically_similar,
)

logger = logging.getLogger("turingmind-mcp")

# Tunables — deliberately conservative; loosen only with evidence.
RECURRENCE_THRESHOLD = 3        # occurrences before a pattern candidate is mined
SIMILARITY_THRESHOLD = 0.5      # Jaccard token overlap for "same" observation
DECAY_HALF_LIFE_DAYS = 30       # unreinforced pattern loses ~50% in this window
DECAY_FLOOR = 0.1               # confidence never decays below this
STALE_THRESHOLD = 0.3           # below this, a stale-memory finding is emitted
CANDIDATE_CONFIDENCE = 0.6      # starting confidence for mined candidates
REVERT_CONFIDENCE_FACTOR = 0.75 # multiply confidence when a revert touches scope
INVALIDATION_CONFIDENCE_FACTOR = 0.85  # scope file missing from workspace
SCOPE_CHURN_THRESHOLD = 5       # edit_cluster mentions before churn penalty
SCOPE_CHURN_DECAY_FACTOR = 0.9
GIT_CHURN_EQUIVALENT_HITS = 5   # git-touched scope counts like N editor signals
SUCCESS_REINFORCEMENT_DELTA = 0.05
SUCCESS_REINFORCEMENT_CAP = 0.95


def _tokens(text: str) -> frozenset:
    return frozenset(w for w in text.lower().split() if len(w) > 2)


def _similarity(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _dedup_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _scope_matches_file(scope: str, file_path: str) -> bool:
    """True when a memory scope overlaps a reverted/churn file path."""
    if not scope or scope == "repo":
        return False
    scope_n = _normalize_path(scope)
    file_n = _normalize_path(file_path)
    if scope_n == file_n:
        return True
    if file_n.endswith("/" + scope_n) or scope_n.endswith("/" + file_n):
        return True
    return scope_n in file_n or file_n in scope_n


def _extract_revert_files(obs: dict) -> List[str]:
    """Pull file paths from git_revert observation evidence and content."""
    files: List[str] = []
    for ev in obs.get("evidence") or []:
        if ev.get("type") == "files":
            raw = ev.get("content") or ""
            files.extend(p.strip() for p in raw.split(",") if p.strip())
    content = obs.get("content") or ""
    marker = "files:"
    if marker in content:
        tail = content.split(marker, 1)[1].strip()
        if " — " in tail:
            tail = tail.split(" — ", 1)[0]
        files.extend(p.strip() for p in tail.split(",") if p.strip())
    # Stable dedupe preserving order
    seen: set[str] = set()
    out: List[str] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _workspace_root() -> Path:
    """Workspace for scope file existence checks — env wins, then cwd."""
    env = os.environ.get("TURINGMIND_WORKSPACE_DIR")
    if env:
        return Path(env)
    return Path.cwd()


class ReconciliationEngine:
    """Runs all deterministic passes for one repo and records stats."""

    def __init__(self, db: MemoryDatabase):
        self.db = db

    def _lower_confidence(
        self, memory_id: str, old_confidence: float, factor: float
    ) -> Optional[float]:
        """Apply multiplicative penalty without touching updated_at."""
        new_confidence = max(DECAY_FLOOR, old_confidence * factor)
        if old_confidence - new_confidence < 0.01:
            return None
        cursor = self.db.conn.cursor()
        cursor.execute(
            "UPDATE memory_entries SET confidence = ? WHERE memory_id = ?",
            (new_confidence, memory_id),
        )
        self.db.conn.commit()
        return new_confidence

    def _apply_confidence_penalty(
        self,
        repo: str,
        memory_id: str,
        old_confidence: float,
        factor: float,
        *,
        finding_type: str,
        severity: str,
        action: str,
        dedup_key: str,
        evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Lower confidence and surface a queue finding (deduped by dedup_key)."""
        finding_id = self.db.create_finding(
            repo=repo,
            finding_type=finding_type,
            severity=severity,
            action=action,
            dedup_key=dedup_key,
            evidence=evidence,
            memory_id=memory_id,
        )
        if not finding_id:
            return False
        return self._lower_confidence(memory_id, old_confidence, factor) is not None

    def _active_scoped_memories(self, repo: str) -> List[dict]:
        cursor = self.db.conn.cursor()
        rows = cursor.execute(
            """
            SELECT memory_id, scope, confidence, type, node_id, content
            FROM memory_entries
            WHERE repo = ? AND status = 'active'
              AND type IN ('learned_pattern', 'session_context')
              AND scope IS NOT NULL AND scope != 'repo'
            """,
            (repo,),
        ).fetchall()
        return [dict(r) for r in rows]

    def run(self, repo: str) -> Dict[str, Any]:
        stats: Dict[str, Any] = {
            "repo": repo,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        stats.update(self.mine_recurrence(repo))
        stats.update(self.apply_revert_penalties(repo))
        stats.update(self.apply_invalidation_decay(repo))
        stats.update(self.reinforce_verification_success(repo))
        stats.update(self.decay_confidence(repo))
        stats.update(self.aggregate_conflicts(repo))
        stats.update(self.detect_missing_nodes(repo))
        stats.update(self.suggest_duplicate_merges(repo))
        run_id = self.db.record_reconcile_run(repo, stats)
        stats["run_id"] = run_id
        logger.info(f"Reconciliation run {run_id} for {repo}: {stats}")
        return stats

    def _observations_match(
        self,
        obs: dict,
        exemplar: dict,
        vectors: Dict[str, List[float]],
        embed_method: str,
    ) -> bool:
        if vectors and observations_semantically_similar(
            obs, exemplar, vectors, embed_method=embed_method
        ):
            return True
        return _similarity(_tokens(obs["content"]), _tokens(exemplar["content"])) >= SIMILARITY_THRESHOLD

    # ── Pass 1: recurrence miner ─────────────────────────────────────────────
    def mine_recurrence(self, repo: str) -> Dict[str, int]:
        """Cluster pending observations by lexical Jaccard or embedding similarity.

        Groups that recur RECURRENCE_THRESHOLD+ times become a learned_pattern
        *candidate* (never active directly) plus a promotion finding on the queue.
        """
        pending = self.db.list_observations(repo=repo, status="pending", limit=500)
        pending = sorted(pending, key=lambda o: o.get("created_at") or "")
        embed_method, vec_map = build_observation_vectors(pending)

        clusters_by_type: Dict[str, List[List[dict]]] = {}
        for obs in pending:
            event_type = obs.get("event_type") or "unknown"
            type_clusters = clusters_by_type.setdefault(event_type, [])
            for cluster in type_clusters:
                if self._observations_match(obs, cluster[0], vec_map, embed_method):
                    cluster.append(obs)
                    break
            else:
                type_clusters.append([obs])

        clusters: List[List[dict]] = [
            cluster
            for type_clusters in clusters_by_type.values()
            for cluster in type_clusters
        ]

        candidates = 0
        accepted = 0
        for cluster in clusters:
            if len(cluster) < RECURRENCE_THRESHOLD:
                continue
            exemplar = cluster[0]
            content = (
                f"Recurring {exemplar.get('event_type', 'activity')} ({len(cluster)}x): "
                f"{exemplar['content']}"
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

    # ── Pass 2: revert scope penalty ─────────────────────────────────────────
    def apply_revert_penalties(self, repo: str) -> Dict[str, int]:
        """git_revert observations penalize active memories whose scope overlaps
        reverted files. Observations are consumed; penalties surface on the queue."""
        pending = self.db.list_observations(
            repo=repo, status="pending", event_type=EVENT_GIT_REVERT, limit=200
        )
        if not pending:
            return {"revert_observations": 0, "revert_memories_penalized": 0}

        scoped = self._active_scoped_memories(repo)
        penalized = 0
        processed = 0

        for obs in pending:
            files = _extract_revert_files(obs)
            if not files:
                self.db.resolve_observation(obs["observation_id"], "rejected")
                continue

            obs_penalized = 0
            for mem in scoped:
                if not any(_scope_matches_file(mem["scope"], f) for f in files):
                    continue
                if self._apply_confidence_penalty(
                    repo,
                    mem["memory_id"],
                    mem["confidence"],
                    REVERT_CONFIDENCE_FACTOR,
                    finding_type="revert_penalty",
                    severity="medium",
                    action=(
                        f"Git revert touched scope '{mem['scope']}' — "
                        f"learned_pattern confidence reduced. "
                        f"Review or deprecate if the pattern was wrong."
                    ),
                    dedup_key=_dedup_key(
                        "revert", repo, mem["memory_id"], obs["observation_id"][:8]
                    ),
                    evidence=[
                        {"type": "revert_observation", "content": obs["observation_id"]},
                        {"type": "files", "content": ", ".join(files[:10])},
                    ],
                ):
                    penalized += 1
                    obs_penalized += 1
                    mem["confidence"] = max(
                        DECAY_FLOOR, mem["confidence"] * REVERT_CONFIDENCE_FACTOR
                    )

            self.db.resolve_observation(obs["observation_id"], "accepted")
            processed += 1

        return {
            "revert_observations": processed,
            "revert_memories_penalized": penalized,
        }

    # ── Pass 3: invalidation decay ───────────────────────────────────────────
    def _ingest_git_churn_observations(
        self,
        repo: str,
        snapshot: Optional[GitChurnSnapshot],
    ) -> int:
        """Record draft git_churn observations for paths touched outside the editor."""
        if not snapshot or not snapshot.all_touched:
            return 0

        created = 0
        for path in sorted(snapshot.all_touched)[:100]:
            content = (
                f"git churn: path '{path}' modified or deleted since last reconcile "
                f"(HEAD {snapshot.head[:8]})"
            )
            self.db.create_observation(
                repo=repo,
                event_type="git_churn",
                content=content,
                source="git-churn",
                confidence=0.35,
                evidence=[{"type": "path", "content": path}],
            )
            created += 1
        return created

    def apply_invalidation_decay(self, repo: str) -> Dict[str, int]:
        """Penalize memories tied to deleted files or scopes under heavy churn."""
        workspace = resolve_git_workspace(_workspace_root())
        sync_state = self.db.get_repo_sync_state(repo)
        git_snapshot = collect_git_churn(
            workspace,
            since_ref=sync_state.get("last_git_head"),
        )
        git_observations = self._ingest_git_churn_observations(repo, git_snapshot)

        scoped = self._active_scoped_memories(repo)
        edit_obs = self.db.list_observations(
            repo=repo, status="pending", event_type="edit_cluster", limit=500
        )

        missing_file = 0
        churn_decayed = 0
        git_churn_decayed = 0

        for mem in scoped:
            scope = mem["scope"]
            scope_path = Path(scope)
            if not scope_path.is_absolute():
                full_path = workspace / scope
            else:
                full_path = scope_path

            git_deleted = bool(
                git_snapshot
                and any(_scope_matches_file(scope, p) for p in git_snapshot.deleted)
            )

            if git_deleted or not full_path.exists():
                if self._apply_confidence_penalty(
                    repo,
                    mem["memory_id"],
                    mem["confidence"],
                    INVALIDATION_CONFIDENCE_FACTOR,
                    finding_type="invalidation_decay",
                    severity="low",
                    action=(
                        f"Scope file '{scope}' no longer exists in workspace — "
                        f"memory confidence reduced. Confirm or deprecate."
                    ),
                    dedup_key=_dedup_key("missing_file", repo, mem["memory_id"]),
                    evidence=[
                        {"type": "missing_scope", "content": scope},
                        *(
                            [{"type": "git_deleted", "content": "true"}]
                            if git_deleted
                            else []
                        ),
                    ],
                ):
                    missing_file += 1
                continue

            scope_n = _normalize_path(scope)
            editor_hits = sum(
                1 for o in edit_obs
                if scope_n in _normalize_path(o.get("content") or "")
            )
            git_hits = (
                count_scope_git_hits(scope, git_snapshot)
                if git_snapshot
                else 0
            )
            effective_hits = editor_hits + (
                GIT_CHURN_EQUIVALENT_HITS if git_hits > 0 else 0
            )
            git_only = git_hits > 0 and editor_hits < SCOPE_CHURN_THRESHOLD

            if effective_hits < SCOPE_CHURN_THRESHOLD:
                continue

            if self._apply_confidence_penalty(
                repo,
                mem["memory_id"],
                mem["confidence"],
                SCOPE_CHURN_DECAY_FACTOR,
                finding_type="scope_churn",
                severity="low",
                action=(
                    f"Scope '{scope}' saw churn (editor={editor_hits}, git_paths={git_hits}) — "
                    f"memory may be stale. Review before trusting."
                ),
                dedup_key=_dedup_key("churn", repo, mem["memory_id"]),
                evidence=[
                    {"type": "churn_count", "content": str(effective_hits)},
                    {"type": "git_hits", "content": str(git_hits)},
                ],
            ):
                churn_decayed += 1
                if git_only:
                    git_churn_decayed += 1

        if git_snapshot:
            self.db.set_repo_sync_state(repo, last_git_head=git_snapshot.head)

        return {
            "invalidation_missing_file": missing_file,
            "invalidation_churn": churn_decayed,
            "invalidation_git_churn": git_churn_decayed,
            "git_churn_observations": git_observations,
        }

    # ── Pass 4: verification success reinforcement ───────────────────────────
    def reinforce_verification_success(self, repo: str) -> Dict[str, int]:
        """Positive signal: lightly reinforce node-linked patterns after verify."""
        pending = self.db.list_observations(
            repo=repo, status="pending", event_type=EVENT_VERIFICATION_SUCCESS, limit=100
        )
        reinforced = 0
        processed = 0

        for obs in pending:
            node_id = obs.get("node_id")
            if not node_id:
                self.db.resolve_observation(obs["observation_id"], "rejected")
                continue

            cursor = self.db.conn.cursor()
            rows = cursor.execute(
                """
                SELECT memory_id, confidence FROM memory_entries
                WHERE repo = ? AND status = 'active' AND node_id = ?
                  AND type = 'learned_pattern'
                """,
                (repo, node_id),
            ).fetchall()

            for row in rows:
                old = row["confidence"]
                new = min(SUCCESS_REINFORCEMENT_CAP, old + SUCCESS_REINFORCEMENT_DELTA)
                if new - old < 0.005:
                    continue
                self.db.update_memory_entry(row["memory_id"], confidence=new)
                reinforced += 1

            self.db.resolve_observation(obs["observation_id"], "accepted")
            processed += 1

        return {
            "verification_success_processed": processed,
            "memories_reinforced": reinforced,
        }

    # ── Pass 5: confidence decay (age) ───────────────────────────────────────
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

    # ── Pass 6: conflict aggregator ──────────────────────────────────────────
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

    # ── Pass 7: missing-node detector ────────────────────────────────────────
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

    # ── Pass 8: semantic duplicate suggestions (Tier D) ────────────────────────
    def suggest_duplicate_merges(self, repo: str) -> Dict[str, int]:
        """Find paraphrase-near memories via cached embeddings."""
        entries = self.db.list_memory_entries(
            repo=repo,
            status="active",
            limit=500,
        )
        entries = [
            e for e in entries
            if e["type"] in ("learned_pattern", "explicit_rule", "repo_fact")
        ]
        index_stats = index_memory_embeddings(self.db, entries)

        rows = self.db.list_memory_embeddings(repo)
        rows_by_method: Dict[str, List[dict]] = {}
        for row in rows:
            rows_by_method.setdefault(row["method"], []).append(row)

        suggested = 0
        seen_pairs: set[tuple[str, str]] = set()

        for method, method_rows in rows_by_method.items():
            threshold = duplicate_threshold_for(method)
            pair_list = find_embedding_duplicate_pairs(
                self.db.conn,
                method,
                method_rows,
                threshold,
            )

            for id_a, id_b, sim in pair_list:
                pair = tuple(sorted((id_a, id_b)))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                finding_id = self.db.create_finding(
                    repo=repo,
                    finding_type="semantic_duplicate",
                    severity="low",
                    action=(
                        f"Memories {id_a[:8]} and {id_b[:8]} look like paraphrases "
                        f"(similarity={sim:.2f}, method={method}). "
                        f"Merge or deprecate one via the queue."
                    ),
                    dedup_key=_dedup_key("semantic_dup", repo, pair[0], pair[1]),
                    evidence=[
                        {"type": "memory", "content": id_a},
                        {"type": "memory", "content": id_b},
                        {"type": "similarity", "content": f"{sim:.3f}"},
                        {"type": "embed_method", "content": method},
                    ],
                    memory_id=id_a,
                )
                if finding_id:
                    suggested += 1

        return {
            "duplicate_pairs_suggested": suggested,
            "embeddings_indexed": index_stats.get("embeddings_indexed", len(rows)),
            "embed_method": index_stats.get("embed_method"),
            "vec_index": sqlite_vec_enabled(),
        }


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
