"""HTTP MCP tools for advisory change-risk. No local engine copy."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from mcp.types import TextContent

from .context import ToolContext

EVALUATE_NAMES = ("evaluate_change_risk", "turingmind_evaluate_change_risk")
RECORD_NAMES = ("record_change_event", "turingmind_record_change_event")


def register(registry: dict) -> None:
    """Register HTTP wrappers. Does not import repochatindex."""
    for name in EVALUATE_NAMES:
        registry[name] = handle_evaluate_change_risk
    for name in RECORD_NAMES:
        registry[name] = handle_record_change_event


def _api_base(ctx: ToolContext) -> str:
    for candidate in (
        getattr(ctx, "api_url", None),
        os.environ.get("TURINGMIND_API_URL"),
        os.environ.get("TURINGMIND_LOCAL_API_URL"),
    ):
        if candidate and str(candidate).strip():
            return str(candidate).rstrip("/")
    return "http://127.0.0.1:8477"


def _headers(ctx: ToolContext) -> dict[str, str]:
    headers = dict(getattr(ctx, "headers", None) or {})
    if "Authorization" not in headers and "authorization" not in headers:
        api_key = os.environ.get("TURINGMIND_API_KEY", "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    headers.setdefault("Accept", "application/json")
    headers.setdefault("Content-Type", "application/json")
    return headers


def _ok(data: dict[str, Any]) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, default=str))]


def _err(msg: str, **extra: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"error": msg, **extra}))]


async def _http_post(
    ctx: ToolContext,
    path: str,
    body: dict[str, Any],
) -> httpx.Response:
    url = f"{_api_base(ctx)}{path}"
    headers = _headers(ctx)
    client = getattr(ctx, "client", None)
    if client is not None:
        return await client.post(url, json=body, headers=headers)
    async with httpx.AsyncClient(timeout=30.0) as http:
        return await http.post(url, json=body, headers=headers)


async def handle_evaluate_change_risk(
    arguments: dict,
    ctx: ToolContext,
) -> list[TextContent]:
    """POST ``/api/v2/change-risk/evaluate``. Server owns the formula."""
    repo = (arguments.get("repo") or arguments.get("repo_id") or "").strip()
    if not repo:
        return _err("repo is required")
    body = {
        "repo": repo,
        "diff": arguments.get("diff") or "",
        "files": arguments.get("files") or [],
        "environment": arguments.get("env") or arguments.get("environment") or "dev",
        "violated_invariants": arguments.get("violated_invariants") or [],
        "persist": bool(arguments.get("persist", False)),
        "agent_id": arguments.get("agent_id") or "mcp",
    }
    try:
        response = await _http_post(ctx, "/api/v2/change-risk/evaluate", body)
    except Exception as exc:
        ctx.logger.warning("evaluate_change_risk HTTP error: %s", exc)
        return _err(f"HTTP request failed: {exc}")
    if response.status_code in (401, 403):
        return _err("Not authorized to evaluate change risk", status=response.status_code)
    if response.status_code >= 400:
        return _err(
            f"POST /api/v2/change-risk/evaluate failed ({response.status_code})",
            detail=response.text[:400],
            status=response.status_code,
        )
    try:
        payload = response.json()
    except Exception:
        return _err("Response was not JSON")
    if not isinstance(payload, dict):
        return _err("Unexpected evaluate payload")
    return _ok(payload)


async def handle_record_change_event(
    arguments: dict,
    ctx: ToolContext,
) -> list[TextContent]:
    """POST ``/api/v2/change-events``."""
    repo = (
        arguments.get("repo")
        or arguments.get("repo_id")
        or arguments.get("repository")
        or ""
    ).strip()
    if not repo:
        return _err("repo is required")
    try:
        predicted = float(
            arguments.get("predicted_loss") or arguments.get("expected_loss_usd") or 0.0
        )
    except (TypeError, ValueError):
        return _err("predicted_loss must be a number")
    body = {
        "change_id": arguments.get("change_id"),
        "repository": repo,
        "agent_id": arguments.get("agent_id") or "mcp",
        "environment": arguments.get("env") or arguments.get("environment") or "dev",
        "expected_loss_usd": predicted,
        "decision": arguments.get("decision") or "REVIEW_REQUIRED",
        "predicted_probability": arguments.get("predicted_probability") or 0.0,
    }
    try:
        response = await _http_post(ctx, "/api/v2/change-events", body)
    except Exception as exc:
        ctx.logger.warning("record_change_event HTTP error: %s", exc)
        return _err(f"HTTP request failed: {exc}")
    if response.status_code in (401, 403):
        return _err("Not authorized to record change event", status=response.status_code)
    if response.status_code >= 400:
        return _err(
            f"POST /api/v2/change-events failed ({response.status_code})",
            detail=response.text[:400],
            status=response.status_code,
        )
    try:
        payload = response.json()
    except Exception:
        return _err("Response was not JSON")
    if not isinstance(payload, dict):
        return _err("Unexpected record payload")
    return _ok(payload)
