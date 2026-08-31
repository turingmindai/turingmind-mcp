"""MCP change-risk tools HTTP-call /api/v2 and do not reimplement the engine."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from turingmind_mcp.tools.change_risk import (
    handle_evaluate_change_risk,
    handle_record_change_event,
    register,
)
from turingmind_mcp.tools.context import ToolContext


class FakeResponse:
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if not isinstance(payload, str) else payload

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


class FakeClient:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.response


def _ctx(client: FakeClient, **kwargs: Any) -> ToolContext:
    defaults: dict[str, Any] = {
        "client": client,
        "api_url": "http://test",
        "headers": {"Authorization": "Bearer test"},
        "logger": logging.getLogger("test_change_risk_mcp"),
        "save_api_key": lambda *_a, **_k: "",
        "version": "test",
    }
    defaults.update(kwargs)
    return ToolContext(**defaults)


@pytest.mark.asyncio
async def test_evaluate_posts_to_v2():
    client = FakeClient(
        FakeResponse(
            200,
            {
                "advisory": True,
                "certificate": {
                    "expected_loss_usd": 20.0,
                    "incident_probability": 0.004,
                },
            },
        )
    )
    listed = await handle_evaluate_change_risk(
        {"repo": "acme/pay", "diff": "+++ b/requirements.txt\n", "env": "dev"},
        _ctx(client),
    )
    body = json.loads(listed[0].text)
    assert body["advisory"] is True
    assert client.calls[0]["url"] == "http://test/api/v2/change-risk/evaluate"
    assert client.calls[0]["json"]["repo"] == "acme/pay"
    assert client.calls[0]["json"]["environment"] == "dev"


@pytest.mark.asyncio
async def test_evaluate_requires_repo():
    client = FakeClient(FakeResponse(200, {}))
    listed = await handle_evaluate_change_risk({}, _ctx(client))
    body = json.loads(listed[0].text)
    assert "error" in body
    assert client.calls == []


@pytest.mark.asyncio
async def test_record_change_event_posts_to_v2():
    client = FakeClient(FakeResponse(200, {"status": "recorded", "change_id": "CHG-1"}))
    listed = await handle_record_change_event(
        {
            "change_id": "CHG-1",
            "repo_id": "acme/pay",
            "agent_id": "mcp",
            "predicted_loss": 20.0,
            "decision": "AUTO_APPROVE",
        },
        _ctx(client),
    )
    body = json.loads(listed[0].text)
    assert body["change_id"] == "CHG-1"
    assert client.calls[0]["url"] == "http://test/api/v2/change-events"
    assert client.calls[0]["json"]["expected_loss_usd"] == 20.0


@pytest.mark.asyncio
async def test_unauthorized():
    client = FakeClient(FakeResponse(401, {"detail": "no"}))
    listed = await handle_evaluate_change_risk({"repo": "acme/pay"}, _ctx(client))
    body = json.loads(listed[0].text)
    assert body["status"] == 401


def test_register_names():
    registry: dict = {}
    register(registry)
    assert "evaluate_change_risk" in registry
    assert "turingmind_evaluate_change_risk" in registry
    assert "record_change_event" in registry


@pytest.mark.asyncio
async def test_evaluate_uses_get_config_key_when_headers_and_env_empty(monkeypatch):
    """Local handler path passes headers={} and often has no process env key."""
    monkeypatch.delenv("TURINGMIND_API_KEY", raising=False)
    client = FakeClient(FakeResponse(200, {"advisory": True}))
    listed = await handle_evaluate_change_risk(
        {"repo": "acme/pay"},
        _ctx(
            client,
            headers={},
            get_config=lambda: ("http://from-config", "tmk_from_config"),
        ),
    )
    body = json.loads(listed[0].text)
    assert body["advisory"] is True
    assert client.calls[0]["headers"]["Authorization"] == "Bearer tmk_from_config"


@pytest.mark.asyncio
async def test_evaluate_env_key_wins_over_get_config(monkeypatch):
    monkeypatch.setenv("TURINGMIND_API_KEY", "tmk_from_env")
    client = FakeClient(FakeResponse(200, {"advisory": True}))
    await handle_evaluate_change_risk(
        {"repo": "acme/pay"},
        _ctx(
            client,
            headers={},
            get_config=lambda: ("http://from-config", "tmk_from_config"),
        ),
    )
    assert client.calls[0]["headers"]["Authorization"] == "Bearer tmk_from_env"
