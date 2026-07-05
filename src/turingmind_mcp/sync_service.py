"""Atomic sync orchestration for POST /api/v2/sync."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from .database import MemoryDatabase
from .control_plane import CognitionControlPlane
from .sqlite_guard import commit_with_retry
from .v2_engine.database import save_spec_node, use_write_connection
from .v2_engine.handlers import (
    _all_nodes_for_repo,
    _now,
    cascade_blast_radius,
    recalculate_confidence,
)
from .v2_engine.models import Evidence, SpecStatus

logger = logging.getLogger("turingmind-mcp.sync-service")


@contextmanager
def atomic_sync_transaction(db: MemoryDatabase):
    """Wrap sync invalidation + control plane writes in one SQLite transaction."""
    conn = db.conn
    conn.execute("BEGIN IMMEDIATE")
    db._defer_commit = True  # noqa: SLF001 — intentional batch mode
    try:
        with use_write_connection(conn):
            yield conn
        commit_with_retry(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        db._defer_commit = False


def invalidate_nodes_for_files(
    repo: str,
    files: List[str],
    *,
    cluster_label: str = "",
    conn=None,
) -> tuple[List[str], List[Dict[str, Any]]]:
    """Invalidate SpecNodes overlapping changed files and run cascade."""
    changed_set = set(files)
    impacted_nodes: List[str] = []

    for node in _all_nodes_for_repo(repo):
        node_files = set(node.implementation.files)
        overlap = changed_set.intersection(node_files)
        if not overlap:
            continue

        old_conf = node.state.confidence
        new_score = float(round(old_conf * 0.9, 4)) if old_conf > 0 else 0.0

        detail = f"Files modified: {', '.join(sorted(overlap))}"
        if cluster_label:
            detail += cluster_label

        node.state.evidence.append(
            Evidence(
                kind="code_change",
                score=new_score,
                detail=detail,
                source="git_hook",
                origin_id=f"sync_{node.id}",
            )
        )
        node.state.confidence = recalculate_confidence(node)
        if node.state.status == SpecStatus.VERIFIED:
            node.state.status = SpecStatus.IN_PROGRESS
        node.updated_at = _now()

        save_spec_node(node, conn=conn)
        impacted_nodes.append(node.id)

    cascades: List[Dict[str, Any]] = []
    for nid in impacted_nodes:
        res = cascade_blast_radius(nid, repo, conn=conn)
        if res.get("impacted_count", 0) > 0:
            cascades.append(res)

    return impacted_nodes, cascades


def run_sync(
    db: MemoryDatabase,
    *,
    repo: str,
    files: List[str],
    composer_id: Optional[str] = None,
    session_id: Optional[str] = None,
    branch: Optional[str] = None,
    head_sha: Optional[str] = None,
    workspace_root: Optional[str] = None,
    cluster_meta: Optional[Dict[str, Any]] = None,
    cluster_label: str = "",
) -> Dict[str, Any]:
    """Run full sync atomically: graph invalidation + session/bundle update."""
    from .profile_config import is_memory_profile

    with atomic_sync_transaction(db) as conn:
        if is_memory_profile():
            logger.debug("memory profile: skipping SpecNode invalidation on sync")
            impacted_nodes: List[str] = []
            cascades: List[Dict[str, Any]] = []
        else:
            impacted_nodes, cascades = invalidate_nodes_for_files(
                repo, files, cluster_label=cluster_label, conn=conn
            )
        return CognitionControlPlane.sync_codebase(
            db=db,
            repo=repo,
            files=files,
            composer_id=composer_id,
            session_id=session_id,
            branch=branch,
            head_sha=head_sha,
            workspace_root=workspace_root,
            cluster_meta=cluster_meta,
            impacted_nodes=impacted_nodes,
            cascades=cascades,
        )
