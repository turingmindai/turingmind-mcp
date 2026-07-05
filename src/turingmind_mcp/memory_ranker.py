"""Shared memory ranking for control-plane recall and MemoryManager queries."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional, Tuple

from .recall_bundle import MemoryItem, _memory_item_from_row

logger = logging.getLogger("turingmind-mcp.memory-ranker")

MemoryType = Literal["explicit_rule", "learned_pattern"]


def normalize_files(files: List[str]) -> List[str]:
    """Normalize file paths for scope comparison."""
    return [f.replace("\\", "/").lstrip("./") for f in files if f]


def file_scope_score(scope: Optional[str], files: List[str]) -> float:
    """Score how well a memory scope matches touched files (0.0–1.0)."""
    if not files:
        return 0.5
    scope_n = (scope or "repo").replace("\\", "/").lstrip("./")
    if scope_n == "repo":
        return 0.5
    file_set = set(normalize_files(files))
    if scope_n in file_set:
        return 1.0
    for path in file_set:
        if path == scope_n or path.endswith("/" + scope_n) or scope_n.endswith("/" + path):
            return 1.0
        if scope_n in path or path in scope_n:
            return 0.85
    return 0.0


def score_explicit_rule(row: Dict[str, Any], files: List[str]) -> float:
    """Rank explicit rules: exact scope match beats repo-wide."""
    scope = row.get("scope") or "repo"
    file_set = set(normalize_files(files))
    if scope in file_set:
        return 1.0
    if scope == "repo":
        return 0.8
    return max(file_scope_score(scope, files), 0.6)


def collect_learned_patterns(
    db: Any,
    repo: str,
    files: List[str],
    *,
    task: Optional[str] = None,
    branch: Optional[str] = None,
) -> List[Tuple[Dict[str, Any], float]]:
    """Gather learned patterns with term weights."""
    raw_patterns: List[Tuple[Dict[str, Any], float]] = []
    seen_patterns: set[str] = set()

    try:
        for path in files:
            for pattern_row in db.list_memory_entries(
                repo=repo,
                memory_type="learned_pattern",
                status="active",
                scope=path,
                branch=branch,
                limit=10,
            ):
                mid = pattern_row["memory_id"]
                if mid not in seen_patterns:
                    seen_patterns.add(mid)
                    raw_patterns.append((pattern_row, 1.0))

        search_terms: List[str] = []
        if task:
            search_terms.append(task)
        for path in files:
            name_part = path.split("/")[-1]
            if name_part:
                search_terms.append(name_part)

        for term in search_terms[:3]:
            for pattern_row in db.list_memory_entries(
                repo=repo,
                memory_type="learned_pattern",
                status="active",
                search=term,
                branch=branch,
                limit=10,
            ):
                mid = pattern_row["memory_id"]
                if mid not in seen_patterns:
                    seen_patterns.add(mid)
                    raw_patterns.append((pattern_row, 0.7))
    except Exception as exc:
        logger.error("Error fetching learned patterns: %s", exc)

    return raw_patterns


def rank_rules_and_patterns(
    db: Any,
    repo: str,
    files: List[str],
    *,
    task: Optional[str] = None,
    branch: Optional[str] = None,
    rules_limit: int = 8,
    patterns_limit: int = 8,
) -> Dict[str, List[MemoryItem]]:
    """Query, de-duplicate, and rank explicit rules and learned patterns."""
    explicit_rules: List[MemoryItem] = []
    schema_failures = 0

    try:
        raw_rules = db.list_memory_entries(
            repo=repo,
            memory_type="explicit_rule",
            status="active",
            branch=branch,
            limit=50,
        )
    except Exception as exc:
        logger.error("Error fetching explicit rules: %s", exc)
        raw_rules = []

    for row in raw_rules:
        score = score_explicit_rule(row, files)
        item = _memory_item_from_row(row, "explicit_rule", score)
        if item:
            explicit_rules.append(item)
        elif row:
            schema_failures += 1

    learned_patterns: List[MemoryItem] = []
    for row, term_weight in collect_learned_patterns(
        db, repo, files, task=task, branch=branch
    ):
        confidence = float(row.get("confidence") or 0.5)
        scope_boost = file_scope_score(row.get("scope"), files)
        combined = confidence * term_weight * (0.5 + 0.5 * scope_boost)
        score = float(round(combined, 4))
        item = _memory_item_from_row(row, "learned_pattern", score)
        if item:
            learned_patterns.append(item)
        elif row:
            schema_failures += 1

    explicit_rules.sort(key=lambda x: x.score, reverse=True)
    learned_patterns.sort(key=lambda x: x.score, reverse=True)

    return {
        "explicit_rules": explicit_rules[:rules_limit],
        "learned_patterns": learned_patterns[:patterns_limit],
        "schema_failures": schema_failures,
    }


def entry_relevance_score(entry: Dict[str, Any], files: List[str]) -> float:
    """Unified relevance score for MemoryManager result ordering."""
    memory_type = entry.get("type") or entry.get("memory_type")
    if memory_type == "explicit_rule":
        return score_explicit_rule(entry, files)
    if memory_type == "learned_pattern":
        confidence = float(entry.get("confidence") or 0.5)
        return confidence * (0.5 + 0.5 * file_scope_score(entry.get("scope"), files))
    scope = entry.get("scope") or "repo"
    if scope == "repo":
        return 0.5
    return file_scope_score(scope, files)
