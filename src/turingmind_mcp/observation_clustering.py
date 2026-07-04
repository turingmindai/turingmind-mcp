"""Semantic + lexical clustering for pending observations (Reconcile Pass 1)."""

from __future__ import annotations

from typing import Dict, List

from .memory_embeddings import (
    AZURE_TE3_SMALL_METHOD,
    HASH_BOW_METHOD,
    cosine_similarity,
    embed_text_hash_bow,
    embed_texts_azure,
    preferred_embed_method,
    unpack_embedding,
)

OBSERVATION_SIMILARITY_THRESHOLDS: Dict[str, float] = {
    HASH_BOW_METHOD: 0.78,
    AZURE_TE3_SMALL_METHOD: 0.88,
}


def observation_similarity_threshold(method: str | None = None) -> float:
    """Cosine threshold for grouping paraphrased observations."""
    method = method or preferred_embed_method()
    return OBSERVATION_SIMILARITY_THRESHOLDS.get(method, 0.78)


def build_observation_vectors(observations: List[dict]) -> tuple[str, Dict[str, List[float]]]:
    """Embed observation content for semantic clustering.

    Returns ``(embed_method, vectors_by_observation_id)``.
    """
    if not observations:
        return HASH_BOW_METHOD, {}

    texts = [o.get("content") or "" for o in observations]
    method = preferred_embed_method()
    vectors: Dict[str, List[float]] = {}

    if method == AZURE_TE3_SMALL_METHOD:
        batch = embed_texts_azure(texts)
        if batch is not None:
            for obs, vec in zip(observations, batch):
                vectors[obs["observation_id"]] = vec
            return AZURE_TE3_SMALL_METHOD, vectors
        method = HASH_BOW_METHOD

    for obs, text in zip(observations, texts):
        vectors[obs["observation_id"]] = unpack_embedding(embed_text_hash_bow(text))
    return HASH_BOW_METHOD, vectors


def observations_semantically_similar(
    obs_a: dict,
    obs_b: dict,
    vectors: Dict[str, List[float]],
    *,
    threshold: float | None = None,
    embed_method: str | None = None,
) -> bool:
    """True when two observations exceed the embedding cosine threshold."""
    id_a = obs_a.get("observation_id")
    id_b = obs_b.get("observation_id")
    if not id_a or not id_b:
        return False
    vec_a = vectors.get(id_a)
    vec_b = vectors.get(id_b)
    if not vec_a or not vec_b:
        return False
    limit = threshold
    if limit is None:
        limit = observation_similarity_threshold(embed_method)
    return cosine_similarity(vec_a, vec_b) >= limit
