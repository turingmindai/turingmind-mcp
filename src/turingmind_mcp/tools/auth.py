"""Auth tool: turingmind_validate_auth."""

from __future__ import annotations

from mcp.types import TextContent

from .context import ToolContext


def register(registry: dict) -> None:
    registry["turingmind_validate_auth"] = handle_validate_auth


async def handle_validate_auth(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    response = await ctx.client.get(
        f"{ctx.api_url}/api/v1/code-review/auth/validate",
        headers=ctx.headers,
    )
    if response.status_code == 200:
        data = response.json()
        quota = data.get("quota", {})
        return [
            TextContent(
                type="text",
                text=(
                    f"✅ **TuringMind Authentication Valid**\n\n"
                    f"- **Tier:** {data.get('tier', 'unknown')}\n"
                    f"- **Quota:** {quota.get('reviews_remaining', '?')}"
                    f"/{quota.get('reviews_limit', '?')} reviews remaining\n"
                    f"- **User:** {data.get('user_id', 'unknown')[:20]}...\n\n"
                    f"Cloud features are enabled. You can use `turingmind_upload_review` "
                    f"and `turingmind_get_context`."
                ),
            )
        ]
    if response.status_code == 401:
        return [
            TextContent(
                type="text",
                text=(
                    "❌ **Authentication Failed**\n\n"
                    "API key is invalid or expired. Run `/tmind:login` to re-authenticate."
                ),
            )
        ]
    return [
        TextContent(
            type="text",
            text=f"❌ **Auth check failed:** HTTP {response.status_code}\n{response.text[:200]}",
        )
    ]
