"""
Background chat → observation poller (Tier B capture funnel).

Architecture placement
----------------------
This module sits on the **API server** (port 8477), not the stdio MCP bridge.
The reconcile loop already runs here; co-locating chat polling keeps all
"always-on ingestion" in one supervised process (launchd / systemd).

    Cursor SQLite  →  observation-ready check  →  extract latest bubble text
                                                      ↓
                                            observations (pending, conf≈0.3)
                                                      ↓
                                            reconcile passes (deterministic)

Cursor isolation
----------------
Observation polling uses **separate cursor fields** on ``chat_capture_state``
(``lastObservationBubbleCount``, etc.) so it does not advance the
``messageCount`` / ``lastCapturedAt`` cursor used by ``capture_exchange()``.
Both paths can run without starving the LLM chat-analysis flow.

Repo scope
----------
Chat is workspace-local; observations attach to the repo inferred from **git
origin** (or ``~/.turingmind/env`` / ``TURINGMIND_DEFAULT_REPO`` as fallback).
We do **not** loop ``repos_with_activity()`` — that would mis-attribute the
same composer exchange to the wrong repo.

Disable with ``TURINGMIND_CHAT_POLL_INTERVAL_SEC=0``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

from .chat_capture import (
    CURRENT_CHAT_UPDATE_COOLDOWN_MS,
    filter_to_latest_exchange,
    should_capture_chat,
)
from .cursor_database_reader import (
    extract_metadata,
    find_cursor_database,
    get_last_exchange_state,
    get_most_recently_active_composer,
)
from .database import MemoryDatabase
from .observation_capture import record_chat_exchange_observation

logger = logging.getLogger("turingmind-mcp.chat-poller")

ACTIVE_AGENT_SESSIONS: dict[str, float] = {}

def register_active_composer(composer_id: str):
    """Mark a composer ID as belonging to the active coding agent."""
    global ACTIVE_AGENT_SESSIONS
    ACTIVE_AGENT_SESSIONS[composer_id] = time.time()


def _resolve_repo_from_vscdb(db_path: str) -> Optional[str]:
    """Parse history.entries or similar to extract workspace files and get git repo origin."""
    try:
        import sqlite3
        import json
        import urllib.parse
        
        # Connect to the workspace DB
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Check if ItemTable exists
        table_check = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ItemTable'"
        ).fetchone()
        if not table_check:
            conn.close()
            return None

        cursor.execute("SELECT value FROM ItemTable WHERE key='history.entries'")
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
            
        entries = json.loads(row["value"])
        for entry in entries:
            resource = entry.get("editor", {}).get("resource", "")
            if resource.startswith("file://"):
                file_path = urllib.parse.unquote(resource[7:])
                # Find git root by walking up
                curr_dir = os.path.dirname(file_path)
                while curr_dir and curr_dir != "/":
                    git_dir = os.path.join(curr_dir, ".git")
                    if os.path.isdir(git_dir):
                        url = subprocess.check_output(
                            ["git", "remote", "get-url", "origin"],
                            cwd=curr_dir,
                            stderr=subprocess.DEVNULL,
                        ).decode().strip()
                        match = re.search(r"[:/]([^/:]+/[^/]+?)(\.git)?$", url)
                        if match:
                            return match.group(1)
                    curr_dir = os.path.dirname(curr_dir)
    except Exception as e:
        logger.debug(f"Failed to resolve repo from vscdb: {e}")
    return None


def _resolve_default_repo(db_path: Optional[str] = None) -> str:
    """Git origin from active workspace db first, then process cwd, then ~/.turingmind/env, then process env."""
    if db_path:
        repo_from_db = _resolve_repo_from_vscdb(db_path)
        if repo_from_db:
            return repo_from_db

    try:
        url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=os.getcwd(),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        match = re.search(r"[:/]([^/:]+/[^/]+?)(\.git)?$", url)
        if match:
            return match.group(1)
    except Exception:
        pass
    try:
        from .daemon_setup import load_env_file

        file_env = load_env_file()
        if file_env.get("TURINGMIND_DEFAULT_REPO"):
            return file_env["TURINGMIND_DEFAULT_REPO"]
    except Exception:
        pass
    env = os.environ.get("TURINGMIND_DEFAULT_REPO")
    if env:
        return env
    return "local/workspace"



def _check_observation_ready(
    db: MemoryDatabase,
) -> Optional[Dict[str, Any]]:
    """Return composer/exchange info when a dumb observation should be recorded.

    Mirrors ``check_exchanges()`` readiness (complete exchange, debounce,
    cooldown) but compares against the **observation** cursor, not the
    capture_exchange cursor.
    """
    db_path = find_cursor_database()
    if db_path is None:
        return None

    db_path_str = str(db_path)
    most_recent = get_most_recently_active_composer(db_path_str)
    if not most_recent or most_recent.get("bubbleCount", 0) < 2:
        return None

    composer_id = most_recent["composerId"]

    # Isolate chat mining to only those composers used by the active coding agent
    if os.environ.get("TURINGMIND_CHAT_POLL_ISOLATED", "1") != "0":
        if composer_id not in ACTIVE_AGENT_SESSIONS:
            return None

    last_activity_at = most_recent["lastActivityAt"]
    exchange_state = get_last_exchange_state(db_path_str, composer_id)
    if not exchange_state:
        return None

    cached = db.get_chat_capture_state(composer_id)
    now = int(time.time() * 1000)
    obs_bubble_count = cached.get("lastObservationBubbleCount", 0) if cached else 0
    total_bubbles = exchange_state.get("totalBubbles", 0)

    exchange_is_complete = exchange_state.get("isCompleteExchange", False)
    has_new_exchange = total_bubbles > obs_bubble_count
    is_write_complete = (now - (last_activity_at or 0)) >= 500

    last_obs_at = cached.get("lastObservationAt", 0) if cached else 0
    cooldown_expired = not cached or (now - last_obs_at) >= CURRENT_CHAT_UPDATE_COOLDOWN_MS

    if not (exchange_is_complete and has_new_exchange and is_write_complete and cooldown_expired):
        return None

    return {
        "composerId": composer_id,
        "exchangeState": exchange_state,
        "isUpdate": cached is not None,
    }


def _advance_observation_cursor(
    db: MemoryDatabase,
    composer_id: str,
    exchange_state: Dict[str, Any],
) -> None:
    """Advance observation debounce cursor without touching capture_exchange fields."""
    now = int(time.time() * 1000)
    db.update_chat_capture_state(
        composer_id,
        last_observation_bubble_count=exchange_state.get("totalBubbles", 0),
        last_observation_at=now,
        last_observation_exchange_timestamp=exchange_state.get("lastBubbleTimestamp", 0),
    )


async def start_chat_observation_poller(get_db) -> None:
    """Launch infinite poll loop — called from api_server startup."""
    interval_sec = float(os.environ.get("TURINGMIND_CHAT_POLL_INTERVAL_SEC", "15"))
    if interval_sec <= 0:
        logger.info("Chat observation poller disabled (TURINGMIND_CHAT_POLL_INTERVAL_SEC <= 0)")
        return

    async def loop() -> None:
        repo = _resolve_default_repo()
        while True:
            await asyncio.sleep(interval_sec)
            try:
                # Cursor DB reads can take seconds — never block the HTTP event loop.
                stats = await asyncio.to_thread(
                    _poll_chat_observations_sync, get_db(), repo
                )
                if stats.get("recorded"):
                    logger.info("Chat poll [%s]: %s", repo, stats)
            except Exception as exc:
                logger.warning("Chat observation poll cycle failed: %s", exc)

    asyncio.create_task(loop())
    logger.info("Chat observation poller started (every %g s, repo=%s)", interval_sec, _resolve_default_repo())


def _poll_chat_observations_sync(
    db: MemoryDatabase,
    repo: str,
    session_start_time: Optional[int] = None,
) -> Dict[str, Any]:
    """Sync poll body — run via asyncio.to_thread from the poller loop."""
    ready = _check_observation_ready(db)
    if not ready:
        return {"recorded": 0}

    composer_id = ready["composerId"]
    exchange_state = ready["exchangeState"] or {}

    db_path = find_cursor_database(composer_id)
    if db_path is None:
        return {"recorded": 0}

    dynamic_repo = _resolve_default_repo(str(db_path)) or repo

    cached = db.get_chat_capture_state(composer_id)
    last_ts = cached.get("lastObservationExchangeTimestamp", 0) if cached else 0
    full_metadata = extract_metadata(composer_id, str(db_path))
    if not full_metadata:
        _advance_observation_cursor(db, composer_id, exchange_state)
        return {"recorded": 0}

    metadata = filter_to_latest_exchange(full_metadata, last_ts)
    should_capture, _skip = should_capture_chat(
        metadata,
        cached,
        session_start_time,
        ready.get("isUpdate", False),
    )
    if not should_capture:
        _advance_observation_cursor(db, composer_id, exchange_state)
        return {"recorded": 0, "skipped": True}

    obs_id = record_chat_exchange_observation(
        db,
        repo=dynamic_repo,
        composer_id=composer_id,
        metadata=metadata,
    )
    if not obs_id:
        _advance_observation_cursor(db, composer_id, exchange_state)
        return {"recorded": 0}

    _advance_observation_cursor(db, composer_id, exchange_state)
    logger.info(
        "Chat observation [%s] composer=%s obs=%s",
        dynamic_repo,
        composer_id[:8],
        obs_id[:8],
    )
    return {"recorded": 1}
