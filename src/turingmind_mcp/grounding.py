"""Compose one-shot grounding payload from existing recall surfaces.

Reuses GET /memory/relevant + decision-queue + session cards — does not
invent a parallel recall stack.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .memory_manager import MemoryManager
from .profile_config import filter_decision_queue_gaps, is_memory_profile, PROFILE_MEMORY, PROFILE_GOVERNED

logger = logging.getLogger(__name__)

_CLUSTER_MARKERS = (
    "non-code edit",
    "no graph impact",
    "likely a refactor",
    "likely a targeted fix",
    "refactor burst",
    "code file(s) changed",
    "files across",
    "targeted_fix/",
    "cross_module/",
    "development/",
    "non_code/",
)


def _is_cluster_junk(content: str) -> bool:
    lower = (content or "").lower()
    return any(m in lower for m in _CLUSTER_MARKERS)


def _entry_summary(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "memory_id": entry.get("memory_id"),
        "type": entry.get("type"),
        "status": entry.get("status"),
        "content": entry.get("content"),
        "scope": entry.get("scope"),
        "confidence": entry.get("confidence"),
        "branch": entry.get("branch"),
    }


def compose_ground(
    manager: MemoryManager,
    *,
    repo: str,
    files: Optional[List[str]] = None,
    query: Optional[str] = None,
    limit: int = 10,
    queue_limit: int = 5,
    include_session_context: bool = True,
    branch: Optional[str] = None,
    head: Optional[str] = None,
    dirty: Optional[bool] = None,
    decision_queue_builder=None,
) -> Dict[str, Any]:
    """Return ``{ rules, patterns, working_set, top_actions, digest }``."""
    file_paths = list(files or [])
    if query and not file_paths:
        # Soft hint: treat query tokens as search when no files given
        pass

    relevant = manager.get_relevant_memory(
        repo=repo,
        file_paths=file_paths,
        exclude_types=["session_context"],
        limit=max(limit * 3, 30),
        branch=branch,
        head=head,
        dirty=dirty,
        include_session_context=False,
    )

    if query:
        q = query.lower()
        scored = []
        for e in relevant:
            content = (e.get("content") or "").lower()
            scope = (e.get("scope") or "").lower()
            hit = sum(1 for tok in q.split() if tok and (tok in content or tok in scope))
            scored.append((hit, e))
        scored.sort(key=lambda x: (-x[0], -(x[1].get("confidence") or 0)))
        relevant = [e for _, e in scored]

    rules = [_entry_summary(e) for e in relevant if e.get("type") == "explicit_rule"][:limit]
    patterns = [
        _entry_summary(e)
        for e in relevant
        if e.get("type") == "learned_pattern" and (e.get("confidence") or 0) >= 0.5
    ][:limit]

    working_set: List[Dict[str, Any]] = []
    if include_session_context:
        sessions = manager.db.list_memory_entries(
            repo, memory_type="session_context", status="active", page=1, limit=30
        )
        for e in sessions:
            if _is_cluster_junk(e.get("content") or ""):
                continue
            working_set.append(_entry_summary(e))
            if len(working_set) >= min(5, limit):
                break

    top_actions: List[Dict[str, Any]] = []
    if decision_queue_builder is not None:
        try:
            raw_queue = decision_queue_builder(repo=repo, limit=max(queue_limit * 3, 15))
            gaps = raw_queue if isinstance(raw_queue, list) else (raw_queue or {}).get("queue") or []
            gaps = filter_decision_queue_gaps(
                gaps,
                scope=PROFILE_MEMORY if is_memory_profile() else None,
            )
            top_actions = gaps[:queue_limit]
        except Exception:
            logger.warning("Grounding decision-queue compose failed", exc_info=True)

    digest_lines = ["# TuringMind ground digest", ""]
    if rules:
        digest_lines.append("## Explicit rules")
        for r in rules[:5]:
            digest_lines.append(f"- ({r.get('scope')}) {r.get('content')}")
        digest_lines.append("")
    if patterns:
        digest_lines.append("## Learned patterns")
        for p in patterns[:5]:
            digest_lines.append(f"- ({p.get('scope')}) {p.get('content')}")
        digest_lines.append("")
    if working_set:
        digest_lines.append("## Working set")
        for w in working_set[:3]:
            digest_lines.append(f"- {w.get('content')}")
        digest_lines.append("")
    if top_actions:
        digest_lines.append("## Top actions")
        for a in top_actions[:3]:
            digest_lines.append(f"- [{a.get('severity', 'medium')}] {a.get('action')}")
        digest_lines.append("")
    if len(digest_lines) <= 2:
        digest_lines.append("_No active rules or patterns for this repo yet._")
        digest_lines.append("")

    return {
        "repo": repo,
        "files": file_paths,
        "query": query,
        "rules": rules,
        "patterns": patterns,
        "working_set": working_set,
        "top_actions": top_actions,
        "digest": "\n".join(digest_lines).strip() + "\n",
        "scope": PROFILE_MEMORY if is_memory_profile() else PROFILE_GOVERNED,
    }
