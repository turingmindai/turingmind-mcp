"""Git-aware file churn detection for invalidation decay (Pass 3).

Reads ``git log`` / ``git diff`` from the workspace so memories invalidate when
files change outside editor buffers (CLI edits, merges, rebases).
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitChurnSnapshot:
    """Paths touched since ``since_ref`` and the current HEAD."""

    head: str
    modified: frozenset[str]
    deleted: frozenset[str]

    @property
    def all_touched(self) -> Set[str]:
        return set(self.modified) | set(self.deleted)


def _normalize_repo_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


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
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("git command failed: %s", exc)
        return None


def resolve_git_workspace(explicit: Optional[Path] = None) -> Path:
    """Return workspace root for git operations."""
    if explicit is not None:
        return explicit
    import os

    env = os.environ.get("TURINGMIND_WORKSPACE_DIR")
    if env:
        return Path(env)
    return Path.cwd()


def collect_git_churn(
    workspace: Optional[Path] = None,
    since_ref: Optional[str] = None,
    *,
    max_commits: int = 50,
) -> Optional[GitChurnSnapshot]:
    """Collect modified/deleted paths since ``since_ref`` (or recent commits).

    Returns None when the workspace is not a git repository.
    """
    root = resolve_git_workspace(workspace)
    head = _run_git(root, "rev-parse", "HEAD")
    if not head:
        return None

    modified: Set[str] = set()
    deleted: Set[str] = set()

    if since_ref and since_ref == head:
        return GitChurnSnapshot(head=head, modified=frozenset(), deleted=frozenset())

    if since_ref and since_ref != head:
        log_range = f"{since_ref}..{head}"
        raw = _run_git(root, "diff", "--name-status", log_range)
    else:
        # Bootstrap: record HEAD only — do not retroactively penalize from history.
        return GitChurnSnapshot(head=head, modified=frozenset(), deleted=frozenset())

    if raw is None:
        return GitChurnSnapshot(head=head, modified=frozenset(), deleted=frozenset())

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0].strip()
        if status.startswith("R") and len(parts) >= 3:
            deleted.add(_normalize_repo_path(parts[1]))
            modified.add(_normalize_repo_path(parts[2]))
            continue
        path = _normalize_repo_path(parts[1])
        if not path:
            continue
        if status.startswith("D"):
            deleted.add(path)
        elif status.startswith(("M", "A", "C", "T")):
            modified.add(path)

    return GitChurnSnapshot(
        head=head,
        modified=frozenset(modified),
        deleted=frozenset(deleted),
    )


def count_scope_git_hits(scope: str, snapshot: GitChurnSnapshot) -> int:
    """Count git-touched paths that overlap a memory scope."""
    if not scope or scope == "repo":
        return 0

    scope_n = _normalize_repo_path(scope)
    hits = 0
    for path in snapshot.all_touched:
        path_n = _normalize_repo_path(path)
        if scope_n == path_n:
            hits += 1
        elif path_n.endswith("/" + scope_n) or scope_n.endswith("/" + path_n):
            hits += 1
        elif scope_n in path_n or path_n in scope_n:
            hits += 1
    return hits
