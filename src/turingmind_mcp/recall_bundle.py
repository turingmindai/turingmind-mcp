from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field

logger = logging.getLogger("turingmind-mcp.recall-bundle")

# In-memory session recall history cache has been retired in favor of SQLite coding_sessions.

def get_recall_history(session_key: str) -> List[str]:
    """Deprecated: returns an empty list. Recall history is managed dynamically in SQLite."""
    return []


def extend_recall_history(session_key: str, memory_ids: List[str]) -> None:
    """Deprecated no-op. SQLite coding_sessions.recall_history is the single source of truth."""
    pass


def reset_recall_history_cache() -> None:
    """No-op (historically cleared the in-memory stub)."""
    pass


def _memory_item_from_row(
    row: Dict[str, Any],
    memory_type: Literal["explicit_rule", "learned_pattern", "repo_fact"],
    score: float,
) -> Optional[MemoryItem]:
    """Build a MemoryItem; skip invalid rows fail-soft per CP-SPEC-05."""
    try:
        return MemoryItem(
            memory_id=row["memory_id"],
            type=memory_type,
            content=row["content"],
            scope=row["scope"],
            score=score,
            branch=row.get("branch"),
        )
    except Exception as exc:
        logger.warning(
            "BUNDLE_SCHEMA_FAIL: skipping memory_id=%s: %s",
            row.get("memory_id"),
            exc,
        )
        return None


# ==========================================
# CP-SPEC-01: JSON Schema Models (Pydantic v2)
# ==========================================

class MemoryItem(BaseModel):
    memory_id: str
    type: Literal["explicit_rule", "learned_pattern", "repo_fact"]
    content: str = Field(..., max_length=2000)
    scope: str
    score: float = Field(0.0, ge=0.0, le=1.0)
    branch: Optional[str] = None

class QueueItem(BaseModel):
    gap_type: str
    severity: Literal["critical", "high", "medium", "low"]
    action: str = Field(..., max_length=500)
    finding_id: Optional[str] = None
    memory_id: Optional[str] = None

class PolicySpec(BaseModel):
    hydrate_required: bool
    code: Optional[str] = Field(None, pattern=r"^TM-[A-Z]+-[0-9]{3}$")
    message: Optional[str] = Field(None, max_length=400)
    required_tools: List[str] = Field(default_factory=list)

class RecallBundle(BaseModel):
    explicit_rules: List[MemoryItem] = Field(..., max_length=8)
    learned_patterns: List[MemoryItem] = Field(..., max_length=8)
    queue_top: List[QueueItem] = Field(..., max_length=5)
    policy: PolicySpec


# ==========================================
# CP-SPEC-03: Delta Hydration & Ranking
# ==========================================

def rank_memories(
    db: Any,
    repo: str,
    files: List[str],
    task: Optional[str] = None,
    branch: Optional[str] = None,
    rules_limit: int = 8,
    patterns_limit: int = 8,
) -> Dict[str, List[MemoryItem]]:
    """Query, de-duplicate, and rank explicit rules and learned patterns."""
    from .memory_ranker import rank_rules_and_patterns

    return rank_rules_and_patterns(
        db=db,
        repo=repo,
        files=files,
        task=task,
        branch=branch,
        rules_limit=rules_limit,
        patterns_limit=patterns_limit,
    )


def compute_delta_bundle(
    candidate_rules: List[MemoryItem],
    candidate_patterns: List[MemoryItem],
    recall_history: List[str],
) -> Dict[str, Any]:
    """Determine delta rules/patterns that haven't been recalled yet."""
    history_set = set(recall_history)

    delta_rules = [r for r in candidate_rules if r.memory_id not in history_set]
    delta_patterns = [p for p in candidate_patterns if p.memory_id not in history_set]

    added_ids = [r.memory_id for r in delta_rules] + [p.memory_id for p in delta_patterns]

    unchanged = len(added_ids) == 0

    return {
        "added_rule_ids": added_ids,
        "removed_rule_ids": [],
        "unchanged": unchanged,
        "delta_rules": delta_rules,
        "delta_patterns": delta_patterns,
    }
