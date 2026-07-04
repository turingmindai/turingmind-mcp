"""Remote memory cloud sync via authenticated repochatindex API."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


def use_cloud_sync(api_url: str, api_key: str) -> bool:
    """Prefer cloud API when flagged and keys are present."""
    flag = os.getenv("TURINGMIND_CLOUD_SYNC", "").strip().lower()
    if flag in ("0", "false", "no"):
        return False
    return bool(api_url and api_key)


def resolve_sync_branch(explicit: Optional[str] = None) -> Optional[str]:
    """Resolve git branch for cloud pull filter (Phase 4.5 / SPEC-BR-05)."""
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    env_branch = os.environ.get("TURINGMIND_SYNC_BRANCH", "").strip()
    if env_branch:
        return env_branch
    try:
        from .git_context import collect_git_context

        ctx = collect_git_context()
        if ctx and ctx.branch and ctx.branch != "HEAD":
            return ctx.branch
    except Exception as exc:
        logger.debug("Could not infer sync branch: %s", exc)
    return None


def _build_sync_payload(
    db: Any,
    repo: str,
    *,
    since: Optional[str],
    branch: Optional[str],
    push: bool,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "repo": repo,
        "since": since,
        "push": _serialize_push_entries(db, repo) if push else [],
    }
    sync_branch = resolve_sync_branch(branch)
    if sync_branch:
        payload["branch"] = sync_branch
    return payload


def _serialize_push_entries(db: Any, repo: str) -> List[Dict[str, Any]]:
    entries = [
        e for e in db.list_memory_entries_for_cloud_sync(repo=repo)
        if e.get("status") in ("active", "candidate", "deprecated")
    ]
    push: List[Dict[str, Any]] = []
    for entry in entries:
        row = dict(entry)
        tags = row.get("security_tags")
        if isinstance(tags, str):
            try:
                import json
                row["security_tags"] = json.loads(tags)
            except Exception:
                row["security_tags"] = None
        push.append(row)
    return push


async def _post_cloud_sync(
    payload: Dict[str, Any],
    *,
    api_url: str,
    api_key: str,
    timeout: float,
) -> Dict[str, Any]:
    url = f"{api_url.rstrip('/')}/api/v2/memory/cloud/sync"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload, headers=headers)

    if response.status_code == 403:
        raise PermissionError("Cloud memory sync denied — check repo access and API key permissions.")
    if response.status_code == 401:
        raise PermissionError("Cloud memory sync unauthorized — renew TURINGMIND_API_KEY.")
    if response.status_code >= 400:
        detail = response.text[:500]
        raise RuntimeError(f"Cloud memory sync failed ({response.status_code}): {detail}")
    return response.json()


def _merge_pull_stats(db: Any, repo: str, body: Dict[str, Any]) -> Dict[str, Any]:
    pulled = body.get("pulled") or []
    merge_stats = db.apply_cloud_memory_rows(repo, pulled) if pulled else {
        "memories_applied": 0,
        "tombstones_applied": 0,
    }
    last_pull = body.get("last_cloud_pull_at")
    if last_pull:
        db.set_repo_sync_state(repo, last_cloud_pull_at=last_pull)
    return {
        "memories_pulled": body.get("memories_pulled", len(pulled)),
        "memories_applied": merge_stats.get("memories_applied", 0),
        "tombstones_applied": merge_stats.get("tombstones_applied", 0),
        "memories_pushed": body.get("memories_pushed", 0),
        "cloud_repo_key": body.get("repo_key"),
        "sync_branch": body.get("branch") or resolve_sync_branch(),
    }


async def sync_memories_via_cloud_api(
    db: Any,
    repo: str,
    *,
    api_url: str,
    api_key: str,
    branch: Optional[str] = None,
    timeout: float = 60.0,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Pull/push through repochat sidecar (Mongo). Applies pulled rows locally and updates cursor.

    Returns:
        (stats_dict, warning_or_none)
    """
    sync_state = db.get_repo_sync_state(repo)
    payload = _build_sync_payload(
        db,
        repo,
        since=sync_state.get("last_cloud_pull_at"),
        branch=branch,
        push=True,
    )
    body = await _post_cloud_sync(payload, api_url=api_url, api_key=api_key, timeout=timeout)
    return _merge_pull_stats(db, repo, body), None


async def pull_memories_via_cloud_api(
    db: Any,
    repo: str,
    *,
    api_url: str,
    api_key: str,
    branch: Optional[str] = None,
    timeout: float = 60.0,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Pull-only cloud sync: merge remote rows locally without pushing."""
    sync_state = db.get_repo_sync_state(repo)
    payload = _build_sync_payload(
        db,
        repo,
        since=sync_state.get("last_cloud_pull_at"),
        branch=branch,
        push=False,
    )
    body = await _post_cloud_sync(payload, api_url=api_url, api_key=api_key, timeout=timeout)
    stats = _merge_pull_stats(db, repo, body)
    stats["via"] = "cloud"
    return stats, None


EMPTY_MEMORY_PULL_STATS: Dict[str, Any] = {
    "memories_pulled": 0,
    "memories_applied": 0,
    "tombstones_applied": 0,
    "memories_pushed": 0,
    "via": "none",
}
