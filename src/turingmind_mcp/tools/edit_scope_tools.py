"""MCP tools: declare / get / check edit-scope contracts."""

from __future__ import annotations

import json

from mcp.types import TextContent

from turingmind_mcp.edit_scope import (
    build_scope_payload,
    check_against_store,
    declare_scope,
    get_scope,
    seed_scope_from_prompt,
)

from .context import ToolContext


def register(registry: dict) -> None:
    registry["turingmind_declare_edit_scope"] = handle_declare_edit_scope
    registry["turingmind_get_edit_scope"] = handle_get_edit_scope
    registry["turingmind_check_edit_scope"] = handle_check_edit_scope


def _json(payload: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, indent=2))]


async def handle_declare_edit_scope(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo") or ""
    if not repo:
        return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        if arguments.get("prompt") and not (
            arguments.get("prefixes")
            or arguments.get("files")
            or arguments.get("modules")
            or arguments.get("paths")
        ):
            payload = seed_scope_from_prompt(
                repo=repo,
                prompt=arguments.get("prompt") or "",
                attachments=arguments.get("attachments") or [],
                conversation_id=arguments.get("conversation_id"),
            )
            if arguments.get("status"):
                payload["status"] = arguments["status"]
        else:
            payload = build_scope_payload(
                repo=repo,
                status=arguments.get("status") or "declared",
                intent=arguments.get("intent") or "",
                prefixes=arguments.get("prefixes"),
                modules=arguments.get("modules"),
                files=arguments.get("files"),
                paths=arguments.get("paths"),
                source=arguments.get("source") or "mcp",
                conversation_id=arguments.get("conversation_id"),
            )
        stored = declare_scope(db, payload)
        return _json({"status": "ok", "scope": stored})
    except Exception as e:
        ctx.logger.exception("declare_edit_scope failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_get_edit_scope(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo") or ""
    if not repo:
        return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        scope = get_scope(
            ctx.get_db(),
            repo=repo,
            conversation_id=arguments.get("conversation_id"),
        )
        return _json({"repo": repo, "scope": scope})
    except Exception as e:
        ctx.logger.exception("get_edit_scope failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_check_edit_scope(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo") or ""
    files = arguments.get("files") or []
    if isinstance(files, str):
        files = [f.strip() for f in files.split(",") if f.strip()]
    if not repo:
        return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        result = check_against_store(
            ctx.get_db(),
            repo=repo,
            files=files,
            conversation_id=arguments.get("conversation_id"),
            cluster_type=arguments.get("cluster_type"),
            cluster_severity=arguments.get("cluster_severity"),
            ensure_provisional=bool(arguments.get("ensure_provisional", False)),
        )
        return _json(result)
    except Exception as e:
        ctx.logger.exception("check_edit_scope failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]
