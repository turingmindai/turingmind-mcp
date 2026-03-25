"""Code review tools: upload_review, get_context, submit_feedback."""

from __future__ import annotations

import json
import re

from mcp.types import TextContent

from ..models import FeedbackAction, ReviewType, SubmitFeedbackInput, UploadReviewInput
from .context import ToolContext


def register(registry: dict) -> None:
    registry["turingmind_upload_review"] = handle_upload_review
    registry["turingmind_get_context"] = handle_get_context
    registry["turingmind_submit_feedback"] = handle_submit_feedback


async def handle_upload_review(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    try:
        review = UploadReviewInput(**arguments)
    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"❌ **Invalid input:** {e}\n\nRequired field: `repo`",
            )
        ]
    if not re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", review.repo):
        return [
            TextContent(
                type="text",
                text=f"❌ **Invalid repo format:** `{review.repo}`\n\nExpected format: `owner/repo`",
            )
        ]
    issues = review.issues or []
    auto_summary = {
        "critical": sum(1 for i in issues if i.get("severity") == "critical"),
        "high": sum(1 for i in issues if i.get("severity") == "high"),
        "medium": sum(1 for i in issues if i.get("severity") == "medium"),
        "low": sum(1 for i in issues if i.get("severity") == "low"),
    }
    body = {
        "context": {
            "repo": review.repo,
            "branch": review.branch,
            "commit": review.commit,
            "review_type": (
                review.review_type.value
                if isinstance(review.review_type, ReviewType)
                else review.review_type
            ),
        },
        "issues": issues,
        "raw_content": review.raw_content,
        "summary": review.summary or auto_summary,
        "files_reviewed": review.files_reviewed or [],
    }
    ctx.logger.info(f"Uploading review for {review.repo} with {len(issues)} issues")
    response = await ctx.client.post(
        f"{ctx.api_url}/api/v1/code-review/reviews",
        headers=ctx.headers,
        json=body,
    )
    if response.status_code in (200, 201):
        data = response.json()
        review_id = data.get("review_id", "unknown")
        try:
            if ctx.get_db and ctx.get_memory_manager:
                db = ctx.get_db()
                active_memories = db.list_memory_entries(repo=review.repo, status="active")
                for issue in issues:
                    issue_file = issue.get("file", "")
                    issue_line = issue.get("line")
                    influencing_memories = []
                    for memory in active_memories:
                        scope = memory.get("scope", "")
                        if scope == "repo" or scope == issue_file or issue_file.startswith(scope):
                            weight = memory.get("confidence", 0.8) * 0.5
                            if memory.get("type") == "explicit_rule":
                                weight = memory.get("confidence", 1.0) * 0.8
                            elif memory.get("type") == "learned_pattern":
                                weight = memory.get("confidence", 0.7) * 0.3
                            elif memory.get("type") == "session_context":
                                weight = memory.get("confidence", 0.8) * 0.4
                            if weight > 0.1:
                                influencing_memories.append({
                                    "memory_id": memory["memory_id"],
                                    "weight": weight,
                                })
                    for mem in influencing_memories:
                        db.track_memory_usage(
                            repo=review.repo,
                            memory_id=mem["memory_id"],
                            context="code_review",
                            weight=mem["weight"],
                            issue_id=review_id,
                            file_path=issue_file,
                            line_number=issue_line,
                        )
        except Exception as e:
            ctx.logger.warning(f"Failed to track memory usage: {e}")
        review_obj = {
            "review_id": review_id,
            "repo": review.repo,
            "branch": review.branch,
            "commit": review.commit,
            "issues_count": len(issues),
            "summary": auto_summary,
        }
        return [TextContent(type="text", text=json.dumps(review_obj, indent=2))]
    if response.status_code == 403:
        return [
            TextContent(
                type="text",
                text=(
                    "❌ **Permission Denied**\n\n"
                    "API key lacks `code_review:write` permission.\n"
                    "Run `/tmind:login` to create a new key with proper permissions."
                ),
            )
        ]
    if response.status_code == 422:
        return [
            TextContent(
                type="text",
                text=(
                    f"❌ **Validation Error**\n\n"
                    f"Request body failed validation:\n```\n{response.text[:500]}\n```"
                ),
            )
        ]
    return [
        TextContent(
            type="text",
            text=f"❌ **Upload failed:** HTTP {response.status_code}\n{response.text[:200]}",
        )
    ]


