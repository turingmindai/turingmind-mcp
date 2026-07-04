"""Tests for turingmind_sync_cloud handler routing."""

from __future__ import annotations

import json
import os
from unittest import mock

import pytest

from turingmind_mcp.v2_engine.handlers import handle_sync_cloud
from turingmind_mcp.tools.context import ToolContext


@pytest.fixture
def ctx():
    import logging
    return ToolContext(
        client=mock.MagicMock(),
        api_url="",
        headers={},
        logger=logging.getLogger("test"),
        save_api_key=lambda url, key: "",
        version="1.0",
        get_db=mock.MagicMock(),
        get_config=lambda: ("", ""),
    )


@pytest.mark.asyncio
class TestHandleSyncCloud:
    async def test_requires_repo(self, ctx):
        result = await handle_sync_cloud({}, ctx)
        assert "repo is required" in result[0].text

    async def test_errors_when_no_cloud_config(self, ctx):
        env = {k: v for k, v in os.environ.items() if k != "TURINGMIND_CLOUD_SYNC"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("turingmind_mcp.server.get_config", return_value=("", "")):
                with mock.patch("turingmind_mcp.server.get_db", return_value=mock.MagicMock()):
                    with mock.patch(
                        "turingmind_mcp.v2_engine.database.get_all_spec_nodes",
                        return_value=[],
                    ):
                        with mock.patch(
                            "turingmind_mcp.v2_engine.database.get_execution_state",
                            return_value=None,
                        ):
                            result = await handle_sync_cloud({"repo": "org/repo"}, ctx)
        assert "Cloud memory sync unavailable" in result[0].text

    async def test_cloud_api_path_success(self, ctx):
        with mock.patch.dict(os.environ, {"TURINGMIND_CLOUD_SYNC": "1"}, clear=False):
            with mock.patch(
                "turingmind_mcp.server.get_config",
                return_value=("https://api.example.com", "tmk_test"),
            ):
                with mock.patch("turingmind_mcp.server.get_db", return_value=mock.MagicMock()):
                    with mock.patch(
                        "turingmind_mcp.v2_engine.database.get_all_spec_nodes",
                        return_value=[],
                    ):
                        with mock.patch(
                            "turingmind_mcp.v2_engine.database.get_execution_state",
                            return_value=None,
                        ):
                            with mock.patch(
                                "turingmind_mcp.cloud_memory_client.sync_memories_via_cloud_api",
                                new=mock.AsyncMock(
                                    return_value=(
                                        {
                                            "memories_pushed": 1,
                                            "memories_pulled": 0,
                                            "memories_applied": 0,
                                            "tombstones_applied": 0,
                                        },
                                        None,
                                    )
                                ),
                            ):
                                result = await handle_sync_cloud({"repo": "org/repo"}, ctx)

        payload = json.loads(result[0].text)
        assert payload.get("memory_synced") is True
        assert payload.get("memories_pushed") == 1
