"""Git workspace context for branch-aware memory capture (Phase 4.1)."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

HEAD_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DETACHED_BRANCH_LABEL = "HEAD"


@dataclass(frozen=True)
class GitContext:
    """Snapshot of git state at capture time."""

    branch: Optional[str]
    head: Optional[str]
    dirty: bool
    default_branch: Optional[str] = None
    detached: bool = False


def branch_memory_ranking_enabled() -> bool:
    """When false, store git fields but recall ranking stays legacy (Phase 4.2)."""
    import os

    flag = os.environ.get("TURINGMIND_BRANCH_MEMORY", "").strip().lower()
    return flag in ("1", "true", "yes")


def _run_git(workspace: Path, *args: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("git command failed: %s", exc)
        return None


def _branch_exists(workspace: Path, name: str) -> bool:
    ref = _run_git(workspace, "show-ref", "--verify", f"refs/heads/{name}")
    return ref is not None


def resolve_default_branch(workspace: Path) -> Optional[str]:
    """SPEC-BR-E13 / nuance B: origin/HEAD → main → master → current branch."""
    origin_head = _run_git(workspace, "symbolic-ref", "refs/remotes/origin/HEAD")
    if origin_head:
        prefix = "refs/remotes/origin/"
        if origin_head.startswith(prefix):
            return origin_head[len(prefix) :]

    for candidate in ("main", "master"):
        if _branch_exists(workspace, candidate):
            return candidate

    current = _run_git(workspace, "rev-parse", "--abbrev-ref", "HEAD")
    if current and current != DETACHED_BRANCH_LABEL:
        return current
    return None


def collect_git_context(workspace: Optional[Path] = None) -> Optional[GitContext]:
    """Collect branch, HEAD, dirty flag from a git workspace."""
    if workspace is None:
        import os

        env = os.environ.get("TURINGMIND_WORKSPACE_DIR")
        workspace = Path(env) if env else Path.cwd()

    workspace = workspace.resolve()
    if not (workspace / ".git").exists():
        return None

    head = _run_git(workspace, "rev-parse", "HEAD")
    if not head:
        return None

    abbrev = _run_git(workspace, "rev-parse", "--abbrev-ref", "HEAD")
    detached = abbrev == DETACHED_BRANCH_LABEL or abbrev is None
    branch = DETACHED_BRANCH_LABEL if detached else abbrev

    porcelain = _run_git(workspace, "status", "--porcelain")
    dirty = bool(porcelain)

    return GitContext(
        branch=branch,
        head=head,
        dirty=dirty,
        default_branch=resolve_default_branch(workspace),
        detached=detached,
    )


def validate_head_sha(head: Optional[str]) -> None:
    """Raise ValueError when head is present but not a full SHA."""
    if head is None:
        return
    if not HEAD_SHA_RE.match(head.lower()):
        raise ValueError("git.head must be a 40-character hex SHA")


def validate_branch_name(branch: Optional[str]) -> None:
    """Raise ValueError on empty or overlong branch names."""
    if branch is None:
        return
    if not branch.strip():
        raise ValueError("git.branch must not be empty")
    if len(branch) > 255:
        raise ValueError("git.branch exceeds 255 characters")


def derive_scope_tier(
    branch: Optional[str],
    git_dirty: bool,
) -> str:
    """SPEC-BR-08: derive scope tier from branch + dirty (not client override)."""
    if branch is None:
        return "repo"
    if git_dirty:
        return "working_tree"
    return "branch"


def normalize_scope_tier_write(
    branch: Optional[str],
    git_dirty: bool,
    scope_tier: Optional[str] = None,
) -> str:
    """Return consistent scope_tier; reject inconsistent client values."""
    derived = derive_scope_tier(branch, git_dirty)
    if scope_tier is None:
        return derived
    tier = scope_tier.strip().lower()
    if tier not in ("repo", "branch", "working_tree"):
        raise ValueError(f"invalid scope_tier: {scope_tier!r}")
    if tier == "working_tree" and not git_dirty:
        raise ValueError("scope_tier=working_tree requires git_dirty=1")
    if tier == "repo" and branch is not None:
        raise ValueError("scope_tier=repo requires branch to be omitted")
    if tier == "branch" and (branch is None or git_dirty):
        raise ValueError("scope_tier=branch requires branch and git_dirty=0")
    return tier


def git_context_from_payload(data: Optional[Dict[str, Any]]) -> Optional[GitContext]:
    """Parse API/hook git blob; raises ValueError on invalid fields."""
    if not data:
        return None
    branch = data.get("branch")
    head = data.get("head")
    dirty = bool(data.get("dirty", False))
    default_branch = data.get("default_branch")
    validate_branch_name(branch if branch is None else str(branch))
    validate_head_sha(head if head is None else str(head))
    detached = branch == DETACHED_BRANCH_LABEL
    return GitContext(
        branch=str(branch) if branch is not None else None,
        head=str(head) if head is not None else None,
        dirty=dirty,
        default_branch=str(default_branch) if default_branch else None,
        detached=detached,
    )


def git_fields_for_storage(ctx: Optional[GitContext]) -> Dict[str, Any]:
    """Map GitContext to database column values."""
    if ctx is None:
        return {
            "branch": None,
            "head_sha": None,
            "git_dirty": 0,
            "scope_tier": "repo",
        }
    return {
        "branch": ctx.branch,
        "head_sha": ctx.head,
        "git_dirty": 1 if ctx.dirty else 0,
        "scope_tier": derive_scope_tier(ctx.branch, ctx.dirty),
    }
