"""
Memory embeddings for Tier D duplicate detection.

Preferred: Azure OpenAI ``text-embedding-3-small`` when configured via env.
Fallback: deterministic hash bag-of-words (offline, no network).

Embeddings power reconcile pass ``suggest_duplicate_merges``: near-duplicate
memories surface as ``semantic_duplicate`` queue findings for agent merge review.

Environment (Azure — use either full URL or endpoint + deployment name):

    AZURE_OPENAI_EMBEDDING_DEPLOYMENT=https://.../deployments/text-embedding-3-small/embeddings?api-version=2023-05-15
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT_KEY=...   # or AZURE_OPENAI_KEY

    # or:
    AZURE_OPENAI_ENDPOINT=https://turingmind-ai.openai.azure.com
    EMBEDDING_DEPLOYMENT_NAME=text-embedding-3-small
    AZURE_OPENAI_EMBEDDING_API_VERSION=2023-05-15

Set ``TURINGMIND_EMBEDDING_PROVIDER=hash_bow`` to force the local fallback.

Optional ANN acceleration: ``pip install sqlite-vec`` (or ``pip install turingmind-mcp[vec]``).
When installed, reconcile pass 8 uses KNN search for repos with 40+ embedded memories;
set ``TURINGMIND_VEC_INDEX=0`` to disable.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import struct
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

HASH_BOW_METHOD = "hash_bow_v1"
AZURE_TE3_SMALL_METHOD = "azure_te3_small_v1"

HASH_BOW_DIM = 128
AZURE_TE3_SMALL_DIM = 1536

# Backward-compatible aliases used by tests and older imports.
EMBED_DIM = HASH_BOW_DIM
EMBED_METHOD = HASH_BOW_METHOD

DUPLICATE_COSINE_THRESHOLD = 0.82
DUPLICATE_THRESHOLDS: Dict[str, float] = {
    HASH_BOW_METHOD: 0.82,
    AZURE_TE3_SMALL_METHOD: 0.90,
}


def duplicate_threshold_for(method: str) -> float:
    """Return the cosine threshold for a given embedding method."""
    return DUPLICATE_THRESHOLDS.get(method, DUPLICATE_COSINE_THRESHOLD)


def _tokenize(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9_]+", text.lower()) if len(w) > 2]


def _stable_bucket(token: str, dim: int) -> int:
    """Process-stable hash bucket (Python's built-in hash is salted per process)."""
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "little") % dim


def embed_text_hash_bow(text: str, *, dim: int = HASH_BOW_DIM) -> bytes:
    """Return a normalized hash-bow vector packed as little-endian float32 bytes."""
    vec = [0.0] * dim
    for token in _tokenize(text):
        vec[_stable_bucket(token, dim)] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return pack_vector([v / norm for v in vec])


def embed_text(text: str, *, dim: int = HASH_BOW_DIM) -> bytes:
    """Hash-bow embed (backward-compatible name)."""
    return embed_text_hash_bow(text, dim=dim)


def pack_vector(vec: Sequence[float]) -> bytes:
    """Pack a float vector as little-endian float32 bytes."""
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_embedding(blob: bytes, *, dim: Optional[int] = None) -> List[float]:
    """Unpack float32 bytes; infer dimension from blob size when omitted."""
    if dim is None:
        if len(blob) % 4 != 0:
            raise ValueError(f"Invalid embedding blob length: {len(blob)}")
        dim = len(blob) // 4
    return list(struct.unpack(f"{dim}f", blob))


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _resolve_azure_endpoint(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    if "/deployments/" in raw:
        return raw.split("/openai/deployments/")[0].rstrip("/")
    return raw.rstrip("/")


def resolve_azure_embedding_url() -> Optional[str]:
    """Build the Azure embeddings REST URL from env, or return None if unconfigured."""
    full_url = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "").strip()
    if full_url and "embeddings" in full_url:
        return full_url

    endpoint = _resolve_azure_endpoint(os.getenv("AZURE_OPENAI_ENDPOINT", ""))
    deployment = (
        os.getenv("EMBEDDING_DEPLOYMENT_NAME")
        or os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME")
        or "text-embedding-3-small"
    )
    api_version = (
        os.getenv("AZURE_OPENAI_EMBEDDING_API_VERSION")
        or os.getenv("AZURE_OPENAI_API_VERSION")
        or "2023-05-15"
    )
    if not endpoint:
        return None
    return (
        f"{endpoint}/openai/deployments/{deployment}/embeddings"
        f"?api-version={api_version}"
    )


