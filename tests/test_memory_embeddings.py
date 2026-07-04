"""Tests for memory embeddings (Tier D duplicate detection)."""

import unittest
from unittest import mock

from turingmind_mcp.memory_embeddings import (
    AZURE_TE3_SMALL_METHOD,
    HASH_BOW_DIM,
    HASH_BOW_METHOD,
    azure_embeddings_configured,
    cosine_similarity,
    duplicate_threshold_for,
    embed_text,
    embed_texts_azure,
    index_memory_embeddings,
    preferred_embed_method,
    resolve_azure_embedding_url,
    unpack_embedding,
)


class TestHashBowEmbeddings(unittest.TestCase):
    def test_embed_produces_normalized_vector(self):
        blob = embed_text("async await for database operations")
        vec = unpack_embedding(blob, dim=HASH_BOW_DIM)
        self.assertEqual(len(vec), HASH_BOW_DIM)
        norm = sum(v * v for v in vec) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=5)

    def test_similar_texts_score_high(self):
        a = unpack_embedding(embed_text("Always use async await for IO operations"))
        b = unpack_embedding(embed_text("Use async await for all IO operations"))
        sim = cosine_similarity(a, b)
        self.assertGreaterEqual(sim, duplicate_threshold_for(HASH_BOW_METHOD))

    def test_dissimilar_texts_score_low(self):
        a = unpack_embedding(embed_text("kubernetes deployment manifests"))
        b = unpack_embedding(embed_text("react component styling guidelines"))
        sim = cosine_similarity(a, b)
        self.assertLess(sim, duplicate_threshold_for(HASH_BOW_METHOD))

    def test_identical_texts_score_one(self):
        text = "repository uses python with fastapi endpoints"
        a = unpack_embedding(embed_text(text))
        b = unpack_embedding(embed_text(text))
        self.assertAlmostEqual(cosine_similarity(a, b), 1.0, places=5)

    def test_embedding_is_process_stable(self):
        first = embed_text("deterministic embedding stability check")
        second = embed_text("deterministic embedding stability check")
        self.assertEqual(first, second)


class TestAzureEmbeddingConfig(unittest.TestCase):
    def test_resolve_full_deployment_url(self):
        url = (
            "https://turingmind-ai.openai.azure.com/openai/deployments/"
            "text-embedding-3-small/embeddings?api-version=2023-05-15"
        )
        with mock.patch.dict(
            "os.environ",
            {"AZURE_OPENAI_EMBEDDING_DEPLOYMENT": url},
            clear=False,
        ):
            self.assertEqual(resolve_azure_embedding_url(), url)

    def test_resolve_from_endpoint_components(self):
        with mock.patch.dict(
            "os.environ",
            {
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "",
                "AZURE_OPENAI_ENDPOINT": "https://turingmind-ai.openai.azure.com",
                "EMBEDDING_DEPLOYMENT_NAME": "text-embedding-3-small",
                "AZURE_OPENAI_EMBEDDING_API_VERSION": "2023-05-15",
            },
            clear=False,
        ):
            resolved = resolve_azure_embedding_url()
            self.assertIn("text-embedding-3-small", resolved or "")
            self.assertIn("2023-05-15", resolved or "")

    def test_preferred_method_falls_back_without_key(self):
        url = (
            "https://turingmind-ai.openai.azure.com/openai/deployments/"
            "text-embedding-3-small/embeddings?api-version=2023-05-15"
        )
        env = {
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": url,
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT_KEY": "",
            "AZURE_OPENAI_KEY": "",
            "AZURE_OPENAI_API_KEY": "",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            self.assertFalse(azure_embeddings_configured())
            self.assertEqual(preferred_embed_method(), HASH_BOW_METHOD)

    @mock.patch("httpx.Client")
    def test_embed_texts_azure_batch(self, mock_client_cls):
        url = (
            "https://turingmind-ai.openai.azure.com/openai/deployments/"
            "text-embedding-3-small/embeddings?api-version=2023-05-15"
        )
        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "data": [
                {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                {"index": 1, "embedding": [0.0, 1.0, 0.0]},
            ]
        }
        mock_client = mock.Mock()
        mock_client.__enter__ = mock.Mock(return_value=mock_client)
        mock_client.__exit__ = mock.Mock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        with mock.patch.dict(
            "os.environ",
            {
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": url,
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT_KEY": "test-key",
            },
            clear=False,
        ):
            vectors = embed_texts_azure(["hello", "world"])

        self.assertIsNotNone(vectors)
        self.assertEqual(len(vectors), 2)
        self.assertEqual(vectors[0][0], 1.0)
        mock_client.post.assert_called_once()

    def test_index_memory_embeddings_azure(self):
        import tempfile
        from pathlib import Path

        from turingmind_mcp.database import MemoryDatabase

        db = MemoryDatabase(db_path=str(Path(tempfile.mkdtemp()) / "embed.db"))
        try:
            mid = db.create_memory_entry(
                repo="test/repo",
                memory_type="learned_pattern",
                content="Always validate JWT on protected routes",
                scope="repo",
            )
            fake_vec = [0.1] * 1536
            with mock.patch(
                "turingmind_mcp.memory_embeddings.embed_texts_azure",
                return_value=[fake_vec],
            ), mock.patch(
                "turingmind_mcp.memory_embeddings.preferred_embed_method",
                return_value=AZURE_TE3_SMALL_METHOD,
            ):
                stats = index_memory_embeddings(
                    db, [{"memory_id": mid, "content": "Always validate JWT on protected routes"}]
                )
            self.assertEqual(stats["embed_method"], AZURE_TE3_SMALL_METHOD)
            rows = db.list_memory_embeddings("test/repo")
            self.assertEqual(rows[0]["method"], AZURE_TE3_SMALL_METHOD)
            self.assertEqual(len(unpack_embedding(rows[0]["embedding"])), 1536)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
