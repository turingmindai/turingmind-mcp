"""Branch-aware memory recall ranking (Phase 4.2)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .git_context import (
    DETACHED_BRANCH_LABEL,
    GitContext,
    branch_memory_ranking_enabled,
    collect_git_context,
)

logger = logging.getLogger(__name__)

# Rank weights — higher is better (SPEC-BR-04 / plan Phase 4.2).
SCORE_L2_WORKING_TREE = 1.5
SCORE_L3_BRANCH = 1.2
SCORE_L4_REPO = 1.0
SCORE_OTHER_BRANCH = 0.3
SCORE_EXCLUDED = 0.0


@dataclass(frozen=True)
class RecallContext:
    """Git context used for branch-aware recall."""

    branch: Optional[str]
    head: Optional[str]
    dirty: bool
    detached: bool
    ranking_enabled: bool
    include_other_branches: bool


def resolve_recall_context(
    branch: Optional[str] = None,
    head: Optional[str] = None,
    dirty: Optional[bool] = None,
    *,
    include_other_branches: bool = False,
) -> RecallContext:
    """Resolve recall git context from params or TURINGMIND_WORKSPACE_DIR (SPEC-BR-10)."""
    ranking = branch_memory_ranking_enabled()
    if branch is None and head is None and dirty is None:
        inferred = collect_git_context()
        if inferred is not None:
            return RecallContext(
                branch=inferred.branch,
                head=inferred.head,
                dirty=inferred.dirty,
                detached=inferred.detached,
                ranking_enabled=ranking,
                include_other_branches=include_other_branches,
            )
        if ranking:
            logger.debug(
                "Branch recall enabled but git context could not be inferred; "
                "using L4 + file scope only"
            )
        return RecallContext(
            branch=None,
            head=None,
            dirty=False,
            detached=False,
            ranking_enabled=ranking,
            include_other_branches=include_other_branches,
        )

    detached = branch == DETACHED_BRANCH_LABEL
    return RecallContext(
        branch=branch,
        head=head,
        dirty=bool(dirty) if dirty is not None else False,
        detached=detached,
        ranking_enabled=ranking,
        include_other_branches=include_other_branches,
    )


def branch_rank_score(entry: Dict[str, Any], recall: RecallContext) -> float:
    """Compute branch recall weight for an entry."""
    if not recall.ranking_enabled:
        return SCORE_L4_REPO

    entry_branch = entry.get("branch")
    entry_head = entry.get("head_sha")
    entry_dirty = bool(entry.get("git_dirty"))
    scope_tier = entry.get("scope_tier") or "repo"

    if entry_branch is None:
        return SCORE_L4_REPO

    if recall.detached:
        if recall.head and entry_head == recall.head:
            if entry_dirty and recall.dirty and scope_tier == "working_tree":
                return SCORE_L2_WORKING_TREE
            return SCORE_L3_BRANCH
        if recall.include_other_branches:
            return SCORE_OTHER_BRANCH
        return SCORE_EXCLUDED

    if entry_branch == recall.branch:
        if (
            scope_tier == "working_tree"
            and entry_dirty
            and recall.dirty
            and recall.head
            and entry_head == recall.head
        ):
            return SCORE_L2_WORKING_TREE
        return SCORE_L3_BRANCH

    if recall.include_other_branches:
        return SCORE_OTHER_BRANCH
    return SCORE_EXCLUDED


def sort_entries_by_branch_rank(
    entries: List[Dict[str, Any]],
    recall: RecallContext,
) -> List[Dict[str, Any]]:
    """Sort by branch rank (desc), then recency."""
    if not recall.ranking_enabled:
        return entries

    ranked = [e for e in entries if branch_rank_score(e, recall) > SCORE_EXCLUDED]
    ranked.sort(
        key=lambda row: (
            -branch_rank_score(row, recall),
            row.get("created_at") or "",
        ),
    )
    return ranked


def recall_context_from_git(ctx: Optional[GitContext], include_other_branches: bool = False) -> RecallContext:
    """Build RecallContext from a GitContext snapshot."""
    if ctx is None:
        return resolve_recall_context(include_other_branches=include_other_branches)
    return RecallContext(
        branch=ctx.branch,
        head=ctx.head,
        dirty=ctx.dirty,
        detached=ctx.detached,
        ranking_enabled=branch_memory_ranking_enabled(),
        include_other_branches=include_other_branches,
    )