def resolve_azure_embedding_key() -> Optional[str]:
    """Return the API key for Azure embeddings, if configured."""
    return (
        os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_KEY")
        or os.getenv("AZURE_OPENAI_KEY")
        or os.getenv("AZURE_OPENAI_API_KEY")
    )


def azure_embeddings_configured() -> bool:
    """True when Azure embedding URL and key are both available."""
    if os.getenv("TURINGMIND_EMBEDDING_PROVIDER", "").strip().lower() == "hash_bow":
        return False
    return bool(resolve_azure_embedding_url() and resolve_azure_embedding_key())


def preferred_embed_method() -> str:
    """Return the embedding method reconcile should index with."""
    if azure_embeddings_configured():
        return AZURE_TE3_SMALL_METHOD
    return HASH_BOW_METHOD


def embed_texts_azure(texts: List[str]) -> Optional[List[List[float]]]:
    """Call Azure OpenAI embeddings API for a batch of strings.

    Returns None on configuration or transport errors (caller should fall back).
    """
    url = resolve_azure_embedding_url()
    api_key = resolve_azure_embedding_key()
    if not url or not api_key or not texts:
        return None

    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed; cannot call Azure embeddings")
        return None

    payload = {"input": texts, "encoding_format": "float"}
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                url,
                headers={"Content-Type": "application/json", "api-key": api_key},
                json=payload,
            )
            if not response.is_success:
                logger.warning(
                    "Azure embeddings failed: %s %s",
                    response.status_code,
                    response.text[:300],
                )
                return None
            data = response.json()
    except Exception as exc:
        logger.warning("Azure embeddings request error: %s", exc)
        return None

    rows = data.get("data") or []
    if len(rows) != len(texts):
        logger.warning(
            "Azure embeddings returned %s vectors for %s inputs",
            len(rows),
            len(texts),
        )
        return None

    vectors: List[List[float]] = []
    for row in sorted(rows, key=lambda r: r.get("index", 0)):
        embedding = row.get("embedding")
        if not isinstance(embedding, list):
            logger.warning("Azure embeddings row missing embedding list")
            return None
        vectors.append([float(v) for v in embedding])
    return vectors


def index_memory_embeddings(db: Any, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute and store embeddings for memory entries.

    Uses Azure ``text-embedding-3-small`` when configured; otherwise hash-bow.
    Falls back to hash-bow if the Azure batch call fails.
    """
    if not entries:
        return {"embeddings_indexed": 0, "embed_method": preferred_embed_method()}

    method = preferred_embed_method()
    if method == AZURE_TE3_SMALL_METHOD:
        vectors = embed_texts_azure([e["content"] for e in entries])
        if vectors is not None:
            for entry, vec in zip(entries, vectors):
                db.upsert_memory_embedding(
                    entry["memory_id"],
                    pack_vector(vec),
                    AZURE_TE3_SMALL_METHOD,
                )
            return {
                "embeddings_indexed": len(entries),
                "embed_method": AZURE_TE3_SMALL_METHOD,
            }
        logger.info("Azure embeddings unavailable; using hash_bow fallback")

    for entry in entries:
        db.upsert_memory_embedding(
            entry["memory_id"],
            embed_text_hash_bow(entry["content"]),
            HASH_BOW_METHOD,
        )
    return {
        "embeddings_indexed": len(entries),
        "embed_method": HASH_BOW_METHOD,
    }
