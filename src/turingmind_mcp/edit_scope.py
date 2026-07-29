"""IDE-agnostic turn-scoped edit contracts.

Declare / get / check path scope for a repo (+ optional conversation).
Hosts (Cursor hooks, Antigravity watcher, Codex) call the HTTP/MCP surface;
agents can also declare via ``turingmind_declare_edit_scope``.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_PATH_RE = re.compile(
    r"(?:^|[\s`\"'(])"
    r"((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+|"
    r"(?:src|lib|app|apps|packages|plugins|tests?|docs|hooks|scripts)/[\w./-]+)"
)

VALID_STATUSES = frozenset(
    {"undeclared", "seeded", "provisional", "declared", "amended"}
)


def normalize_rel(file_path: str) -> str:
    text = (file_path or "").replace("\\", "/").lstrip("./").lstrip("/")
    return text.strip()


def module_key(file_path: str) -> str:
    parts = [p for p in normalize_rel(file_path).split("/") if p]
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0] if parts else ""


def extract_paths_from_text(text: str) -> List[str]:
    found: List[str] = []
    seen = set()
    for match in _PATH_RE.finditer(text or ""):
        path = normalize_rel(match.group(1).rstrip(".,;:!?)"))
        if path and path not in seen and len(path) < 200:
            seen.add(path)
            found.append(path)
    return found


def prefixes_from_paths(paths: Iterable[str]) -> Dict[str, List[str]]:
    prefixes: set[str] = set()
    modules: set[str] = set()
    files: set[str] = set()
    for raw in paths or []:
        path = normalize_rel(str(raw))
        if not path:
            continue
        if re.search(r"\.[A-Za-z0-9]+$", path):
            files.add(path)
            if "/" in path:
                prefixes.add(path.rsplit("/", 1)[0] + "/")
        else:
            prefixes.add(path if path.endswith("/") else f"{path}/")
        mod = module_key(path)
        if mod:
            modules.add(mod)
    return {
        "prefixes": sorted(prefixes),
        "modules": sorted(modules),
        "files": sorted(files),
    }


def path_in_scope(file_path: str, scope: Dict[str, Any]) -> bool:
    path = normalize_rel(file_path)
    if not path:
        return True
    files = [normalize_rel(f) for f in (scope.get("files") or [])]
    if path in files:
        return True
    for pre in scope.get("prefixes") or []:
        n = normalize_rel(str(pre))
        with_slash = n if n.endswith("/") else f"{n}/"
        if path == n or path.startswith(with_slash) or path.startswith(n):
            return True
    mod = module_key(path)
    if mod in (scope.get("modules") or []):
        return True
    if not files and not (scope.get("prefixes") or []) and not (scope.get("modules") or []):
        return True
    return False


def has_path_contract(scope: Optional[Dict[str, Any]]) -> bool:
    if not scope:
        return False
    return bool(
        scope.get("prefixes") or scope.get("files") or scope.get("modules")
    )


def is_wide_cluster(
    *,
    cluster_type: Optional[str],
    file_count: int,
) -> bool:
    ctype = (cluster_type or "").strip()
    if ctype in ("cross_module", "refactor_burst"):
        return True
    if ctype == "development" and file_count >= 5:
        return True
    return False


def check_scope_breach(
    scope: Optional[Dict[str, Any]],
    file_list: Sequence[str],
    *,
    cluster_type: Optional[str] = None,
    cluster_severity: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return a breach payload or None when the cluster is in contract."""
    files = [normalize_rel(f) for f in file_list if f]
    if not files:
        return None

    wide = is_wide_cluster(cluster_type=cluster_type, file_count=len(files))
    if not has_path_contract(scope):
        if not wide:
            return None
        return {
            "breached": True,
            "status": (scope or {}).get("status") or "undeclared",
            "intent": (scope or {}).get("intent") or "",
            "source": (scope or {}).get("source") or "",
            "in_scope": {"prefixes": [], "modules": [], "files": []},
            "cluster_type": cluster_type,
            "cluster_severity": cluster_severity,
            "edited": files[:30],
            "out_of_scope": [],
            "reason": "Wide edit cluster without a declared path contract",
        }

    assert scope is not None
    out = [f for f in files if not path_in_scope(f, scope)]
    if not out:
        return None
    return {
        "breached": True,
        "status": scope.get("status"),
        "intent": scope.get("intent") or "",
        "source": scope.get("source") or "",
        "in_scope": {
            "prefixes": list(scope.get("prefixes") or []),
            "modules": list(scope.get("modules") or []),
            "files": list(scope.get("files") or []),
        },
        "cluster_type": cluster_type,
        "cluster_severity": cluster_severity,
        "edited": files[:30],
        "out_of_scope": out[:30],
        "reason": f"Edited {len(out)} path(s) outside the turn scope contract",
    }


