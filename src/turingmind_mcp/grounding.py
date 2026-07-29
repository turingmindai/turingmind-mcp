"""Compose one-shot grounding payload from existing recall surfaces.

Reuses GET /memory/relevant + decision-queue + session cards — does not
invent a parallel recall stack. Supports multi-repo workspace unions.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .memory_manager import MemoryManager
from .profile_config import filter_decision_queue_gaps, is_memory_profile, PROFILE_MEMORY, PROFILE_GOVERNED
from .workspace import WorkspaceContext, merge_entry_lists, resolve_workspace

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


def _entry_summary(entry: Dict[str, Any], *, source_repo: Optional[str] = None) -> Dict[str, Any]:
    out = {
        "memory_id": entry.get("memory_id"),
        "type": entry.get("type"),
        "status": entry.get("status"),
        "content": entry.get("content"),
        "scope": entry.get("scope"),
        "confidence": entry.get("confidence"),
        "branch": entry.get("branch"),
        "repo": source_repo or entry.get("repo"),
    }
    return out


def _ground_one_repo(
    manager: MemoryManager,
    *,
    repo: str,
    file_paths: List[str],
    query: Optional[str],
    limit: int,
    include_session_context: bool,
    branch: Optional[str],
    head: Optional[str],
    dirty: Optional[bool],
) -> Dict[str, List[Dict[str, Any]]]:
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

    rules = [_entry_summary(e, source_repo=repo) for e in relevant if e.get("type") == "explicit_rule"]
    patterns = [
        _entry_summary(e, source_repo=repo)
        for e in relevant
        if e.get("type") == "learned_pattern" and (e.get("confidence") or 0) >= 0.5
    ]

    working_set: List[Dict[str, Any]] = []
    if include_session_context:
        sessions = manager.db.list_memory_entries(
            repo, memory_type="session_context", status="active", page=1, limit=30
        )
        for e in sessions:
            if _is_cluster_junk(e.get("content") or ""):
                continue
            working_set.append(_entry_summary(e, source_repo=repo))
            if len(working_set) >= min(5, limit):
                break

    return {"rules": rules, "patterns": patterns, "working_set": working_set}


def compose_ground(
    manager: MemoryManager,
    *,
    repo: Optional[str] = None,
    repos: Optional[List[str]] = None,
    workspace_id: Optional[str] = None,
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
    """Return ``{ rules, patterns, working_set, top_actions, digest }``.

    When ``workspace_id`` or multiple ``repos`` are provided, unions linked
    repos for recall. Patterns remain tagged with their source ``repo``.
    """
    file_paths = list(files or [])
    ws = resolve_workspace(
        workspace_id=workspace_id,
        repos=repos,
        primary_repo=repo,
    )
    target_repos = [r for r in ws.repos if r] or ([repo] if repo else [])
    if not target_repos:
        raise ValueError("repo, repos, or workspace_id with linked repos is required")

    primary = ws.primary_repo or target_repos[0]
    rule_batches: List[List[Dict[str, Any]]] = []
    pattern_batches: List[List[Dict[str, Any]]] = []
    working_batches: List[List[Dict[str, Any]]] = []

    # Workspace-scoped working-set cards (synthetic repo key)
    if include_session_context and ws.working_set_repo and ws.working_set_repo not in target_repos:
        try:
            ws_sessions = manager.db.list_memory_entries(
                ws.working_set_repo,
                memory_type="session_context",
                status="active",
                page=1,
                limit=10,
            )
            working_batches.append(
                [
                    _entry_summary(e, source_repo=ws.working_set_repo)
                    for e in ws_sessions
                    if not _is_cluster_junk(e.get("content") or "")
                ]
            )
        except Exception:
            logger.debug("workspace working-set lookup skipped", exc_info=True)

    for r in target_repos:
        # File-scoped relevance only against primary (paths belong to one tree)
        paths = file_paths if r == primary else []
        try:
            chunk = _ground_one_repo(
                manager,
                repo=r,
                file_paths=paths,
                query=query,
                limit=limit,
                include_session_context=include_session_context,
                branch=branch if r == primary else None,
                head=head if r == primary else None,
                dirty=dirty if r == primary else None,
            )
        except Exception:
            logger.warning("Ground compose skipped for repo=%s", r, exc_info=True)
            continue
        rule_batches.append(chunk["rules"])
        pattern_batches.append(chunk["patterns"])
        working_batches.append(chunk["working_set"])

    rules = merge_entry_lists(rule_batches, limit=limit)
    patterns = merge_entry_lists(pattern_batches, limit=limit)
    working_set = merge_entry_lists(working_batches, limit=min(5, limit))

    top_actions: List[Dict[str, Any]] = []
    if decision_queue_builder is not None:
        try:
            gap_batches: List[List[Dict[str, Any]]] = []
            for r in target_repos:
                if r.startswith("workspace/"):
                    continue
                raw_queue = decision_queue_builder(repo=r, limit=max(queue_limit * 3, 15))
                gaps = raw_queue if isinstance(raw_queue, list) else (raw_queue or {}).get("queue") or []
                for g in gaps:
                    g = dict(g)
                    g.setdefault("repo", r)
                    gap_batches.append([g])
            flat = [g for batch in gap_batches for g in batch]
            flat = filter_decision_queue_gaps(
                flat,
                scope=PROFILE_MEMORY if is_memory_profile() else None,
            )
            flat.sort(
                key=lambda g: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(
                    (g.get("severity") or "low").lower(), 9
                )
            )
            # Dedupe by finding_id / action
            seen = set()
            for g in flat:
                key = g.get("finding_id") or g.get("action") or str(g)
                if key in seen:
                    continue
                seen.add(key)
                top_actions.append(g)
                if len(top_actions) >= queue_limit:
                    break
        except Exception:
            logger.warning("Grounding decision-queue compose failed", exc_info=True)

    digest_lines = ["# TuringMind ground digest", ""]
    if ws.workspace_id:
        digest_lines.append(f"_workspace: `{ws.workspace_id}` · repos: {', '.join(target_repos)}_")
        digest_lines.append("")
    if rules:
        digest_lines.append("## Explicit rules")
        for r in rules[:5]:
            src = f" · {r.get('repo')}" if r.get("repo") and len(target_repos) > 1 else ""
            digest_lines.append(f"- ({r.get('scope')}{src}) {r.get('content')}")
        digest_lines.append("")
    if patterns:
        digest_lines.append("## Learned patterns")
        for p in patterns[:5]:
            src = f" · {p.get('repo')}" if p.get("repo") and len(target_repos) > 1 else ""
            digest_lines.append(f"- ({p.get('scope')}{src}) {p.get('content')}")
        digest_lines.append("")
    if working_set:
        digest_lines.append("## Working set")
        for w in working_set[:3]:
            digest_lines.append(f"- {w.get('content')}")
        digest_lines.append("")
    if top_actions:
        digest_lines.append("## Top actions")
        for a in top_actions[:3]:
            src = f" ({a.get('repo')})" if a.get("repo") and len(target_repos) > 1 else ""
            digest_lines.append(f"- [{a.get('severity', 'medium')}]{src} {a.get('action')}")
        digest_lines.append("")
    if len(digest_lines) <= 2 or (ws.workspace_id and len(digest_lines) <= 4 and not rules and not patterns):
        digest_lines.append("_No active rules or patterns for this workspace yet._")
        digest_lines.append("")

    return {
        "repo": primary,
        "repos": target_repos,
        "workspace_id": ws.workspace_id,
        "working_set_repo": ws.working_set_repo,
        "files": file_paths,
        "query": query,
        "rules": rules,
        "patterns": patterns,
        "working_set": working_set,
        "top_actions": top_actions,
        "digest": "\n".join(digest_lines).strip() + "\n",
        "scope": PROFILE_MEMORY if is_memory_profile() else PROFILE_GOVERNED,
    }