async def handle_get_context(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    if not repo:
        return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]
    if not re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", repo):
        return [
            TextContent(
                type="text",
                text=f"❌ **Invalid repo format:** `{repo}`\n\nExpected format: `owner/repo`",
            )
        ]
    ctx.logger.info(f"Fetching context for {repo}")
    response = await ctx.client.get(
        f"{ctx.api_url}/api/v1/code-review/context/{repo}",
        headers=ctx.headers,
    )
    if response.status_code == 200:
        data = response.json()
        open_issues = data.get("recent_open_issues", [])
        issues_text = ""
        if open_issues:
            issues_text = "\n**Recent Open Issues:**\n"
            for issue in open_issues[:5]:
                issues_text += f"- `{issue.get('file', '?')}:{issue.get('line', '?')}` - {issue.get('title', 'Unknown')}\n"
        hotspots = data.get("hotspot_files", [])
        hotspots_text = ""
        if hotspots:
            hotspots_text = "\n**Hotspot Files (frequent issues):**\n"
            for hs in hotspots[:5]:
                hotspots_text += f"- `{hs.get('path', '?')}` ({hs.get('issue_count', 0)} issues)\n"
        conventions = data.get("team_conventions", [])
        conventions_text = ""
        if conventions:
            conventions_text = "\n**Team Conventions:**\n"
            for conv in conventions[:5]:
                conventions_text += f"- {conv}\n"
        fps = data.get("false_positive_patterns", [])
        fp_text = ""
        if fps:
            fp_text = "\n**Known False Positives (skip these patterns):**\n"
            for fp in fps[:5]:
                fp_text += f"- {fp.get('pattern', '?')}: {fp.get('reason', 'N/A')}\n"
        return [
            TextContent(
                type="text",
                text=(
                    f"📚 **Memory Context for {repo}**\n\n"
                    f"- Open issues: {len(open_issues)}\n"
                    f"- Hotspot files: {len(hotspots)}\n"
                    f"- Team conventions: {len(conventions)}\n"
                    f"- False positive patterns: {len(fps)}\n"
                    f"{issues_text}{hotspots_text}{conventions_text}{fp_text}"
                ),
            )
        ]
    if response.status_code == 400:
        return [
            TextContent(
                type="text",
                text=f"⚠️ **No context available for {repo}**\n\nThis may be a new repository or invalid identifier.",
            )
        ]
    return [
        TextContent(
            type="text",
            text=f"⚠️ **Context fetch failed:** HTTP {response.status_code}",
        )
    ]


async def handle_submit_feedback(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    try:
        feedback = SubmitFeedbackInput(**arguments)
    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"❌ **Invalid input:** {e}\n\nRequired fields: `issue_id`, `action`, `repo`",
            )
        ]
    if not re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", feedback.repo):
        return [
            TextContent(
                type="text",
                text=f"❌ **Invalid repo format:** `{feedback.repo}`\n\nExpected format: `owner/repo`",
            )
        ]
    ctx.logger.info(f"Submitting feedback for issue {feedback.issue_id}: {feedback.action.value}")
    body: dict = {
        "action": feedback.action.value,
        "repo": feedback.repo,
        "timestamp": None,
    }
    if feedback.file:
        body["file"] = feedback.file
    if feedback.line:
        body["line"] = feedback.line
    if feedback.pattern:
        body["pattern"] = feedback.pattern
    if feedback.reason:
        body["reason"] = feedback.reason
    response = await ctx.client.post(
        f"{ctx.api_url}/api/v1/code-review/issues/{feedback.issue_id}/feedback",
        headers=ctx.headers,
        json=body,
    )
    if response.status_code in (200, 201):
        action_emoji = {"fixed": "✅", "dismissed": "🔇", "false_positive": "🚫"}.get(
            feedback.action.value, "📝"
        )
        action_text = {
            "fixed": "marked as fixed",
            "dismissed": "dismissed",
            "false_positive": "marked as false positive",
        }.get(feedback.action.value, "updated")
        extra_info = ""
        if feedback.action == FeedbackAction.FALSE_POSITIVE and feedback.pattern:
            extra_info = f"\n\n**Pattern saved:** `{feedback.pattern}`\nThis pattern will be skipped in future reviews."
        return [
            TextContent(
                type="text",
                text=(
                    f"{action_emoji} **Feedback Submitted**\n\n"
                    f"Issue `{feedback.issue_id}` has been {action_text}.\n"
                    f"- **Repository:** {feedback.repo}\n"
                    f"- **Action:** {feedback.action.value}"
                    f"{extra_info}"
                ),
            )
        ]
    if response.status_code == 404:
        return [
            TextContent(
                type="text",
                text=f"❌ **Issue not found:** `{feedback.issue_id}`\n\nThe issue may not exist or has already been resolved.",
            )
        ]
    if response.status_code == 403:
        return [
            TextContent(
                type="text",
                text=(
                    "❌ **Permission Denied**\n\n"
                    "API key lacks permission to submit feedback.\n"
                    "Run `/tmind:login` to create a new key with proper permissions."
                ),
            )
        ]
    return [
        TextContent(
            type="text",
            text=f"❌ **Feedback submission failed:** HTTP {response.status_code}\n{response.text[:200]}",
        )
    ]
