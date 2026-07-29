"""Multi-root workspace → linked repo union (workspace_id).

Patterns stay stored per owner/repo. Grounding/queue/list can union linked
repos for ASG-style Cursor windows. Working-set cards may use
``workspace/<workspace_id>`` as a synthetic repo key.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_ENV_FILE = Path.home() / ".turingmind" / "env"
_WORKSPACES_FILE = Path.home() / ".turingmind" / "workspaces.json"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass
class WorkspaceContext:
    workspace_id: Optional[str] = None
    repos: List[str] = field(default_factory=list)
    primary_repo: Optional[str] = None

    @property
    def working_set_repo(self) -> Optional[str]:
        if self.workspace_id:
            return f"workspace/{self.workspace_id}"
        return self.primary_repo


def _parse_env_file(path: Path) -> dict[str, str]:
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


def normalize_repo(repo: str) -> Optional[str]:
    text = (repo or "").strip()
    if not text:
        return None
    if text.startswith("workspace/"):
        return text
    if _REPO_RE.match(text):
        return text
    return None


def _load_workspaces_file() -> Dict[str, Any]:
    path = Path(os.environ.get("TURINGMIND_WORKSPACES_FILE", str(_WORKSPACES_FILE)))
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read workspaces file %s: %s", path, exc)
        return {}
    if isinstance(data, dict) and "workspaces" in data and isinstance(data["workspaces"], dict):
        return data["workspaces"]
    return data if isinstance(data, dict) else {}


def _repos_from_env() -> List[str]:
    raw = os.environ.get("TURINGMIND_WORKSPACE_REPOS", "").strip()
    if not raw and _ENV_FILE.is_file():
        raw = _parse_env_file(_ENV_FILE).get("TURINGMIND_WORKSPACE_REPOS", "").strip()
    return [r for r in (normalize_repo(x) for x in raw.split(",")) if r]


def _workspace_id_from_env() -> Optional[str]:
    raw = os.environ.get("TURINGMIND_WORKSPACE_ID", "").strip()
    if not raw and _ENV_FILE.is_file():
        raw = _parse_env_file(_ENV_FILE).get("TURINGMIND_WORKSPACE_ID", "").strip()
    return raw or None


def lookup_workspace(workspace_id: str) -> List[str]:
    """Return linked repos for a configured workspace_id."""
    wid = (workspace_id or "").strip()
    if not wid:
        return []
    table = _load_workspaces_file()
    entry = table.get(wid)
    if isinstance(entry, list):
        return [r for r in (normalize_repo(x) for x in entry) if r]
    if isinstance(entry, dict):
        repos = entry.get("repos") or entry.get("repositories") or []
        return [r for r in (normalize_repo(x) for x in repos) if r]
    return []


def derive_workspace_id(repos: Iterable[str]) -> Optional[str]:
    """Stable id from sorted linked repos when none configured."""
    import hashlib

    cleaned = sorted({r for r in (normalize_repo(x) or "" for x in repos) if r and not r.startswith("workspace/")})
    if len(cleaned) < 2:
        return None
    digest = hashlib.sha256("|".join(cleaned).encode()).hexdigest()[:12]
    return f"ws-{digest}"


def resolve_workspace(
    *,
    workspace_id: Optional[str] = None,
    repos: Optional[Iterable[str]] = None,
    primary_repo: Optional[str] = None,
) -> WorkspaceContext:
    """Resolve workspace context from id, explicit repos, and/or env defaults."""
    wid = (workspace_id or "").strip() or _workspace_id_from_env()
    linked: List[str] = []

    explicit = [r for r in (normalize_repo(x) for x in (repos or [])) if r]
    if explicit:
        linked.extend(explicit)

    if wid:
        linked.extend(lookup_workspace(wid))

    if not linked:
        linked.extend(_repos_from_env())

    # Dedup preserving order
    seen = set()
    ordered: List[str] = []
    for r in linked:
        if r in seen:
            continue
        seen.add(r)
        ordered.append(r)

    primary = normalize_repo(primary_repo or "") or None
    if primary and primary not in seen and not primary.startswith("workspace/"):
        ordered.insert(0, primary)
        seen.add(primary)
    if not primary and ordered:
        primary = next((r for r in ordered if not r.startswith("workspace/")), ordered[0])

    if not wid and len([r for r in ordered if not r.startswith("workspace/")]) >= 2:
        wid = derive_workspace_id(ordered)

    return WorkspaceContext(workspace_id=wid, repos=ordered, primary_repo=primary)


def merge_entry_lists(
    batches: Iterable[List[Dict[str, Any]]],
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    """Dedupe memory entries by memory_id then content; keep highest confidence."""
    by_id: Dict[str, Dict[str, Any]] = {}
    by_content: Dict[str, Dict[str, Any]] = {}
    for batch in batches:
        for entry in batch:
            mid = entry.get("memory_id")
            if mid:
                prev = by_id.get(mid)
                if not prev or (entry.get("confidence") or 0) > (prev.get("confidence") or 0):
                    by_id[mid] = entry
                continue
            key = (entry.get("content") or "").strip()
            if not key:
                continue
            prev = by_content.get(key)
            if not prev or (entry.get("confidence") or 0) > (prev.get("confidence") or 0):
                by_content[key] = entry

    merged = list(by_id.values()) + [
        e for c, e in by_content.items() if e.get("memory_id") not in by_id
    ]
    merged.sort(key=lambda e: (-(e.get("confidence") or 0), e.get("type") or ""))
    return merged[:limit]
