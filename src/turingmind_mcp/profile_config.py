"""Install profile: memory (default GA SKU) vs governed (SPDD upsell)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional

PROFILE_MEMORY = "memory"
PROFILE_GOVERNED = "governed"
VALID_PROFILES = frozenset({PROFILE_MEMORY, PROFILE_GOVERNED})

# Graph / SPDD gap types excluded from memory-scoped decision queue.
GRAPH_GAP_TYPES = frozenset(
    {
        "missing_boundary_edge",
        "empty_api_contract",
        "orphan_node",
        "unclassified_failure",
        "ungoverned_files",
    }
)

# Reconcile + memory-facing finding types included in memory scope.
MEMORY_QUEUE_GAP_TYPES = frozenset(
    {
        "promotion_candidate",
        "memory_conflict",
        "semantic_duplicate",
        "duplicate_merge",
        "revert_penalty",
        "stale_memory",
        "invalidation_decay",
        "scope_churn",
        "branch_promotion",
        "archive_branch_memories",
    }
)

MEMORY_PROFILE_GROUPS = "login,code_intelligence"
GOVERNED_PROFILE_GROUPS = "login,code_intelligence,v2_engine"

_ENV_FILE = Path.home() / ".turingmind" / "env"


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from ~/.turingmind/env (no export prefix)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def get_profile() -> str:
    """Return active profile. Defaults to governed for backward compatibility."""
    raw = os.environ.get("TURINGMIND_PROFILE", "").strip().lower()
    if not raw and _ENV_FILE.is_file():
        raw = _parse_env_file(_ENV_FILE).get("TURINGMIND_PROFILE", "").strip().lower()
    if raw in VALID_PROFILES:
        return raw
    return PROFILE_GOVERNED


def is_memory_profile() -> bool:
    return get_profile() == PROFILE_MEMORY


def default_tool_groups_for_profile(profile: Optional[str] = None) -> str:
    p = (profile or get_profile()).lower()
    if p == PROFILE_MEMORY:
        return MEMORY_PROFILE_GROUPS
    return GOVERNED_PROFILE_GROUPS


def filter_decision_queue_gaps(
    gaps: Iterable[dict],
    *,
    scope: Optional[str] = None,
) -> List[dict]:
    """Filter queue items for memory profile or explicit scope=memory."""
    use_memory_scope = scope == PROFILE_MEMORY or (
        scope is None and is_memory_profile()
    )
    if not use_memory_scope:
        return list(gaps)
    filtered: List[dict] = []
    for gap in gaps:
        gap_type = gap.get("gap_type") or gap.get("finding_type") or ""
        if gap_type in GRAPH_GAP_TYPES:
            continue
        if gap_type in MEMORY_QUEUE_GAP_TYPES:
            filtered.append(gap)
        elif gap.get("memory_id"):
            # Reconcile findings with memory_id but novel finding_type
            filtered.append(gap)
    return filtered


def write_profile_env(
    profile: str,
    *,
    mcp_python: Optional[str] = None,
    local_api_url: str = "http://127.0.0.1:8477",
) -> Path:
    """Write ~/.turingmind/env for installer (idempotent merge)."""
    if profile not in VALID_PROFILES:
        raise ValueError(f"Invalid profile: {profile}")
    _ENV_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = _parse_env_file(_ENV_FILE) if _ENV_FILE.is_file() else {}
    existing["TURINGMIND_PROFILE"] = profile
    existing["TURINGMIND_LOCAL_API_URL"] = local_api_url
    existing["TURINGMIND_ENABLED_TOOL_GROUPS"] = default_tool_groups_for_profile(profile)
    if mcp_python:
        existing["TURINGMIND_MCP_PYTHON"] = mcp_python
    lines = [f'{k}="{v}"' for k, v in sorted(existing.items())]
    _ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _ENV_FILE.chmod(0o600)
    return _ENV_FILE
