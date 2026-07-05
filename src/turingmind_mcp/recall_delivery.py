"""Python recall_bundle delivery — mirrors hooks/scripts/lib/bundle.js for non-Cursor IDEs."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("turingmind-mcp.recall-delivery")

RECALLED_MD = "recalled.md"
RECALLED_INDEX = "recalled-index.json"
SESSION_META = "session.json"
MAX_CONTENT_LEN = 2000
EMPTY_INDEX: Dict[str, Any] = {
    "memory_ids": [],
    "rules": [],
    "patterns": [],
    "queue_top": [],
}


def turingmind_dir(workspace_root: Path) -> Path:
    return workspace_root / ".turingmind"


def load_session_meta(workspace_root: Path) -> Dict[str, Any]:
    file_path = turingmind_dir(workspace_root) / SESSION_META
    try:
        if file_path.exists():
            return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_session_meta(workspace_root: Path, meta: Dict[str, Any]) -> None:
    try:
        directory = turingmind_dir(workspace_root)
        directory.mkdir(parents=True, exist_ok=True)
        payload = {**meta, "updated_at": datetime.now(timezone.utc).isoformat()}
        (directory / SESSION_META).write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def update_session_meta_from_sync(workspace_root: Path, sync_response: Dict[str, Any]) -> None:
    session = sync_response.get("session") or {}
    if not session:
        return
    prev = load_session_meta(workspace_root)
    save_session_meta(
        workspace_root,
        {
            "session_id": session.get("session_id") or prev.get("session_id"),
            "composer_id": session.get("composer_id") or prev.get("composer_id"),
            "repo": session.get("repo") or prev.get("repo"),
            "branch": session.get("branch") or prev.get("branch"),
        },
    )


def reset_index_if_composer_changed(
    workspace_root: Path,
    sync_response: Dict[str, Any],
    log_fn: Optional[Callable[[str, str], None]] = None,
) -> bool:
    """Clear recalled-index.json when composer_id changes (prevents bleed)."""
    session = sync_response.get("session") or {}
    new_composer = session.get("composer_id")
    if not new_composer:
        return False
    prev = load_session_meta(workspace_root)
    old_composer = prev.get("composer_id")
    if old_composer and old_composer != new_composer:
        save_recalled_index(workspace_root, dict(EMPTY_INDEX))
        if log_fn:
            log_fn("INFO", "Reset recalled-index.json for new composer session")
        return True
    return False


def parse_recall_bundle(raw: Any) -> Optional[Dict[str, Any]]:
    if not raw or not isinstance(raw, dict):
        return None
    try:
        explicit_rules = raw.get("explicit_rules") if isinstance(raw.get("explicit_rules"), list) else []
        learned_patterns = raw.get("learned_patterns") if isinstance(raw.get("learned_patterns"), list) else []
        queue_top = raw.get("queue_top") if isinstance(raw.get("queue_top"), list) else []
        policy = raw.get("policy") if isinstance(raw.get("policy"), dict) else {"hydrate_required": False}

        for item in [*explicit_rules, *learned_patterns]:
            if not item or not isinstance(item, dict):
                return None
            content = item.get("content")
            if isinstance(content, str) and len(content) > MAX_CONTENT_LEN:
                return None

        return {
            "explicit_rules": explicit_rules,
            "learned_patterns": learned_patterns,
            "queue_top": queue_top,
            "policy": policy,
        }
    except Exception:
        return None


def should_write_context_file(sync_response: Dict[str, Any]) -> bool:
    delivery = sync_response.get("delivery") or {}
    if delivery.get("is_delta") is True:
        return True
    delta = sync_response.get("bundle_delta") or {}
    if delta.get("unchanged") is False:
        return True
    policy = (sync_response.get("recall_bundle") or {}).get("policy") or {}
    if policy.get("hydrate_required") and policy.get("code"):
        return True
    return False


def load_recalled_index(workspace_root: Path) -> Dict[str, Any]:
    file_path = turingmind_dir(workspace_root) / RECALLED_INDEX
    try:
        if file_path.exists():
            return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return dict(EMPTY_INDEX)


def save_recalled_index(workspace_root: Path, index: Dict[str, Any]) -> None:
    directory = turingmind_dir(workspace_root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / RECALLED_INDEX).write_text(json.dumps(index, indent=2), encoding="utf-8")


def merge_delta_into_index(
    index: Dict[str, Any],
    bundle: Dict[str, Any],
    bundle_delta: Dict[str, Any],
) -> Dict[str, Any]:
    seen = set(index.get("memory_ids") or [])
    added = set(bundle_delta.get("added_rule_ids") or [])

    for rule in bundle.get("explicit_rules") or []:
        memory_id = rule.get("memory_id")
        if not memory_id or memory_id not in added or memory_id in seen:
            continue
        seen.add(memory_id)
        index["rules"] = [r for r in (index.get("rules") or []) if r.get("memory_id") != memory_id]
        index.setdefault("rules", []).append(
            {
                "memory_id": memory_id,
                "type": "explicit_rule",
                "content": rule.get("content"),
                "scope": rule.get("scope"),
                "score": rule.get("score"),
            }
        )

    for pattern in bundle.get("learned_patterns") or []:
        memory_id = pattern.get("memory_id")
        if not memory_id or memory_id not in added or memory_id in seen:
            continue
        seen.add(memory_id)
        index["patterns"] = [
            p for p in (index.get("patterns") or []) if p.get("memory_id") != memory_id
        ]
        index.setdefault("patterns", []).append(
            {
                "memory_id": memory_id,
                "type": "learned_pattern",
                "content": pattern.get("content"),
                "scope": pattern.get("scope"),
                "score": pattern.get("score"),
            }
        )

    index["memory_ids"] = list(seen)
    if isinstance(bundle.get("queue_top"), list):
        index["queue_top"] = bundle["queue_top"]
    index["last_updated"] = datetime.now(timezone.utc).isoformat()
    return index


def render_recalled_markdown(sync_response: Dict[str, Any], index: Dict[str, Any]) -> str:
    session = sync_response.get("session") or {}
    policy = (sync_response.get("recall_bundle") or {}).get("policy") or {}
    lines = [
        "<!-- TuringMind auto-generated — do not edit manually -->",
        "# TuringMind active recall",
        "",
        f"> Updated: {index.get('last_updated') or datetime.now(timezone.utc).isoformat()}",
        f"> Session: {session.get('session_id') or 'unknown'} · "
        f"Repo: {session.get('repo') or sync_response.get('repo') or 'unknown'}",
        "",
        "Agents: honor explicit rules below before editing touched scopes.",
        "",
    ]

    if policy.get("code") or policy.get("message"):
        lines.extend(["## Policy", ""])
        if policy.get("code"):
            message = policy.get("message") or "Review before continuing."
            lines.append(f"- **{policy['code']}**: {message}")
        lines.append("")

    lines.extend(["## Explicit rules", ""])
    rules = index.get("rules") or []
    if not rules:
        lines.extend(["_No explicit rules loaded yet._", ""])
    else:
        for rule in rules:
            score_suffix = f" (score {rule['score']})" if rule.get("score") is not None else ""
            lines.append(f"### `{rule.get('scope')}`{score_suffix}")
            lines.append(str(rule.get("content") or ""))
            lines.append("")

    lines.extend(["## Learned patterns", ""])
    patterns = index.get("patterns") or []
    if not patterns:
        lines.extend(["_No learned patterns loaded yet._", ""])
    else:
        for pattern in patterns:
            score_suffix = f" (score {pattern['score']})" if pattern.get("score") is not None else ""
            lines.append(f"### `{pattern.get('scope')}`{score_suffix}")
            lines.append(str(pattern.get("content") or ""))
            lines.append("")

    queue = index.get("queue_top") or []
    if queue:
        lines.extend(["## Decision queue (top)", ""])
        for item in queue:
            severity = item.get("severity") or "medium"
            action = item.get("action") or item.get("gap_type") or "Review finding"
            lines.append(f"- **[{severity}]** {action}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def ensure_gitignore_entry(
    workspace_root: Path,
    log_fn: Optional[Callable[[str, str], None]] = None,
) -> None:
    gitignore_path = workspace_root / ".gitignore"
    entry = ".turingmind/"
    try:
        content = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
        if ".turingmind/" in content or ".turingmind" in content:
            return
        prefix = "\n" if content and not content.endswith("\n") else ""
        gitignore_path.write_text(
            content + f"{prefix}# TuringMind local state (hooks, recall context)\n{entry}\n",
            encoding="utf-8",
        )
        if log_fn:
            log_fn("INFO", "Added .turingmind/ to .gitignore")
    except Exception as exc:
        if log_fn:
            log_fn("WARN", f"Could not update .gitignore: {exc}")


def apply_recall_delivery(
    workspace_root: Path | str,
    sync_response: Dict[str, Any],
    log_fn: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    """Apply recall_bundle delivery: merge delta into index and write recalled.md."""
    root = Path(workspace_root).resolve()
    try:
        reset_index_if_composer_changed(root, sync_response, log_fn)
        update_session_meta_from_sync(root, sync_response)

        if not should_write_context_file(sync_response):
            return {"written": False, "reason": "unchanged"}

        bundle = parse_recall_bundle(sync_response.get("recall_bundle"))
        if not bundle:
            if log_fn:
                log_fn("WARN", "BUNDLE_SCHEMA_FAIL: skipping context file write")
            return {"written": False, "reason": "invalid_bundle"}

        index = load_recalled_index(root)
        index = merge_delta_into_index(index, bundle, sync_response.get("bundle_delta") or {})

        markdown = render_recalled_markdown(sync_response, index)
        directory = turingmind_dir(root)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / RECALLED_MD).write_text(markdown, encoding="utf-8")
        save_recalled_index(root, index)
        ensure_gitignore_entry(root, log_fn)

        added = sync_response.get("bundle_delta", {}).get("added_rule_ids") or []
        if log_fn:
            log_fn(
                "INFO",
                f"Wrote {RECALLED_MD} ({len(added)} new memory id(s), "
                f"{len(index.get('rules') or [])} rule(s) cumulative)",
            )
        return {"written": True, "path": str(directory / RECALLED_MD)}
    except Exception as exc:
        if log_fn:
            log_fn("WARN", f"Context file delivery failed: {exc}")
        logger.warning("Context file delivery failed: %s", exc)
        return {"written": False, "reason": "error"}
