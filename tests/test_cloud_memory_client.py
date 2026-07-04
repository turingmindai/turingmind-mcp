"""Tests for remote memory cloud sync client."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from turingmind_mcp.cloud_memory_client import use_cloud_sync, sync_memories_via_cloud_api


class TestUseCloudSync:
    def test_flag_disabled_returns_false(self):
        with mock.patch.dict(os.environ, {"TURINGMIND_CLOUD_SYNC": "0"}):
            assert use_cloud_sync("https://api.example.com", "tmk_test") is False

    def test_defaults_to_true_when_keys_present(self):
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("TURINGMIND_CLOUD_SYNC", None)
            assert use_cloud_sync("https://api.example.com", "tmk_test") is True

    def test_returns_false_when_keys_missing(self):
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("TURINGMIND_CLOUD_SYNC", None)
            assert use_cloud_sync("", "") is False


@pytest.mark.asyncio
class TestSyncMemoriesViaCloudApi:
    async def test_applies_pulled_rows_and_updates_cursor(self):
        db = mock.MagicMock()
        db.get_repo_sync_state.return_value = {"last_cloud_pull_at": None}
        db.list_memory_entries_for_cloud_sync.return_value = [{
            "memory_id": "m1",
            "type": "explicit_rule",
            "content": "rule",
            "scope": "repo",
            "status": "active",
            "confidence": 0.9,
        }]
        db.apply_cloud_memory_rows.return_value = {
            "memories_applied": 1,
            "tombstones_applied": 0,
        }

        response = mock.MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "pulled": [{
                "memory_id": "m2",
                "repo": "org/repo",
                "type": "learned_pattern",
                "content": "pattern",
                "scope": "repo",
                "status": "active",
                "confidence": 0.8,
            }],
            "memories_pulled": 1,
            "memories_pushed": 1,
            "last_cloud_pull_at": "2026-07-04T12:00:00+00:00",
        }

        mock_client = mock.AsyncMock()
        mock_client.post.return_value = response
        mock_cm = mock.AsyncMock()
        mock_cm.__aenter__.return_value = mock_client

        with mock.patch("turingmind_mcp.cloud_memory_client.httpx.AsyncClient", return_value=mock_cm):
            stats, warning = await sync_memories_via_cloud_api(
                db,
                "org/repo",
                api_url="https://api.example.com",
                api_key="tmk_test",
            )

        assert warning is None
        assert stats["memories_pulled"] == 1
        assert stats["memories_applied"] == 1
        db.set_repo_sync_state.assert_called_once()
