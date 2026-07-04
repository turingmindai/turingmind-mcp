"""Memory tools: list_memory, get_memory, save_memory, delete_memory, detect_conflicts, resolve_conflict, simulate_impact, explain_decision, get_memory_stats."""

from __future__ import annotations

import json

from mcp.types import TextContent

from .context import ToolContext


def register(registry: dict) -> None:
    registry["turingmind_list_memory"] = handle_list_memory
    registry["turingmind_get_memory"] = handle_get_memory
    registry["turingmind_save_memory"] = handle_save_memory
    registry["turingmind_delete_memory"] = handle_delete_memory
    registry["turingmind_detect_conflicts"] = handle_detect_conflicts
    registry["turingmind_resolve_conflict"] = handle_resolve_conflict
    # NOTE: simulate_impact, explain_decision, get_memory_stats remain
    # unregistered — no v2 tool definitions, not exposed to agents.


async def handle_list_memory(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    if not repo:
        return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]
    category = arguments.get("category", "all")
    status = arguments.get("status", "all")
    scope = arguments.get("scope")
    security_tag = arguments.get("security_tag")
    page = arguments.get("page", 1)
    limit = arguments.get("limit", 50)
    search = arguments.get("search")
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        entries = db.list_memory_entries(
            repo=repo,
            memory_type=category if category != "all" else None,
            status=status if status != "all" else None,
            scope=scope,
            page=page,
            limit=limit,
            search=search,
        )
        if security_tag:
            entries = [
                e for e in entries
                if e.get("security_tags") and security_tag in e.get("security_tags", [])
            ]
        # Machine-parseable JSON: agents need memory_id to round-trip into
        # get_memory / save_memory, and full content to act on the entry.
        payload = {
            "total": len(entries),
            "page": page,
            "limit": limit,
            "entries": [
                {
                    "memory_id": e["memory_id"],
                    "type": e["type"],
                    "status": e["status"],
                    "content": e["content"],
                    "scope": e["scope"],
                    "confidence": e["confidence"],
                    "security_tags": e.get("security_tags") or [],
                    "created_at": e.get("created_at"),
                    "updated_at": e.get("updated_at"),
                    "expires_at": e.get("expires_at"),
                }
                for e in entries
            ],
        }
        return [TextContent(type="text", text=json.dumps(payload, indent=2))]
    except Exception as e:
        ctx.logger.exception("List memory failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_get_memory(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    memory_id = arguments.get("memory_id", "")
    if not repo or not memory_id:
        return [
            TextContent(type="text", text="❌ **Missing required fields:** `repo`, `memory_id`")
        ]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        entry = db.get_memory_entry(memory_id)
        if not entry:
            return [
                TextContent(type="text", text=f"❌ **Memory entry not found:** `{memory_id}`")
            ]
        evidence = db.get_evidence(memory_id)
        payload = {
            "memory_id": memory_id,
            "type": entry["type"],
            "status": entry["status"],
            "content": entry["content"],
            "scope": entry["scope"],
            "confidence": entry["confidence"],
            "security_tags": entry.get("security_tags") or [],
            "yaml_definition": entry.get("yaml_definition"),
            "created_at": entry.get("created_at"),
            "updated_at": entry.get("updated_at"),
            "expires_at": entry.get("expires_at"),
            "evidence": [
                {
                    "evidence_type": e["evidence_type"],
                    "content": e["content"],
                    "file_path": e.get("file_path"),
                    "line_number": e.get("line_number"),
                }
                for e in evidence
            ],
        }
        return [TextContent(type="text", text=json.dumps(payload, indent=2))]
    except Exception as e:
        ctx.logger.exception("Get memory failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_save_memory(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    memory_type = arguments.get("type")
    content = arguments.get("content", "")
    scope = arguments.get("scope", "")
    if not repo or not memory_type or not content or not scope:
        return [
            TextContent(
                type="text",
                text="❌ **Missing required fields:** `repo`, `type`, `content`, `scope`",
            )
        ]
    if not ctx.get_db or not ctx.get_memory_manager:
        return [TextContent(type="text", text="❌ **Database/memory manager not available**")]
    try:
        memory_manager = ctx.get_memory_manager()
        memory_id = arguments.get("memory_id")
        db = ctx.get_db()
        if memory_id:
            success = db.update_memory_entry(
                memory_id=memory_id,
                content=content,
                scope=scope,
                confidence=arguments.get("confidence"),
                status=arguments.get("status"),
                security_tags=arguments.get("security_tags"),
                yaml_definition=arguments.get("yaml_definition"),
            )
            if not success:
                return [
                    TextContent(type="text", text=f"❌ **Memory entry not found:** `{memory_id}`")
                ]
        else:
            if memory_type == "explicit_rule":
                result = memory_manager.create_explicit_rule(
                    repo=repo,
                    content=content,
                    scope=scope,
                    yaml_definition=arguments.get("yaml_definition"),
                    security_tags=arguments.get("security_tags"),
                )
                memory_id = result["memory_id"]
            elif memory_type == "session_context":
                memory_id = memory_manager.create_session_context(
                    repo=repo,
                    content=content,
                    scope=scope,
                    evidence=arguments.get("evidence", []),
                )
            else:
                memory_id = db.create_memory_entry(
                    repo=repo,
                    memory_type=memory_type,
                    content=content,
                    scope=scope,
                    confidence=arguments.get("confidence", 0.8),
                    security_tags=arguments.get("security_tags"),
                    yaml_definition=arguments.get("yaml_definition"),
                    node_id=arguments.get("node_id"),
                )
        if arguments.get("evidence"):
            for ev in arguments["evidence"]:
                db.add_evidence(
                    memory_id=memory_id,
                    evidence_type=ev.get("type", "manual"),
                    content=ev.get("content", ""),
                    file_path=ev.get("file"),
                    line_number=ev.get("line"),
                )
        payload = {
            "status": "saved",
            "memory_id": memory_id,
            "type": memory_type,
            "content": content,
            "scope": scope,
        }
        return [TextContent(type="text", text=json.dumps(payload, indent=2))]
    except Exception as e:
        ctx.logger.exception("Save memory failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_delete_memory(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    memory_id = arguments.get("memory_id", "")
    action = arguments.get("action", "deprecate")
    if not repo or not memory_id:
        return [
            TextContent(type="text", text="❌ **Missing required fields:** `repo`, `memory_id`")
        ]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        success = db.delete_memory_entry(memory_id, deprecate=(action == "deprecate"))
        if not success:
            return [
                TextContent(type="text", text=f"❌ **Memory entry not found:** `{memory_id}`")
            ]
        return [
            TextContent(
                type="text",
                text=(
                    f"✅ **Memory Entry {action}d**\n\n"
                    f"- **ID:** {memory_id}\n"
                    f"- **Action:** {action}"
                ),
            )
        ]
    except Exception as e:
        ctx.logger.exception("Delete memory failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_detect_conflicts(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    memory_id = arguments.get("memory_id", "")
    if not repo or not memory_id:
        return [
            TextContent(type="text", text="❌ **Missing required fields:** `repo`, `memory_id`")
        ]
    if not ctx.get_memory_manager:
        return [TextContent(type="text", text="❌ **Memory manager not available**")]
    try:
        memory_manager = ctx.get_memory_manager()
        conflicts = memory_manager.detect_conflicts(repo, memory_id)
        if not conflicts:
            return [TextContent(type="text", text=json.dumps([]))]
        out = [
            {
                "id": c.get("conflict_id", c.get("id", str(i))),
                "type": c.get("type", ""),
                "severity": c.get("severity", ""),
                "description": c.get("description", ""),
            }
            for i, c in enumerate(conflicts)
        ]
        return [TextContent(type="text", text=json.dumps(out, indent=2))]
    except Exception as e:
        ctx.logger.exception("Detect conflicts failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_resolve_conflict(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    conflict_id = arguments.get("conflict_id", "")
    strategy = arguments.get("strategy", "")
    if not repo or not conflict_id or not strategy:
        return [
            TextContent(
                type="text",
                text="❌ **Missing required fields:** `repo`, `conflict_id`, `strategy`",
            )
        ]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        success = db.resolve_conflict(conflict_id, strategy)
        if not success:
            return [
                TextContent(type="text", text=f"❌ **Conflict not found:** `{conflict_id}`")
            ]
        return [
            TextContent(
                type="text",
                text=(
                    f"✅ **Conflict Resolved**\n\n"
                    f"- **Conflict ID:** {conflict_id}\n"
                    f"- **Strategy:** {strategy}"
                ),
            )
        ]
    except Exception as e:
        ctx.logger.exception("Resolve conflict failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_simulate_impact(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    memory_ids = arguments.get("memory_ids", [])
    test_files = arguments.get("test_files")
    if not repo:
        return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]
    impact_obj = {
        "repo": repo,
        "memory_ids": memory_ids,
        "test_files": test_files if test_files else [],
        "simulated": True,
    }
    return [TextContent(type="text", text=json.dumps(impact_obj, indent=2))]


async def handle_explain_decision(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    issue_id = arguments.get("issue_id")
    file_path = arguments.get("file")
    line = arguments.get("line")
    if not repo:
        return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        usage = db.get_memory_usage(
            repo=repo, issue_id=issue_id, file_path=file_path, line_number=line
        )
        if not usage:
            return [
                TextContent(type="text", text="ℹ️ **No memory usage found for this decision**")
            ]
        total_weight = sum(u["weight"] for u in usage)
        return [
            TextContent(
                type="text",
                text=(
                    f"💡 **Decision Explanation**\n\n"
                    f"- **Total influence:** {total_weight:.2f}\n"
                    + "\n".join(
                        f"- **{u['type']}** ({u['weight']*100:.0f}%): {u['content'][:60]}..."
                        for u in usage[:10]
                    )
                ),
            )
        ]
    except Exception as e:
        ctx.logger.exception("Explain decision failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_get_memory_stats(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    if not repo:
        return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        with db.transaction() as cursor:
            cursor.execute(
                """
                SELECT type, status, COUNT(*) as count
                FROM memory_entries
                WHERE repo = ?
                GROUP BY type, status
                """,
                (repo,),
            )
            stats = cursor.fetchall()
        stats_obj = {
            "repo": repo,
            "by_type_status": [
                {"type": row[0], "status": row[1], "count": row[2]}
                for row in stats
            ],
        }
        return [TextContent(type="text", text=json.dumps(stats_obj, indent=2))]
    except Exception as e:
        ctx.logger.exception("Get memory stats failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]
