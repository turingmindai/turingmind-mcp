"""Coding session TTL, heartbeat, explicit end, and GC helpers."""

from __future__ import annotations

import datetime
import logging
import os
from typing import Any, Dict, Optional

from .database import MemoryDatabase
from .sqlite_guard import commit_with_retry, serialized_sqlite_write

logger = logging.getLogger("turingmind-mcp.session-lifecycle")

SESSION_TTL_HOURS = float(os.environ.get("TURINGMIND_SESSION_TTL_HOURS", "4"))


def session_expires_at(
    *,
    now: Optional[datetime.datetime] = None,
    hours: Optional[float] = None,
) -> str:
    """Return ISO timestamp for session expiry (default 4h from now)."""
    base = now or datetime.datetime.utcnow()
    ttl = SESSION_TTL_HOURS if hours is None else hours
    return (base + datetime.timedelta(hours=ttl)).isoformat()


def build_session_summary(sess: Dict[str, Any], *, reason: str = "expired") -> str:
    """Build distilled observation text for a completed coding session."""
    subsystems = sess.get("touched_subsystems") or []
    files = sess.get("touched_files") or []
    recalled = sess.get("recall_history") or []
    branch = sess.get("branch") or "unknown"
    composer = sess.get("composer_id") or "unknown"
    return (
        f"Session {reason} for composer {composer} on branch {branch}. "
        f"Subsystems touched: {', '.join(subsystems) if subsystems else 'none'}. "
        f"Files edited: {', '.join(files) if files else 'none'}. "
        f"Recalled memories: {', '.join(recalled) if recalled else 'none'}."
    )


def finalize_session(
    db: MemoryDatabase,
    sess: Dict[str, Any],
    *,
    reason: str = "expired",
) -> bool:
    """Write summary observation and delete the session row."""
    summary = build_session_summary(sess, reason=reason)
    db._defer_commit = True  # noqa: SLF001 — batch observation + delete
    deleted = False
    try:
        db.create_observation(
            repo=sess["repo"],
            event_type="session_context",
            content=summary,
            confidence=0.8,
        )
        deleted = db.delete_coding_session(sess["session_id"], defer_commit=True)
        commit_with_retry(db.conn)
    finally:
        db._defer_commit = False
    if deleted:
        logger.info(
            "Finalized coding session %s for composer %s (%s)",
            sess["session_id"],
            sess.get("composer_id"),
            reason,
        )
    return deleted


def end_session_by_id(
    db: MemoryDatabase,
    session_id: str,
    *,
    reason: str = "session_end",
) -> Dict[str, Any]:
    """Explicitly end a session (SessionEnd hook / CLI)."""
    sess = db.get_coding_session_by_id(session_id)
    if not sess:
        return {"status": "not_found", "session_id": session_id}

    with serialized_sqlite_write():
        finalize_session(db, sess, reason=reason)
    return {
        "status": "ended",
        "session_id": session_id,
        "composer_id": sess.get("composer_id"),
        "reason": reason,
    }


def end_session_by_composer(
    db: MemoryDatabase,
    composer_id: str,
    repo: str,
    *,
    reason: str = "session_end",
) -> Dict[str, Any]:
    """End the active session for composer_id + repo."""
    sess = db.get_coding_session(composer_id, repo)
    if not sess:
        return {
            "status": "not_found",
            "composer_id": composer_id,
            "repo": repo,
        }
    return end_session_by_id(db, sess["session_id"], reason=reason)


def run_session_gc(db: MemoryDatabase) -> Dict[str, Any]:
    """Expire stale sessions past TTL and archive summaries."""
    stats = {"archived": 0, "errors": 0}
    try:
        expired = db.list_expired_coding_sessions()
        for sess in expired:
            try:
                with serialized_sqlite_write():
                    if finalize_session(db, sess, reason="completed"):
                        stats["archived"] += 1
            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    "Session GC failed for %s: %s",
                    sess.get("session_id"),
                    exc,
                )
    except Exception as exc:
        logger.error("Session GC cycle failed: %s", exc)
        stats["errors"] += 1
    return stats
