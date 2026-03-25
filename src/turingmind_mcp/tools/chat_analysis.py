"""Chat analysis tools: store_chat_analysis_plan, get_chat_analysis_plans, enhance_chat_analysis."""

from __future__ import annotations

import hashlib
import json
import time

from mcp.types import TextContent

from .context import ToolContext


def register(registry: dict) -> None:
    registry["turingmind_store_chat_analysis_plan"] = handle_store_chat_analysis_plan
    registry["turingmind_get_chat_analysis_plans"] = handle_get_chat_analysis_plans
    registry["turingmind_enhance_chat_analysis"] = handle_enhance_chat_analysis


async def handle_store_chat_analysis_plan(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    composer_id = arguments.get("composer_id", "")
    thread_name = arguments.get("thread_name")
    metadata = arguments.get("metadata")
    summary = arguments.get("summary")
    created_at = arguments.get("created_at")
    if not repo or not composer_id:
        return [
            TextContent(
                type="text",
                text="❌ **Missing required fields:** `repo`, `composer_id`",
            )
        ]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        plan_id = (
            f"CAP-{int(time.time() * 1000)}-"
            f"{hashlib.md5(f'{repo}{composer_id}'.encode()).hexdigest()[:8]}"
        )
        result = db.store_chat_analysis_plan(
            plan_id=plan_id,
            repo=repo,
            composer_id=composer_id,
            thread_name=thread_name,
            metadata=metadata,
            summary=summary,
            created_at=created_at,
        )
        if metadata:
            result["metadata_stats"] = {
                "user_prompts": len(metadata.get("userPrompts", [])),
                "reasoning_blocks": len(metadata.get("reasoning", [])),
                "assistant_responses": len(metadata.get("assistantResponses", [])),
            }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        ctx.logger.exception("Store chat analysis plan failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_get_chat_analysis_plans(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    composer_id = arguments.get("composer_id")
    limit = arguments.get("limit", 50)
    offset = arguments.get("offset", 0)
    if not repo:
        return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        result = db.get_chat_analysis_plans(
            repo=repo,
            composer_id=composer_id,
            limit=limit,
            offset=offset,
        )
        return [
            TextContent(type="text", text=json.dumps(result, indent=2, default=str))
        ]
    except Exception as e:
        ctx.logger.exception("Get chat analysis plans failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_enhance_chat_analysis(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    user_prompts = arguments.get("user_prompts", [])
    assistant_responses = arguments.get("assistant_responses", [])
    files_discussed = arguments.get("files_discussed", [])
    ai_todos = arguments.get("ai_todos", [])
    reasoning = arguments.get("reasoning")
    previous_summary = arguments.get("previous_summary")
    file_diffs = arguments.get("file_diffs", [])
    rolling_context = arguments.get("rolling_context", [])
    active_tasks = arguments.get("active_tasks", [])
    if not user_prompts or not assistant_responses:
        return [
            TextContent(
                type="text",
                text="❌ **Missing required fields:** `user_prompts` and `assistant_responses`",
            )
        ]
    agent = ctx.get_chat_analysis_agent() if ctx.get_chat_analysis_agent else None
    if not agent:
        return [
            TextContent(
                type="text",
                text=(
                    "❌ **ChatAnalysisAgent not available.** "
                    "Check that Azure OpenAI is configured "
                    "(AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT_NAME)"
                ),
            )
        ]
    try:
        inputs = {
            "user_prompts": user_prompts,
            "assistant_responses": assistant_responses,
            "files_discussed": files_discussed,
            "ai_todos": ai_todos,
            "reasoning": reasoning,
            "previous_summary": previous_summary,
            "file_diffs": file_diffs,
            "rolling_context": rolling_context,
            "active_tasks": active_tasks,
        }
        ctx.logger.info(
            f"Enhancing chat analysis: {len(user_prompts)} prompts, "
            f"{len(assistant_responses)} responses, {len(file_diffs)} file diffs, "
            f"{len(rolling_context)} rolling context, {len(active_tasks)} active tasks"
        )
        result = await agent.execute(
            inputs=inputs,
            call_type="enhanceChatAnalysis",
            tags=["chat-analysis", "enhancement"],
            extra_metadata={
                "is_incremental": previous_summary is not None,
                "prompt_count": len(user_prompts),
                "response_count": len(assistant_responses),
                "files_count": len(files_discussed),
                "todos_count": len(ai_todos),
                "diffs_count": len(file_diffs),
                "diffs_total_size": sum(d.get("size", 0) for d in file_diffs),
                "rolling_context_count": len(rolling_context),
                "active_tasks_count": len(active_tasks),
            },
        )
        return [
            TextContent(type="text", text=json.dumps(result, indent=2, default=str))
        ]
    except Exception as e:
        ctx.logger.exception("Chat analysis enhancement failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]
