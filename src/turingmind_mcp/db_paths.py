"""Canonical SQLite path resolution for the unified TuringMind store."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

DEFAULT_DB_DIR = Path.home() / ".turingmind"
DEFAULT_PRIMARY_DB = DEFAULT_DB_DIR / "memory.db"
LEGACY_V2_DB = DEFAULT_DB_DIR / "v2_memory.db"


def resolve_primary_db_path(explicit: Optional[str] = None) -> str:
    """Return the canonical unified database file path.

    Precedence: explicit argument > TURINGMIND_DB_PATH > TURINGMIND_MEMORY_DB >
    ``~/.turingmind/memory.db``.
    """
    if explicit:
        return str(Path(explicit).expanduser())
    for env_key in ("TURINGMIND_DB_PATH", "TURINGMIND_MEMORY_DB"):
        env_val = os.getenv(env_key, "").strip()
        if env_val:
            return str(Path(env_val).expanduser())
    return str(DEFAULT_PRIMARY_DB)


def legacy_v2_db_path() -> str:
    """Path to the pre-unification SpecNode database (migration source only)."""
    override = os.getenv("TURINGMIND_LEGACY_V2_DB", "").strip()
    if override:
        return str(Path(override).expanduser())
    return str(LEGACY_V2_DB)


def ensure_db_dir(db_path: str) -> None:
    """Create parent directory with owner-only permissions."""
    parent = Path(db_path).expanduser().parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
