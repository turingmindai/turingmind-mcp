"""Edit tools: get_edit_reasoning, apply_edit, log_reasoning."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

from mcp.types import TextContent

from .context import ToolContext


def register(registry: dict) -> None:
    registry["turingmind_get_edit_reasoning"] = handle_get_edit_reasoning
    registry["turingmind_apply_edit"] = handle_apply_edit
    registry["turingmind_log_reasoning"] = handle_log_reasoning


async def handle_get_edit_reasoning(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    files = arguments.get("files", [])
    if not repo or not files:
        return [
            TextContent(type="text", text="❌ **Missing required fields:** `repo`, `files`")
        ]
    commit_message = arguments.get("commit_message", "")
    commit_hash = arguments.get("commit_hash")
    if not ctx.get_db or not ctx.get_memory_manager:
        return [TextContent(type="text", text="❌ **Database/memory manager not available**")]
    try:
        db = ctx.get_db()
        memory_manager = ctx.get_memory_manager()
        overall_intent = None
        if commit_message and "Why:" in commit_message:
            overall_intent = commit_message.split("Why:")[1].strip().split("\n")[0]
        file_reasoning_map = {}
        for file_obj in files:
            file_path = file_obj.get("file_path")
            reasoning = file_obj.get("reasoning") or overall_intent
            if reasoning:
                file_reasoning_map[file_path] = {
                    "reasoning": reasoning,
                    "change_type": file_obj.get("change_type", "other"),
                    "memory_category": file_obj.get("memory_category", "session_context"),
                    "scope": file_obj.get("scope", file_path),
                    "confidence": file_obj.get("confidence", 0.8),
                }
                memory_manager.create_session_context(
                    repo=repo,
                    content=reasoning,
                    scope=file_path,
                    evidence=[
                        {
                            "type": "commit" if commit_hash else "conversation",
                            "content": commit_message or f"File edit: {file_path}",
                            "file": file_path,
                        }
                    ],
                )
        if commit_hash:
            db.save_edit_reasoning(
                repo=repo,
                files=list(file_reasoning_map.values()),
                commit_hash=commit_hash,
                overall_reasoning=overall_intent,
            )
        return [
            TextContent(
                type="text",
                text=(
                    f"💡 **Edit Reasoning Captured**\n\n"
                    f"- **Repository:** {repo}\n"
                    f"- **Files:** {len(file_reasoning_map)}\n"
                    f"- **Overall intent:** {overall_intent or 'Not specified'}\n\n"
                    + "\n".join(
                        f"- `{fp}`: {data['reasoning'][:50]}..."
                        for fp, data in list(file_reasoning_map.items())[:10]
                    )
                ),
            )
        ]
    except Exception as e:
        ctx.logger.exception("Get edit reasoning failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_apply_edit(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    reasoning = arguments.get("reasoning", "")
    file_path = arguments.get("file_path", "")
    edit_type = arguments.get("edit_type", "")
    problem_observed = arguments.get("problem_observed", "")
    approach = arguments.get("approach", "")
    alternatives = arguments.get("alternatives_considered", [])
    old_content = arguments.get("old_content", "")
    new_content = arguments.get("new_content", "")
    full_content = arguments.get("full_content", "")
    repo = arguments.get("repo", "")
    if not reasoning:
        return [
            TextContent(
                type="text",
                text="❌ **Missing required field:** `reasoning`\n\nYou MUST explain WHY you are making this change.",
            )
        ]
    if not file_path or not edit_type:
        return [
            TextContent(type="text", text="❌ **Missing required fields:** `file_path`, `edit_type`")
        ]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        edit_id = f"RE-{uuid.uuid4().hex[:8]}"
        session_id = f"session_{int(datetime.now().timestamp())}"
        db.store_reasoned_edit(
            edit_id=edit_id,
            reasoning=reasoning,
            file_path=file_path,
            edit_type=edit_type,
            repo=repo or "local/unknown",
            session_id=session_id,
            problem_observed=problem_observed,
            approach=approach,
            alternatives_considered=alternatives,
            old_content=old_content,
            new_content=new_content or full_content,
        )
        applied = False
        error_msg = None
        try:
            resolved_path = file_path
            if repo and not os.path.isabs(file_path) and ctx.get_repo_path:
                repo_path = ctx.get_repo_path()
                if repo_path:
                    resolved_path = os.path.join(repo_path, file_path)
            if edit_type == "create":
                os.makedirs(os.path.dirname(resolved_path), exist_ok=True)
                with open(resolved_path, "w") as f:
                    f.write(full_content or new_content or "")
                applied = True
            elif edit_type == "modify":
                if old_content and new_content:
                    with open(resolved_path, "r") as f:
                        content = f.read()
                    if old_content in content:
                        content = content.replace(old_content, new_content, 1)
                        with open(resolved_path, "w") as f:
                            f.write(content)
                        applied = True
                    else:
                        error_msg = "old_content not found in file"
                elif full_content:
                    with open(resolved_path, "w") as f:
                        f.write(full_content)
                    applied = True
                else:
                    error_msg = "Need old_content+new_content or full_content for modify"
            elif edit_type == "delete":
                if os.path.exists(resolved_path):
                    os.remove(resolved_path)
                    applied = True
                else:
                    error_msg = "File does not exist"
            if applied:
                db.mark_edit_applied(edit_id)
        except Exception as e:
            error_msg = str(e)
        result = {
            "edit_id": edit_id,
            "status": "applied" if applied else "stored",
            "reasoning_captured": True,
            "reasoning": reasoning[:100] + "..." if len(reasoning) > 100 else reasoning,
            "file_path": file_path,
            "edit_type": edit_type,
        }
        if problem_observed:
            result["problem_observed"] = problem_observed[:100] + "..."
        if approach:
            result["approach"] = approach[:100] + "..."
        if error_msg:
            result["error"] = error_msg
            result["note"] = "Edit stored but not applied. You may need to apply manually."
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        ctx.logger.exception("Apply edit failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_log_reasoning(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    reasoning_type = arguments.get("reasoning_type", "")
    content = arguments.get("content", "")
    context = arguments.get("context", "")
    related_files = arguments.get("related_files", [])
    confidence = arguments.get("confidence")
    repo = arguments.get("repo", "")
    session_id = arguments.get("session_id", "")
    if not reasoning_type or not content:
        return [
            TextContent(
                type="text",
                text="❌ **Missing required fields:** `reasoning_type`, `content`",
            )
        ]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        log_id = f"RL-{uuid.uuid4().hex[:8]}"
        if not session_id:
            session_id = f"session_{int(datetime.now().timestamp())}"
        result = db.log_reasoning(
            log_id=log_id,
            reasoning_type=reasoning_type,
            content=content,
            repo=repo,
            session_id=session_id,
            context=context,
            related_files=related_files,
            confidence=confidence,
        )
        result["message"] = "Reasoning logged successfully"
        result["content_preview"] = content[:100] + "..." if len(content) > 100 else content
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        ctx.logger.exception("Log reasoning failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]
