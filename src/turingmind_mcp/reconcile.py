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
    9. Branch lifecycle           — merge_commit obs → branch_promotion findings;
                                  stale branches → archive_branch_memories (SPEC-BR-06/07/11)

LLM distillation belongs at the adjudication gate (agent/human reviews queue
findings) — not in this module. See Tier D optional adjudication workflow.

Triggered via POST /api/v2/reconcile, `turingmind reconcile`, or the
background loop in the API server.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .database import MemoryDatabase
from .git_churn import collect_git_churn, count_scope_git_hits, resolve_git_workspace
from .git_context import collect_git_context
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
STALE_BRANCH_DAYS = 30
EVENT_MERGE_COMMIT = "merge_commit"
PROMOTABLE_MEMORY_TYPES = frozenset({"learned_pattern", "explicit_rule"})

# SPEC-BR-07: documented lifecycle finding types (no ad-hoc strings)
LIFECYCLE_FINDING_TYPES = frozenset({"branch_promotion", "archive_branch_memories"})
RESOLVE_ACTION_FINDING_TYPES = frozenset(LIFECYCLE_FINDING_TYPES)

_SCOPE_PATH_RE = re.compile(
    r"(?:in|changed in|file)\s+([\w./-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|md|yaml|yml|json|toml|css|html))",
    re.IGNORECASE,
)


def _tokens(text: str) -> frozenset:
    return frozenset(w for w in text.lower().split() if len(w) > 2)


