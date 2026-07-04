"""Optional sqlite-vec ANN index for embedding duplicate detection.

When the ``sqlite-vec`` package is installed and
``TURINGMIND_VEC_INDEX`` is not ``0``, reconcile pass 8 uses KNN queries
instead of O(n²) brute-force cosine comparison for large repos.

Falls back to brute force when sqlite-vec is unavailable or row count is
below ``ANN_MIN_ROWS``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .memory_embeddings import cosine_similarity, pack_vector, unpack_embedding

logger = logging.getLogger(__name__)

ANN_MIN_ROWS = 40
ANN_NEIGHBORS = 25

try:
    import sqlite_vec  # type: ignore

    _SQLITE_VEC_AVAILABLE = True
except ImportError:
    sqlite_vec = None  # type: ignore
    _SQLITE_VEC_AVAILABLE = False


def sqlite_vec_enabled() -> bool:
    """True when sqlite-vec is installed and not explicitly disabled."""
    if os.getenv("TURINGMIND_VEC_INDEX", "1").strip() in ("0", "false", "no"):
        return False
    return _SQLITE_VEC_AVAILABLE


def _vec_table_name(method: str, dim: int) -> str:
    safe = re.sub(r"[^a-z0-9]+", "_", method.lower()).strip("_")
    return f"memory_vec_{safe}_{dim}"


def _memory_rowid(memory_id: str) -> int:
    digest = hashlib.blake2b(memory_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") & 0x7FFFFFFFFFFFFFFF or 1


def _load_vec_extension(conn: Any) -> bool:
    if not sqlite_vec_enabled():
        return False
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception as exc:
        logger.debug("sqlite-vec load failed: %s", exc)
        return False


def _sync_vec_table(
    conn: Any,
    table: str,
    dim: int,
    items: List[Tuple[str, Sequence[float]]],
) -> bool:
    """Rebuild a vec0 table for one embedding method/dimension."""
    if not _load_vec_extension(conn):
        return False
    try:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute(
            f"CREATE VIRTUAL TABLE {table} USING vec0(embedding float[{dim}])"
        )
        for memory_id, vec in items:
            if len(vec) != dim:
                continue
            conn.execute(
                f"INSERT INTO {table}(rowid, embedding) VALUES (?, ?)",
                (_memory_rowid(memory_id), pack_vector(vec)),
            )
        conn.commit()
        return True
    except Exception as exc:
        logger.warning("sqlite-vec index rebuild failed for %s: %s", table, exc)
        return False


def _brute_force_pairs(
    ids: List[str],
    vectors: Dict[str, List[float]],
    contents: Dict[str, str],
    threshold: float,
) -> List[Tuple[str, str, float]]:
    pairs: List[Tuple[str, str, float]] = []
    for i, id_a in enumerate(ids):
        for id_b in ids[i + 1 :]:
            if contents.get(id_a) == contents.get(id_b):
                continue
            sim = cosine_similarity(vectors[id_a], vectors[id_b])
            if sim >= threshold:
                pairs.append((id_a, id_b, sim))
    return pairs


def _ann_pairs(
    conn: Any,
    method: str,
    dim: int,
    ids: List[str],
    vectors: Dict[str, List[float]],
    contents: Dict[str, str],
    threshold: float,
) -> List[Tuple[str, str, float]]:
    table = _vec_table_name(method, dim)
    items = [(mid, vectors[mid]) for mid in ids]
    if not _sync_vec_table(conn, table, dim, items):
        return _brute_force_pairs(ids, vectors, contents, threshold)

    seen_pairs: Set[Tuple[str, str]] = set()
    id_by_rowid = {_memory_rowid(mid): mid for mid in ids}
    pairs: List[Tuple[str, str, float]] = []

    for memory_id in ids:
        query = pack_vector(vectors[memory_id])
        try:
            rows = conn.execute(
                f"""
                SELECT rowid, distance
                FROM {table}
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
                """,
                (query, ANN_NEIGHBORS),
            ).fetchall()
        except Exception as exc:
            logger.warning("sqlite-vec KNN query failed: %s", exc)
            return _brute_force_pairs(ids, vectors, contents, threshold)

        for rowid, _distance in rows:
            neighbor_id = id_by_rowid.get(rowid)
            if not neighbor_id or neighbor_id == memory_id:
                continue
            if contents.get(memory_id) == contents.get(neighbor_id):
                continue
            sim = cosine_similarity(vectors[memory_id], vectors[neighbor_id])
            if sim < threshold:
                continue
            pair = tuple(sorted((memory_id, neighbor_id)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            pairs.append((pair[0], pair[1], sim))

    return pairs


def find_embedding_duplicate_pairs(
    conn: Any,
    method: str,
    method_rows: List[dict],
    threshold: float,
    *,
    ann_min_rows: int = ANN_MIN_ROWS,
) -> List[Tuple[str, str, float]]:
    """Return (id_a, id_b, cosine_sim) pairs above threshold for one method."""
    if len(method_rows) < 2:
        return []

    vectors = {
        r["memory_id"]: unpack_embedding(r["embedding"])
        for r in method_rows
    }
    contents = {r["memory_id"]: r["content"] for r in method_rows}
    ids = list(vectors.keys())
    dim = len(next(iter(vectors.values())))

    use_ann = sqlite_vec_enabled() and len(ids) >= ann_min_rows
    if use_ann:
        return _ann_pairs(conn, method, dim, ids, vectors, contents, threshold)
    return _brute_force_pairs(ids, vectors, contents, threshold)
