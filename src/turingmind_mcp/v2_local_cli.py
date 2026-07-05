"""Local V2 REST API helpers for CLI, git hooks, and tooling."""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


def load_install_env_file() -> None:
    """Load ``~/.turingmind/env`` into os.environ (does not override existing)."""
    env_path = Path.home() / ".turingmind" / "env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_local_api_url() -> str:
    return os.environ.get("TURINGMIND_LOCAL_API_URL", "http://127.0.0.1:8477").rstrip("/")


def get_profile() -> str:
    """Return memory or governed (defaults to governed)."""
    from .profile_config import get_profile as _get

    return _get()


def resolve_default_repo() -> str:
    env = os.environ.get("TURINGMIND_DEFAULT_REPO", "").strip()
    if env:
        return env
    try:
        url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        match = re.search(r"[:/]([^/:]+/[^/]+?)(\.git)?$", url)
        if match:
            return match.group(1)
    except Exception:
        pass
    return "local/workspace"


def api_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{get_local_api_url()}{path}"
    if params:
        filtered = {k: v for k, v in params.items() if v is not None}
        if filtered:
            url += "?" + urllib.parse.urlencode(filtered)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def fetch_decision_queue(
    repo: str,
    *,
    scope: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"repo": repo, "limit": limit}
    if scope:
        params["scope"] = scope
    elif get_profile() == "memory":
        params["scope"] = "memory"
    return api_get("/api/v2/decision-queue", params)


def format_queue_markdown(data: Dict[str, Any]) -> str:
    """Agent-readable queue listing (markdown)."""
    items = data.get("queue", [])
    total = data.get("total", len(items))
    if total == 0:
        return "## Decision Queue\n✅ No gaps. Graph is healthy."

    lines = [f"## Decision Queue — {total} item(s)\n"]
    for index, item in enumerate(items, 1):
        sev = str(item.get("severity", "unknown")).upper()
        gap = item.get("gap_type", "unknown")
        node = item.get("node_id")
        action = item.get("action") or item.get("suggested_action", "")
        lines.append(f"### {index}. [{sev}] `{gap}`")
        if node:
            lines.append(f"- **Node:** `{node}`")
        if item.get("finding_id"):
            lines.append(f"- **Finding:** `{item['finding_id']}`")
        if action:
            lines.append(f"- **Action:** {action}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_queue_pop_markdown(data: Dict[str, Any]) -> str:
    items = data.get("queue", [])
    if not items:
        return "## Next Action\n✅ Nothing to do. Graph is healthy."

    item = items[0]
    sev = str(item.get("severity", "unknown")).upper()
    gap = item.get("gap_type", "unknown")
    node = item.get("node_id", "?")
    action = item.get("action") or item.get("suggested_action", "")
    lines = [
        f"## Next Action: `{gap}` ({sev})",
        f"- **Node:** `{node}`",
    ]
    if action:
        lines.append(f"- **Fix:** {action}")
    if len(items) > 1:
        lines.append(f"\n*{len(items) - 1} more item(s) in queue*")
    return "\n".join(lines) + "\n"


def queue_has_severity(data: Dict[str, Any], severity: str) -> bool:
    target = severity.lower()
    return any(
        str(item.get("severity", "")).lower() == target
        for item in data.get("queue", [])
    )


def evaluate_pre_push(data: Dict[str, Any], profile: str) -> tuple[int, str]:
    """Return (exit_code, formatted_output) for git pre-push."""
    output = format_queue_markdown(data)
    if profile == "memory":
        if queue_has_severity(data, "critical") or queue_has_severity(data, "high"):
            return 0, output
        return 0, output

    if queue_has_severity(data, "critical"):
        return 1, output
    return 0, output