def build_scope_payload(
    *,
    repo: str,
    status: str,
    intent: str = "",
    prefixes: Optional[Sequence[str]] = None,
    modules: Optional[Sequence[str]] = None,
    files: Optional[Sequence[str]] = None,
    source: str = "mcp",
    conversation_id: Optional[str] = None,
    paths: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    status_n = (status or "declared").strip().lower()
    if status_n not in VALID_STATUSES:
        status_n = "declared"
    derived = prefixes_from_paths(paths or [])
    pref = list(prefixes) if prefixes is not None else derived["prefixes"]
    mods = list(modules) if modules is not None else derived["modules"]
    fils = list(files) if files is not None else derived["files"]
    # If caller passed only paths, merge derived
    if paths and prefixes is None and modules is None and files is None:
        pref, mods, fils = derived["prefixes"], derived["modules"], derived["files"]
    return {
        "repo": repo,
        "conversation_id": (conversation_id or "").strip() or None,
        "status": status_n,
        "source": source or "mcp",
        "intent": (intent or "")[:500],
        "prefixes": [normalize_rel(p) for p in pref if p],
        "modules": [normalize_rel(m) for m in mods if m],
        "files": [normalize_rel(f) for f in fils if f],
    }


def seed_scope_from_prompt(
    *,
    repo: str,
    prompt: str,
    attachments: Optional[Sequence[str]] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    paths = extract_paths_from_text(prompt) + [
        normalize_rel(a) for a in (attachments or []) if a
    ]
    derived = prefixes_from_paths(paths)
    if not has_path_contract({"prefixes": derived["prefixes"], "modules": derived["modules"], "files": derived["files"]}):
        return build_scope_payload(
            repo=repo,
            status="undeclared",
            intent=(prompt or "")[:240],
            source="prompt_seed",
            conversation_id=conversation_id,
            prefixes=[],
            modules=[],
            files=[],
        )
    return build_scope_payload(
        repo=repo,
        status="seeded",
        intent=(prompt or "")[:240],
        source="prompt_seed",
        conversation_id=conversation_id,
        prefixes=derived["prefixes"],
        modules=derived["modules"],
        files=derived["files"],
    )


def provisional_from_files(
    *,
    repo: str,
    file_paths: Sequence[str],
    intent: str = "Provisional scope from first edit(s)",
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    return build_scope_payload(
        repo=repo,
        status="provisional",
        intent=intent,
        source="first_edit",
        conversation_id=conversation_id,
        paths=file_paths,
    )


# ── Persistence helpers (MemoryDatabase duck-typed) ─────────────────────────


def upsert_scope(db: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Persist scope; returns stored row dict."""
    return db.upsert_edit_scope(payload)


def get_scope(
    db: Any,
    *,
    repo: str,
    conversation_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    return db.get_edit_scope(repo=repo, conversation_id=conversation_id)


def declare_scope(db: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Declare/amend without clobbering stronger status on weaker seeds.

    Seeds do not overwrite declared/amended for the same conversation.
    """
    repo = payload["repo"]
    conv = payload.get("conversation_id")
    existing = get_scope(db, repo=repo, conversation_id=conv)
    new_status = payload.get("status") or "declared"
    if existing and existing.get("status") in ("declared", "amended"):
        if new_status in ("seeded", "undeclared", "provisional"):
            return existing
    if (
        existing
        and existing.get("status") == "provisional"
        and new_status == "seeded"
    ):
        # Keep provisional paths if seed is empty
        if not has_path_contract(payload) and has_path_contract(existing):
            return existing
    stored = upsert_scope(db, payload)
    return stored


def check_against_store(
    db: Any,
    *,
    repo: str,
    files: Sequence[str],
    conversation_id: Optional[str] = None,
    cluster_type: Optional[str] = None,
    cluster_severity: Optional[str] = None,
    ensure_provisional: bool = False,
) -> Dict[str, Any]:
    scope = get_scope(db, repo=repo, conversation_id=conversation_id)
    if ensure_provisional and files and not has_path_contract(scope):
        scope = declare_scope(
            db,
            provisional_from_files(
                repo=repo,
                file_paths=files[:3],
                conversation_id=conversation_id,
            ),
        )
    breach = check_scope_breach(
        scope,
        files,
        cluster_type=cluster_type,
        cluster_severity=cluster_severity,
    )
    return {
        "repo": repo,
        "conversation_id": conversation_id,
        "scope": scope,
        "breached": bool(breach),
        "breach": breach,
    }