def _similarity(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _dedup_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _observation_branch_key(obs: dict) -> str:
    """Bucket key for Pass 1 clustering — NULL/legacy obs share one bucket."""
    branch = obs.get("branch")
    return branch if branch else ""


def _scope_for_mined_candidate(exemplar: dict) -> str:
    """Derive scope; avoid repo-wide default when observations were branch-scoped."""
    content = exemplar.get("content") or ""
    match = _SCOPE_PATH_RE.search(content)
    if match:
        return match.group(1)
    branch = exemplar.get("branch")
    if branch:
        return f"branch:{branch}"
    return "repo"


def _git_fields_for_mined_candidate(exemplar: dict) -> Dict[str, Any]:
    """Inherit branch metadata from exemplar observation."""
    branch = exemplar.get("branch")
    git_dirty = int(exemplar.get("git_dirty") or 0)
    if branch:
        from .git_context import derive_scope_tier

        return {
            "branch": branch,
            "head_sha": exemplar.get("head_sha"),
            "git_dirty": git_dirty,
            "scope_tier": derive_scope_tier(branch, bool(git_dirty)),
        }
    return {
        "branch": None,
        "head_sha": None,
        "git_dirty": 0,
        "scope_tier": "repo",
    }


def _memory_subject_to_churn(mem: dict, current_branch: Optional[str]) -> bool:
    """Branch-scoped memories ignore churn on other branches (SPEC-BR-03)."""
    mem_branch = mem.get("branch")
    if mem_branch is None:
        return True
    if not current_branch or current_branch == "HEAD":
        return False
    return mem_branch == current_branch


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
            SELECT memory_id, scope, confidence, type, node_id, content, branch
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
        stats.update(self.branch_lifecycle(repo))
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

        clusters_by_bucket: Dict[tuple, List[List[dict]]] = {}
        for obs in pending:
            event_type = obs.get("event_type") or "unknown"
            bucket = (_observation_branch_key(obs), event_type)
            type_clusters = clusters_by_bucket.setdefault(bucket, [])
            for cluster in type_clusters:
                if self._observations_match(obs, cluster[0], vec_map, embed_method):
                    cluster.append(obs)
                    break
            else:
                type_clusters.append([obs])

        clusters: List[List[dict]] = [
            cluster
            for type_clusters in clusters_by_bucket.values()
            for cluster in type_clusters
        ]

        candidates = 0
        accepted = 0
        for cluster in clusters:
            if len(cluster) < RECURRENCE_THRESHOLD:
                continue
            exemplar = cluster[0]
            git_fields = _git_fields_for_mined_candidate(exemplar)
            content = (
                f"Recurring {exemplar.get('event_type', 'activity')} ({len(cluster)}x): "
                f"{exemplar['content']}"
            )
            memory_id = self.db.create_memory_entry(
                repo=repo,
                memory_type="learned_pattern",
                content=content,
                scope=_scope_for_mined_candidate(exemplar),
                confidence=CANDIDATE_CONFIDENCE,
                status="candidate",
                created_by="reconcile:recurrence_miner",
                node_id=exemplar.get("node_id"),
                **git_fields,
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
                dedup_key=_dedup_key(
                    "promotion",
                    repo,
                    exemplar.get("branch") or "",
                    exemplar["content"][:120],
                ),
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
        git_ctx = collect_git_context(workspace)
        current_branch = git_ctx.branch if git_ctx else None

        since_ref: Optional[str] = None
        if current_branch and current_branch != "HEAD":
            since_ref = self.db.get_branch_git_cursor(repo, current_branch)
            # First reconcile on a branch: bootstrap HEAD only — do not reuse
            # another branch's repo-global cursor (SPEC-BR-03 / TC-BR-FS03).
        elif since_ref is None:
            sync_state = self.db.get_repo_sync_state(repo)
            since_ref = sync_state.get("last_git_head")

        git_snapshot = collect_git_churn(
            workspace,
            since_ref=since_ref,
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
            if not _memory_subject_to_churn(mem, current_branch):
                continue

            scope = mem["scope"]
            if scope.startswith("branch:"):
                # Pseudo-scope from Pass 1 — not a workspace path (SPEC-BR-02).
                scope_path = None
                full_path = None
            else:
                scope_path = Path(scope)
                if not scope_path.is_absolute():
                    full_path = workspace / scope
                else:
                    full_path = scope_path

            git_deleted = bool(
                git_snapshot
                and full_path is not None
                and any(_scope_matches_file(scope, p) for p in git_snapshot.deleted)
            )

            if full_path is not None and (git_deleted or not full_path.exists()):
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
            if current_branch and current_branch != "HEAD":
                self.db.set_branch_git_cursor(repo, current_branch, git_snapshot.head)
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
        promoted_pairs = _promotion_lineage_pairs(entries)
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
                if pair in seen_pairs or pair in promoted_pairs:
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

    # ── Pass 9: branch lifecycle (SPEC-BR-06/07/11) ───────────────────────────
    def branch_lifecycle(self, repo: str) -> Dict[str, int]:
        """Merge promotions + stale-branch archive findings (consent-gated)."""
        touched_memory_ids: set[str] = set()
        promotions = self._propose_merge_promotions(repo, touched_memory_ids)
        archives = self._propose_stale_archives(repo, touched_memory_ids)
        return {
            "branch_promotions_suggested": promotions,
            "branch_archives_suggested": archives,
        }

    def _propose_merge_promotions(
        self, repo: str, touched_memory_ids: set[str]
    ) -> int:
        """Pending merge_commit observations → branch_promotion queue items."""
        pending = self.db.list_observations(
            repo=repo,
            status="pending",
            event_type=EVENT_MERGE_COMMIT,
            limit=50,
        )
        suggested = 0
        for obs in pending:
            source_branch = _merged_source_branch(obs)
            if not source_branch:
                self.db.resolve_observation(obs["observation_id"], "rejected")
                continue

            branch_memories = [
                m
                for m in self.db.list_memory_entries(
                    repo=repo, status="all", limit=5000
                )
                if m.get("branch") == source_branch
                and m.get("status") in ("active", "candidate")
                and m.get("type") in PROMOTABLE_MEMORY_TYPES
            ]

            created = 0
            for mem in branch_memories:
                mid = mem["memory_id"]
                if mid in touched_memory_ids:
                    continue
                finding_id = self.db.create_finding(
                    repo=repo,
                    finding_type="branch_promotion",
                    severity="medium",
                    action=(
                        f"Merge detected — promote '{source_branch}' memory "
                        f"{mid[:8]} to repo-wide (L4)?"
                    ),
                    dedup_key=_dedup_key(
                        "branch_promotion", repo, source_branch, mid
                    ),
                    evidence=[
                        {"type": "branch", "content": source_branch},
                        {
                            "type": "merge_observation",
                            "content": obs["observation_id"],
                        },
                        {"type": "memory", "content": mid},
                    ],
                    memory_id=mid,
                )
                if finding_id:
                    suggested += 1
                    created += 1
                    touched_memory_ids.add(mid)

            self.db.resolve_observation(obs["observation_id"], "accepted")
            if created == 0 and not branch_memories:
                logger.debug(
                    "Merge obs %s: no branch memories on %s",
                    obs["observation_id"][:8],
                    source_branch,
                )
        return suggested

    def _propose_stale_archives(
        self, repo: str, touched_memory_ids: set[str]
    ) -> int:
        """Surface stale-branch archive findings; skip memories touched this run."""
        now = datetime.now(timezone.utc)
        cursor = self.db.conn.cursor()
        rows = cursor.execute(
            """
            SELECT branch, MAX(updated_at) AS last_activity, COUNT(*) AS cnt
            FROM memory_entries
            WHERE repo = ? AND branch IS NOT NULL AND status = 'active'
            GROUP BY branch
            """,
            (repo,),
        ).fetchall()

        archives = 0
        for row in rows:
            branch = row["branch"]
            if not branch:
                continue
            try:
                last = datetime.fromisoformat(str(row["last_activity"]).replace(" ", "T"))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            age_days = (now - last).total_seconds() / 86400
            if age_days < STALE_BRANCH_DAYS:
                continue

            branch_memories = cursor.execute(
                """
                SELECT memory_id FROM memory_entries
                WHERE repo = ? AND branch = ? AND status = 'active'
                """,
                (repo, branch),
            ).fetchall()
            memory_ids = [r["memory_id"] for r in branch_memories]
            if any(mid in touched_memory_ids for mid in memory_ids):
                continue

            finding_id = self.db.create_finding(
                repo=repo,
                finding_type="archive_branch_memories",
                severity="low",
                action=(
                    f"Branch '{branch}' inactive for {age_days:.0f}d — "
                    f"archive {len(memory_ids)} branch-scoped memories?"
                ),
                dedup_key=_dedup_key("archive_branch", repo, branch),
                evidence=[
                    {"type": "branch", "content": branch},
                    {"type": "memory_count", "content": str(len(memory_ids))},
                    *(
                        {"type": "memory", "content": mid}
                        for mid in memory_ids[:20]
                    ),
                ],
                memory_id=memory_ids[0] if memory_ids else None,
            )
            if finding_id:
                archives += 1
                touched_memory_ids.update(memory_ids)

        return archives


def _evidence_value(obs: dict, ev_type: str) -> Optional[str]:
    for ev in obs.get("evidence") or []:
        if ev.get("type") == ev_type:
            content = ev.get("content")
            return str(content) if content is not None else None
    return None


def _default_branch_from_obs(obs: dict) -> Optional[str]:
    raw = obs.get("git_context")
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        val = data.get("default_branch")
        return str(val) if val else None
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None


def _normalize_branch_name(name: str) -> str:
    cleaned = name.strip()
    for prefix in ("remotes/origin/", "origin/"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    if "~" in cleaned:
        cleaned = cleaned.split("~", 1)[0]
    if "^" in cleaned:
        cleaned = cleaned.split("^", 1)[0]
    return cleaned


def _merged_source_branch(obs: dict) -> Optional[str]:
    """Infer the merged-away feature branch from merge_commit observation."""
    default_branch = _default_branch_from_obs(obs)
    merge_branches = _evidence_value(obs, "merge_branches")
    if merge_branches:
        for raw in merge_branches.split(","):
            name = _normalize_branch_name(raw)
            if not name or name == "HEAD":
                continue
            if default_branch and name == default_branch:
                continue
            return name

    branch = obs.get("branch")
    if branch and branch not in ("HEAD",) and branch != default_branch:
        return str(branch)
    return None


def _promotion_lineage_pairs(entries: List[dict]) -> set[tuple[str, str]]:
    """Pairs linked by promoted_from — skip Pass 8 semantic_duplicate (SPEC-BR-06)."""
    pairs: set[tuple[str, str]] = set()
    for entry in entries:
        source_id = entry.get("promoted_from")
        if source_id:
            pairs.add(tuple(sorted((entry["memory_id"], source_id))))
    return pairs


def execute_branch_promotion(db: MemoryDatabase, finding: dict) -> str:
    """Accept branch_promotion: create L4 copy with lineage (SPEC-BR-06)."""
    source_id = finding.get("memory_id")
    if not source_id:
        raise ValueError("branch_promotion finding missing memory_id")
    source = db.get_memory_entry(source_id)
    if not source:
        raise ValueError(f"source memory not found: {source_id}")
    if source.get("status") not in ("active", "candidate"):
        raise ValueError("branch_promotion source must be active or candidate")

    scope = source["scope"]
    if scope.startswith("branch:"):
        scope = "repo"

    l4_id = db.create_memory_entry(
        repo=source["repo"],
        memory_type=source["type"],
        content=source["content"],
        scope=scope,
        confidence=source["confidence"],
        status="active",
        created_by="reconcile:branch_promotion",
        node_id=source.get("node_id"),
        branch=None,
        head_sha=None,
        git_dirty=0,
        scope_tier="repo",
        promoted_from=source_id,
    )
    db.update_memory_entry(source_id, status="deprecated")
    return l4_id


def execute_branch_archive(db: MemoryDatabase, finding: dict) -> int:
    """Accept archive_branch_memories: idempotent tombstone (SPEC-BR-11)."""
    branch = None
    for ev in finding.get("evidence") or []:
        if ev.get("type") == "branch":
            branch = ev.get("content")
            break
    if not branch:
        raise ValueError("archive_branch_memories finding missing branch evidence")

    archived = 0
    for mem in db.list_memory_entries(repo=finding["repo"], status="active", limit=5000):
        if mem.get("branch") != branch:
            continue
        if mem["status"] != "active":
            continue
        db.update_memory_entry(mem["memory_id"], status="deprecated")
        archived += 1
    return archived


def apply_finding_resolution(
    db: MemoryDatabase, finding_id: str, status: str
) -> bool:
    """Resolve a finding and run consent-gated side effects when actioned."""
    if status not in ("actioned", "dismissed"):
        raise ValueError(f"Invalid finding status: {status}")

    finding = db.get_finding(finding_id)
    if not finding:
        return False
    if finding.get("status") != "pending":
        return True

    if status == "actioned":
        finding_type = finding.get("finding_type")
        if finding_type not in RESOLVE_ACTION_FINDING_TYPES:
            pass
        elif finding_type == "branch_promotion":
            execute_branch_promotion(db, finding)
        elif finding_type == "archive_branch_memories":
            execute_branch_archive(db, finding)

    return db.resolve_finding(finding_id, status)


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
