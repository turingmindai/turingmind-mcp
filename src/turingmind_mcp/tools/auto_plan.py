"""Auto-plan tools: analyze_diff, store_auto_plan, get_auto_plans, bundle_plans_for_commit."""

from __future__ import annotations

import hashlib
import json

from mcp.types import TextContent

from .context import ToolContext


def register(registry: dict) -> None:
    registry["turingmind_analyze_diff"] = handle_analyze_diff
    registry["turingmind_store_auto_plan"] = handle_store_auto_plan
    registry["turingmind_get_auto_plans"] = handle_get_auto_plans
    registry["turingmind_bundle_plans_for_commit"] = handle_bundle_plans_for_commit


async def handle_analyze_diff(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    file_path = arguments.get("file_path", "")
    diff = arguments.get("diff", "")
    context = arguments.get("context", "")
    commit_message = arguments.get("commit_message", "")
    if not repo or not file_path or not diff:
        return [
            TextContent(
                type="text",
                text="❌ **Missing required fields:** `repo`, `file_path`, `diff`",
            )
        ]
    try:
        diff_hash = hashlib.sha256(diff.encode()).hexdigest()[:16]
        lines = diff.split("\n")
        additions = [l[1:] for l in lines if l.startswith("+") and not l.startswith("+++")]
        deletions = [l[1:] for l in lines if l.startswith("-") and not l.startswith("---")]
        changes_summary = []
        risk_level = "low"
        confidence = 0.7
        add_text = " ".join(additions).lower()
        del_text = " ".join(deletions).lower()
        all_text = add_text + " " + del_text
        security_keywords = [
            "password", "secret", "token", "auth", "crypto", "encrypt", "hash",
            "api_key", "credential",
        ]
        for kw in security_keywords:
            if kw in all_text:
                risk_level = "high"
                changes_summary.append(f"Security-related change ({kw})")
                confidence = 0.8
                break
        if "try" in add_text or "catch" in add_text or "except" in add_text:
            changes_summary.append("Added error handling")
        if "null" in add_text or "undefined" in add_text or "None" in add_text:
            changes_summary.append("Added null/undefined checks")
        if "def " in add_text or "function " in add_text or "async " in add_text:
            changes_summary.append("Added new function(s)")
        if "import " in add_text or "require(" in add_text:
            changes_summary.append("Modified imports")
        if len(deletions) > len(additions) * 2:
            changes_summary.append("Removed code (cleanup/refactoring)")
        elif len(additions) > len(deletions) * 2:
            changes_summary.append("Added significant new code")
        if not changes_summary:
            changes_summary.append(
                f"Modified {len(additions)} lines, removed {len(deletions)} lines"
            )
        if commit_message:
            intent = commit_message
            confidence = 0.9
        else:
            intent_parts = []
            if "Security-related" in str(changes_summary):
                intent_parts.append("Security update")
            if "error handling" in str(changes_summary).lower():
                intent_parts.append("Improved error handling")
            if "null" in str(changes_summary).lower():
                intent_parts.append("Added safety checks")
            if "function" in str(changes_summary).lower():
                intent_parts.append("Added new functionality")
            if "refactoring" in str(changes_summary).lower():
                intent_parts.append("Code cleanup")
            if intent_parts:
                intent = " + ".join(intent_parts) + f" in {file_path.split('/')[-1]}"
            else:
                intent = f"Modified {file_path.split('/')[-1]}"
        suggested_specs = []
        if "error handling" in str(changes_summary).lower():
            suggested_specs.append(
                "Given an error condition, the system should handle it gracefully"
            )
        if "null" in str(changes_summary).lower():
            suggested_specs.append(
                "Given null/undefined input, the system should not crash"
            )
        if "Security" in str(changes_summary):
            suggested_specs.append(
                "Given sensitive data, it should be properly protected"
            )
        result = {
            "status": "analyzed",
            "auto_plan_id": f"AP-{diff_hash}",
            "repo": repo,
            "file_path": file_path,
            "diff_hash": diff_hash,
            "intent": intent,
            "changes_summary": changes_summary,
            "risk_level": risk_level,
            "confidence": confidence,
            "suggested_specs": suggested_specs,
            "stats": {"lines_added": len(additions), "lines_removed": len(deletions)},
            "next_action": "Call turingmind_store_auto_plan to save this plan",
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        ctx.logger.exception("Analyze diff failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_store_auto_plan(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    file_path = arguments.get("file_path", "")
    intent = arguments.get("intent", "")
    diff_hash = arguments.get("diff_hash", "")
    risk_level = arguments.get("risk_level", "low")
    changes_summary = arguments.get("changes_summary", [])
    suggested_specs = arguments.get("suggested_specs", [])
    confidence = arguments.get("confidence", 0.5)
    if not repo or not file_path or not intent or not diff_hash:
        return [
            TextContent(
                type="text",
                text="❌ **Missing required fields:** `repo`, `file_path`, `intent`, `diff_hash`",
            )
        ]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        auto_plan_id = f"AP-{diff_hash}"
        result = db.store_auto_plan(
            auto_plan_id=auto_plan_id,
            repo=repo,
            file_path=file_path,
            diff_hash=diff_hash,
            intent=intent,
            changes_summary=changes_summary,
            suggested_specs=suggested_specs,
            risk_level=risk_level,
            confidence=confidence,
        )
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        ctx.logger.exception("Store auto plan failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_get_auto_plans(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    file_path = arguments.get("file_path")
    since = arguments.get("since")
    uncommitted_only = arguments.get("uncommitted_only", True)
    if not repo:
        return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        plans = db.get_auto_plans(
            repo=repo,
            file_path=file_path,
            since=since,
            uncommitted_only=uncommitted_only,
        )
        result = {
            "repo": repo,
            "count": len(plans),
            "uncommitted_only": uncommitted_only,
            "plans": plans,
        }
        if len(plans) > 0:
            result["summary"] = {
                "files_affected": list(set(p["file_path"] for p in plans)),
                "risk_levels": {
                    "critical": sum(1 for p in plans if p.get("risk_level") == "critical"),
                    "high": sum(1 for p in plans if p.get("risk_level") == "high"),
                    "medium": sum(1 for p in plans if p.get("risk_level") == "medium"),
                    "low": sum(1 for p in plans if p.get("risk_level") == "low"),
                },
            }
        return [
            TextContent(type="text", text=json.dumps(result, indent=2, default=str))
        ]
    except Exception as e:
        ctx.logger.exception("Get auto plans failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_bundle_plans_for_commit(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    commit_sha = arguments.get("commit_sha", "")
    commit_message = arguments.get("commit_message", "")
    files = arguments.get("files", [])
    if not repo or not commit_sha or not files:
        return [
            TextContent(
                type="text",
                text="❌ **Missing required fields:** `repo`, `commit_sha`, `files`",
            )
        ]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        result = db.bundle_auto_plans_for_commit(
            repo=repo,
            commit_sha=commit_sha,
            files=files,
        )
        if commit_message:
            result["commit_message"] = commit_message
        return [
            TextContent(type="text", text=json.dumps(result, indent=2, default=str))
        ]
    except Exception as e:
        ctx.logger.exception("Bundle plans for commit failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]
