"""
Observation capture helpers — Tier B of the memory trickle-up ladder.

Why this module exists
----------------------
The memory system splits **capture** from **judgment** on purpose:

    raw events  →  observations (draft, append-only, low confidence)
                →  reconciliation (deterministic: Jaccard, age decay, FTS)
                →  decision queue (proposals only — engine never enforces)
                →  agent / human adjudication (optional LLM *draft* at this gate)
                →  active memory / explicit_rule

These helpers implement the **missing capture paths** identified in the roadmap:
verification success, chat exchanges, git reverts, and pre-push HIGH warnings.

What these helpers NEVER do
---------------------------
- Write ``learned_pattern`` or ``explicit_rule`` directly (failures still use
  the existing ``_save_failure_memory`` path in handlers).
- Call an LLM — semantic categorization belongs at the adjudication gate.
- Auto-promote anything into active memory.

What they ALWAYS do
-------------------
- Insert into ``observations`` with ``status=pending``.
- Tag ``event_type`` so reconciliation passes can route on it later.
- Fail silently (log a warning) so hooks, git, and verification never break.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .database import MemoryDatabase
from .git_context import GitContext, collect_git_context

logger = logging.getLogger("turingmind-mcp.observation-capture")

# ── Event types (stable contract for reconcile passes) ───────────────────────
# Reconciliation can filter/group on these without parsing free-text content.
EVENT_CHAT_EXCHANGE = "chat_exchange"
EVENT_VERIFICATION_SUCCESS = "verification_success"
EVENT_GIT_REVERT = "git_revert"
EVENT_PRE_PUSH_HIGH = "pre_push_high_gap"


def _git_observation_fields(git: Optional[GitContext] = None) -> Dict[str, Any]:
    """Map GitContext → create_observation kwargs (TC-BR-08 poller path)."""
    ctx = git if git is not None else collect_git_context()
    if ctx is None:
        return {
            "branch": None,
            "head_sha": None,
            "git_dirty": 0,
            "git_context": None,
        }
    payload = {
        "branch": ctx.branch,
        "head": ctx.head,
        "dirty": ctx.dirty,
    }
    if ctx.default_branch:
        payload["default_branch"] = ctx.default_branch
    return {
        "branch": ctx.branch,
        "head_sha": ctx.head,
        "git_dirty": 1 if ctx.dirty else 0,
        "git_context": json.dumps(payload, sort_keys=True),
    }


def record_observation(
    db: MemoryDatabase,
    repo: str,
    event_type: str,
    content: str,
    *,
    source: str,
    confidence: float = 0.3,
    evidence: Optional[List[Dict[str, Any]]] = None,
    node_id: Optional[str] = None,
    git: Optional[GitContext] = None,
) -> Optional[str]:
    """Append a single draft observation. Returns observation_id or None on failure.

    This is the lowest-level capture primitive. Hooks and handlers should prefer
    the typed helpers below when they exist — those encode the right confidence
    and evidence shape for each signal type.
    """
    if not repo or not content.strip():
        return None
    git_fields = _git_observation_fields(git)
    try:
        obs_id = db.create_observation(
            repo=repo,
            event_type=event_type,
            content=content[:4000],
            source=source,
            confidence=confidence,
            evidence=evidence,
            node_id=node_id,
            branch=git_fields["branch"],
            head_sha=git_fields["head_sha"],
            git_dirty=git_fields["git_dirty"],
            git_context=git_fields["git_context"],
        )
        logger.info(
            "Observation recorded [%s] repo=%s type=%s id=%s",
            source,
            repo,
            event_type,
            obs_id[:8],
        )
        return obs_id
    except Exception as exc:
        # Capture must never break the caller's primary job (push, verify, commit).
        logger.warning("Failed to record observation (%s): %s", event_type, exc)
        return None


def record_verification_success_observation(
    db: MemoryDatabase,
    *,
    repo: str,
    node_id: str,
    node_title: str,
    confidence: float,
    detail: str,
    source: str = "run_verification",
) -> Optional[str]:
    """Capture a positive signal when a SpecNode reaches VERIFIED.

    Today the gradient is failure-heavy (blocks, classify_failure). Success
    observations give the recurrence miner and future revert-penalty pass
    something to *reinforce* instead of only punishing mistakes.

    Still an observation — not a learned_pattern. Promotion requires queue
    review if the same success pattern recurs.
    """
    content = (
        f"Verification succeeded on '{node_title}' "
        f"(confidence={confidence:.2f}): {detail[:300]}"
    )
    return record_observation(
        db,
        repo,
        EVENT_VERIFICATION_SUCCESS,
        content,
        source=source,
        confidence=min(0.55, max(0.35, confidence * 0.7)),
        evidence=[{"type": "verification", "content": detail[:500]}],
        node_id=node_id,
    )


def record_git_revert_observation(
    db: MemoryDatabase,
    *,
    repo: str,
    commit_sha: str,
    subject: str,
    files: List[str],
) -> Optional[str]:
    """Capture structured negative feedback when a commit is a revert.

    A revert means "that change was wrong." The observation itself does not
    subtract confidence from existing memories — that is Tier C work for
    ``reconcile.py`` (scope-matched revert penalty pass). Here we only ensure
    the signal enters the funnel.
    """
    file_sample = ", ".join(files[:15])
    content = (
        f"Git revert {commit_sha[:8]}: {subject[:200]}"
        + (f" — files: {file_sample}" if file_sample else "")
    )
    return record_observation(
        db,
        repo,
        EVENT_GIT_REVERT,
        content,
        source="antigravity-hook",
        confidence=0.45,
        evidence=[
            {"type": "commit_sha", "content": commit_sha},
            {"type": "files", "content": file_sample[:1000]},
        ],
    )


def record_pre_push_high_observation(
    db: MemoryDatabase,
    *,
    repo: str,
    summary_lines: List[str],
) -> Optional[str]:
    """Capture HIGH-severity graph gaps that today only print a warning.

    CRITICAL gaps already block and write memory/observations. HIGH gaps
    evaporate unless we log them here. Confidence stays low (0.4) because
    HIGH items are often hygiene warnings, not hard laws.
    """
    joined = "; ".join(summary_lines)[:800] or "HIGH severity gaps on pre-push"
    return record_observation(
        db,
        repo,
        EVENT_PRE_PUSH_HIGH,
        f"HIGH gaps at push time: {joined}",
        source="antigravity-hook",
        confidence=0.4,
        evidence=[{"type": "queue_excerpt", "content": joined[:500]}],
    )


def build_chat_exchange_content(metadata: Dict[str, Any]) -> Optional[str]:
    """Build a dumb text excerpt from Cursor chat metadata — no LLM.

    We intentionally store raw user/assistant text. Whether the exchange is a
    "correction" vs generic Q&A is **semantic judgment** — the adjudication
    gate (agent/human/optional LLM) decides that when promoting, not capture.
    """
    prompts = metadata.get("userPrompts") or []
    responses = metadata.get("assistantResponses") or []
    if not prompts and not responses:
        return None

    user_text = (prompts[-1].get("text") or "").strip() if prompts else ""
    asst_text = (responses[-1].get("text") or "").strip() if responses else ""

    if not user_text and not asst_text:
        return None

    parts = []
    if user_text:
        parts.append(f"user: {user_text[:1200]}")
    if asst_text:
        parts.append(f"assistant: {asst_text[:1200]}")
    return "\n".join(parts)


def record_chat_exchange_observation(
    db: MemoryDatabase,
    *,
    repo: str,
    composer_id: str,
    metadata: Dict[str, Any],
) -> Optional[str]:
    """Record a dumb chat exchange observation — the Tier B funnel path.

    The full ``capture_exchange()`` flow still exists for LLM-enhanced chat
    analysis plans (bridge/extension). This lighter path ensures every
    completed exchange enters ``observations`` even when nobody calls
    ``turingmind_capture_exchange``. Reconciliation decides later whether the
    exchange looks like a correction, a pattern, or noise.
    """
    content = build_chat_exchange_content(metadata)
    if not content:
        return None
    return record_observation(
        db,
        repo,
        EVENT_CHAT_EXCHANGE,
        content,
        source="chat-poller",
        confidence=0.3,
        evidence=[{"type": "composer_id", "content": composer_id[:36]}],
    )
