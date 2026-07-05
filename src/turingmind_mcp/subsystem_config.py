"""Load optional workspace subsystem mappings from .turingmind/config.json."""

from __future__ import annotations

import fnmatch
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("turingmind-mcp.subsystem-config")


def _pattern_matches(pattern: str, file_path: str) -> bool:
    """Return True when a repo-relative file path matches a config glob."""
    norm = file_path.replace("\\", "/").lstrip("./")
    pat = pattern.replace("\\", "/")

    if pat.endswith("/**"):
        prefix = pat[:-3].rstrip("/")
        return norm == prefix or norm.startswith(f"{prefix}/")

    if "**" in pat:
        glob_pat = pat.replace("**", "*")
        return fnmatch.fnmatch(norm, glob_pat) or fnmatch.fnmatch(norm, glob_pat.lstrip("/"))

    return fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(Path(norm).name, pat)


def load_subsystem_map(workspace_root: Optional[str]) -> Dict[str, List[str]]:
    """Load subsystem path mappings from ``<workspace>/.turingmind/config.json``."""
    if not workspace_root:
        return {}

    config_path = Path(workspace_root).expanduser() / ".turingmind" / "config.json"
    if not config_path.is_file():
        return {}

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read subsystem config %s: %s", config_path, exc)
        return {}

    subsystems = raw.get("subsystems")
    if not isinstance(subsystems, dict):
        return {}

    result: Dict[str, List[str]] = {}
    for name, patterns in subsystems.items():
        if not isinstance(name, str) or not isinstance(patterns, list):
            continue
        cleaned = [p for p in patterns if isinstance(p, str) and p.strip()]
        if cleaned:
            result[name] = cleaned
    return result


def match_subsystem(file_path: str, subsystem_map: Dict[str, List[str]]) -> Optional[str]:
    """Return the first configured subsystem name matching ``file_path``."""
    if not subsystem_map:
        return None

    for name, patterns in subsystem_map.items():
        for pattern in patterns:
            if _pattern_matches(pattern, file_path):
                return name
    return None
