"""HTTP /api/v2 MCP tools — handlers only, mocked httpx (no Mongo)."""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from turingmind_mcp.tools.cloud_v2 import (
    handle_check_specnode_contract,
    handle_query_repo_memory,
    register,
)
from turingmind_mcp.tools.context import ToolContext


def _ctx(client) -> ToolContext:
    return ToolContext(
        client=client,
        api_url="https://api.example.com",
        headers={"Authorization": "Bearer tmk_test"},
        logger=logging.getLogger("test_cloud_v2"),
        save_api_key=lambda *_a, **_k: "",
        version="test",
    )


def _json_response(payload: dict, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = json.dumps(payload)
    return response


def test_register_canonical_and_aliased_names():
    registry: dict = {}
    register(registry)
    assert registry["check_specnode_contract"] is handle_check_specnode_contract
    assert registry["turingmind_check_specnode_contract"] is handle_check_specnode_contract
    assert registry["query_repo_memory"] is handle_query_repo_memory
    assert registry["turingmind_query_repo_memory"] is handle_query_repo_memory


@pytest.mark.asyncio
async def test_check_specnode_contract_ok():
    response = _json_response({
        "node_id": "node_transcoder",
        "title": "Audio Transcoder L3",
        "level": "L3_API",
        "contract": {"invariants": ["Zero audio frame dropping"]},
    })
    client = AsyncMock()
    client.get.return_value = response

    result = await handle_check_specnode_contract(
        {"repo": "acme/widget", "node_id": "node_transcoder"},
        _ctx(client),
    )
    payload = json.loads(result[0].text)
    assert payload["ok"] is True
    assert payload["invariants"] == ["Zero audio frame dropping"]
    assert payload["gaps"] == []
    client.get.assert_awaited_once()
    args, kwargs = client.get.await_args
    assert args[0] == "https://api.example.com/api/v2/graph/nodes/node_transcoder"
    assert kwargs["params"]["repo"] == "acme/widget"


@pytest.mark.asyncio
async def test_check_specnode_contract_missing_invariants():
    response = _json_response({
        "node_id": "n1",
        "title": "Bare",
        "contract": {"invariants": []},
    })
    client = AsyncMock()
    client.get.return_value = response

    result = await handle_check_specnode_contract(
        {"repo": "acme/widget", "node_id": "n1"},
        _ctx(client),
    )
    payload = json.loads(result[0].text)
    assert payload["ok"] is False
    assert "missing_invariants" in payload["gaps"]


@pytest.mark.asyncio
async def test_check_specnode_contract_requires_fields():
    client = AsyncMock()
    missing_repo = await handle_check_specnode_contract({"node_id": "n1"}, _ctx(client))
    missing_id = await handle_check_specnode_contract({"repo": "acme/widget"}, _ctx(client))
    assert "repo is required" in missing_repo[0].text
    assert "node_id is required" in missing_id[0].text
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_query_repo_memory_passthrough():
    response = _json_response({
        "repo": "acme/widget",
        "entries": [{"content": "rate must be a decimal", "type": "explicit_rule"}],
        "count": 1,
    })
    client = AsyncMock()
    client.get.return_value = response

    result = await handle_query_repo_memory(
        {"repo": "acme/widget", "search": "rate", "limit": 5},
        _ctx(client),
    )
    payload = json.loads(result[0].text)
    assert payload["count"] == 1
    assert "decimal" in payload["entries"][0]["content"]
    args, kwargs = client.get.await_args
    assert args[0] == "https://api.example.com/api/v2/memory"
    assert kwargs["params"]["search"] == "rate"
    assert kwargs["params"]["limit"] == 5


@pytest.mark.asyncio
async def test_query_repo_memory_requires_repo():
    client = AsyncMock()
    result = await handle_query_repo_memory({}, _ctx(client))
    assert "repo is required" in result[0].text
    client.get.assert_not_called()
