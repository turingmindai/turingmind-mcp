"""Login tools: turingmind_initiate_login, turingmind_poll_login."""

from __future__ import annotations

from mcp.types import TextContent

from .context import ToolContext


def register(registry: dict) -> None:
    registry["turingmind_initiate_login"] = handle_initiate_login
    registry["turingmind_poll_login"] = handle_poll_login


async def handle_initiate_login(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    ctx.logger.info("Initiating device code login flow")
    response = await ctx.client.get(
        f"{ctx.api_url}/api/v1/cli/auth",
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"turingmind-mcp/{ctx.version}",
        },
    )
    if response.status_code == 200:
        data = response.json()
        device_code = data.get("device_code", "")
        user_code = data.get("user_code", "")
        verification_url = data.get("verification_url", "")
        expires_in = data.get("expires_in", 900)
        if not device_code or not user_code:
            return [
                TextContent(
                    type="text",
                    text=f"❌ **Login initiation failed**\n\nInvalid response from server:\n```json\n{response.text[:500]}\n```",
                )
            ]
        return [
            TextContent(
                type="text",
                text=(
                    f"🧠 **TuringMind Login Started**\n\n"
                    f"**Step 1:** Open this URL in your browser:\n"
                    f"```\n{verification_url}\n```\n\n"
                    f"**Step 2:** Sign in with Google or GitHub\n\n"
                    f"**Step 3:** After completing authentication in browser, "
                    f"call `turingmind_poll_login` with:\n"
                    f"```json\n{{\"device_code\": \"{device_code}\"}}\n```\n\n"
                    f"⏱️ Code expires in {expires_in // 60} minutes."
                ),
            )
        ]
    return [
        TextContent(
            type="text",
            text=f"❌ **Login initiation failed:** HTTP {response.status_code}\n{response.text[:300]}",
        )
    ]


async def handle_poll_login(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    device_code = arguments.get("device_code", "")
    if not device_code:
        return [
            TextContent(
                type="text",
                text="❌ **Missing required field:** `device_code`\n\nCall `turingmind_initiate_login` first to get a device code.",
            )
        ]
    ctx.logger.info(f"Polling for login completion: {device_code[:10]}...")
    response = await ctx.client.get(
        f"{ctx.api_url}/api/v1/cli/token",
        params={"device_code": device_code},
        headers={"User-Agent": f"turingmind-mcp/{ctx.version}"},
    )
    data = response.json() if response.status_code in (200, 400, 401, 403) else {}
    if response.status_code == 200 and "access_token" in data:
        access_token = data["access_token"]
        config_path = ctx.save_api_key(access_token, ctx.api_url)
        ctx.logger.info(f"API key saved to {config_path}")
        return [
            TextContent(
                type="text",
                text=(
                    f"✅ **Login Successful!**\n\n"
                    f"API key has been saved to `{config_path}`\n\n"
                    f"**API Key:** `{access_token[:8]}...{access_token[-4:]}`\n\n"
                    f"Cloud features are now enabled. You can use:\n"
                    f"- `turingmind_validate_auth` - Check account status\n"
                    f"- `turingmind_upload_review` - Upload reviews\n"
                    f"- `turingmind_get_context` - Get memory context\n"
                    f"- `turingmind_submit_feedback` - Report false positives\n\n"
                    f"To view your full API key, run: `cat ~/.turingmind/config`"
                ),
            )
        ]
    if data.get("error") == "authorization_pending":
        return [
            TextContent(
                type="text",
                text=(
                    "⏳ **Authorization Pending**\n\n"
                    "User has not completed authentication yet.\n"
                    "Please complete the login in your browser, then call "
                    "`turingmind_poll_login` again with the same device_code."
                ),
            )
        ]
    if data.get("error") == "expired":
        return [
            TextContent(
                type="text",
                text=(
                    "❌ **Device Code Expired**\n\n"
                    "The authentication session has expired.\n"
                    "Please call `turingmind_initiate_login` to start a new login flow."
                ),
            )
        ]
    if data.get("error") == "access_denied":
        return [
            TextContent(
                type="text",
                text=(
                    "❌ **Access Denied**\n\n"
                    "Authentication was denied or cancelled.\n"
                    "Please call `turingmind_initiate_login` to try again."
                ),
            )
        ]
    error_desc = data.get("error_description", data.get("error", "Unknown error"))
    return [
        TextContent(
            type="text",
            text=f"❌ **Login poll failed:** {error_desc}\n\nHTTP {response.status_code}",
        )
    ]
