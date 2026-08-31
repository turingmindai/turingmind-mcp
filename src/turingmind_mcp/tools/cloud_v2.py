"""HTTP MCP tools that call existing cloud / local ``/api/v2`` endpoints.

Lane C: do not open Mongo or SQLite. ``server.py`` local-handler path sets
``ctx.client = None``, so handlers open their own ``httpx.AsyncClient`` when
needed (same idea as ``cloud_memory_client``).
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional
from urllib.parse import quote

import httpx
from mcp.types import TextContent

from .context import ToolContext

CHECK_SPECNODE_NAMES = (
    "check_specnode_contract",
    "turingmind_check_specnode_contract",
)
QUERY_MEMORY_NAMES = (
    "query_repo_memory",
    "turingmind_query_repo_memory",
)


def register(registry: dict) -> None:
    """Register HTTP /api/v2 wrappers (canonical names + turingmind_ aliases)."""
    for name in CHECK_SPECNODE_NAMES:
        registry[name] = handle_check_specnode_contract
    for name in QUERY_MEMORY_NAMES:
        registry[name] = handle_query_repo_memory


def _api_base(ctx: ToolContext) -> str:
    """Resolve TURINGMIND_API_URL, context url, or local v2 server."""
    for candidate in (
        getattr(ctx, "api_url", None),
        os.environ.get("TURINGMIND_API_URL"),
        os.environ.get("TURINGMIND_LOCAL_API_URL"),
    ):
        if candidate and str(candidate).strip():
            return str(candidate).rstrip("/")
    return "http://127.0.0.1:8477"


def _headers(ctx: ToolContext) -> dict[str, str]:
    """Reuse context headers; add Bearer from TURINGMIND_API_KEY if missing."""
    headers = dict(getattr(ctx, "headers", None) or {})
    if "Authorization" not in headers and "authorization" not in headers:
        api_key = os.environ.get("TURINGMIND_API_KEY", "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    headers.setdefault("Accept", "application/json")
    return headers


def _ok(data: dict[str, Any]) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, default=str))]


def _err(msg: str, **extra: Any) -> list[TextContent]:
    payload = {"error": msg, **extra}
    return [TextContent(type="text", text=json.dumps(payload))]


def _invariant_texts(node: dict[str, Any]) -> list[str]:
    contract = node.get("contract") or {}
    raw = contract.get("invariants") or []
    if isinstance(raw, str):
        raw = [raw]
    texts: list[str] = []
    if not isinstance(raw, list):
        return texts
    for item in raw:
        if isinstance(item, str) and item.strip():
            texts.append(item.strip())
        elif isinstance(item, dict):
            text = item.get("text") or item.get("rule") or item.get("invariant")
            if text and str(text).strip():
                texts.append(str(text).strip())
    return texts


async def _http_get(
    ctx: ToolContext,
    path: str,
    params: Optional[dict[str, Any]] = None,
) -> httpx.Response:
    """GET ``path`` on the configured /api/v2 host."""
    url = f"{_api_base(ctx)}{path}"
    headers = _headers(ctx)
    filtered = {k: v for k, v in (params or {}).items() if v is not None}
    client = getattr(ctx, "client", None)
    if client is not None:
        return await client.get(url, params=filtered or None, headers=headers)
    async with httpx.AsyncClient(timeout=30.0) as http:
        return await http.get(url, params=filtered or None, headers=headers)


async def handle_check_specnode_contract(
    arguments: dict,
    ctx: ToolContext,
) -> list[TextContent]:
    """GET ``/api/v2/graph/nodes/{node_id}`` and report contract invariants."""
    repo = (arguments.get("repo") or "").strip()
    node_id = (arguments.get("node_id") or "").strip()
    if not repo:
        return _err("repo is required")
    if not node_id:
        return _err("node_id is required")

    path = f"/api/v2/graph/nodes/{quote(node_id, safe='')}"
    try:
        response = await _http_get(ctx, path, {"repo": repo})
    except Exception as exc:
        ctx.logger.warning("check_specnode_contract HTTP error: %s", exc)
        return _err(f"HTTP request failed: {exc}")

    if response.status_code == 404:
        return _err(f"SpecNode `{node_id}` not found", status=404, repo=repo)
    if response.status_code in (401, 403):
        return _err(
            "Not authorized to read SpecNode contract",
            status=response.status_code,
        )
    if response.status_code >= 400:
        return _err(
            f"GET /api/v2/graph/nodes failed ({response.status_code})",
            detail=response.text[:400],
            status=response.status_code,
        )

    try:
        node = response.json()
    except Exception:
        return _err("Response was not JSON")
    if not isinstance(node, dict):
        return _err("Unexpected graph node payload")

    invariants = _invariant_texts(node)
    return _ok({
        "ok": bool(invariants),
        "repo": repo,
        "node_id": node.get("node_id") or node_id,
        "title": node.get("title"),
        "level": node.get("level"),
        "contract": node.get("contract") or {},
        "invariants": invariants,
        "gaps": [] if invariants else ["missing_invariants"],
    })


async def handle_query_repo_memory(
    arguments: dict,
    ctx: ToolContext,
) -> list[TextContent]:
    """GET ``/api/v2/memory`` — pass-through recall, no local store."""
    repo = (arguments.get("repo") or "").strip()
    if not repo:
        return _err("repo is required")

    search = arguments.get("search")
    mem_type = arguments.get("type") or arguments.get("category")
    limit = arguments.get("limit", 10)
    try:
        response = await _http_get(
            ctx,
            "/api/v2/memory",
            {
                "repo": repo,
                "search": search,
                "type": mem_type,
                "limit": limit,
            },
        )
    except Exception as exc:
        ctx.logger.warning("query_repo_memory HTTP error: %s", exc)
        return _err(f"HTTP request failed: {exc}")

    if response.status_code in (401, 403):
        return _err(
            "Not authorized to query repo memory",
            status=response.status_code,
        )
    if response.status_code >= 400:
        return _err(
            f"GET /api/v2/memory failed ({response.status_code})",
            detail=response.text[:400],
            status=response.status_code,
        )

    try:
        body = response.json()
    except Exception:
        return _err("Response was not JSON")
    if not isinstance(body, dict):
        return _err("Unexpected memory payload")
    return _ok({
        "repo": body.get("repo", repo),
        "entries": body.get("entries") or [],
        "count": body.get("count", len(body.get("entries") or [])),
    })
