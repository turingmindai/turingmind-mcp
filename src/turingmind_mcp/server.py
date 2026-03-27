#!/usr/bin/env python3
"""
TuringMind MCP Server

Provides type-safe tools for Claude to interact with TuringMind cloud:
- turingmind_initiate_login: Start device code authentication flow
- turingmind_poll_login: Poll for login completion
- turingmind_validate_auth: Check API key and account status
- turingmind_upload_review: Upload code review results
- turingmind_get_context: Get memory context for a repository
- turingmind_submit_feedback: Submit feedback on review issues

Run with: turingmind-mcp
Configure in Claude Desktop config:
  - macOS: ~/Library/Application Support/Claude/claude_desktop_config.json
  - Windows: %APPDATA%/Claude/claude_desktop_config.json
  - Linux: ~/.config/Claude/claude_desktop_config.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from pydantic import BaseModel, Field

# Load .env file if it exists (before other imports that might use env vars)
_env_path = Path(__file__).parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
    logging.getLogger("turingmind-mcp").debug(f"Loaded environment variables from {_env_path}")

# Import new modules
from .database import MemoryDatabase
from .memory_manager import MemoryManager
from .entity_indexer import EntityIndexer, get_repo_path
from .auto_review_service import get_auto_review_service
from .tool_config import is_tool_enabled, get_enabled_tools
from .v2_engine.tool_registry import ALL_V2_TOOLS
from .tools import ToolContext, get_handler, register_all

# Import agents and LLM providers
try:
    from .agents.chat_analysis_agent import ChatAnalysisAgent
    from .llm.config import get_langsmith_client, get_llm_provider
    AGENTS_AVAILABLE = True
except ImportError:
    AGENTS_AVAILABLE = False
    ChatAnalysisAgent = None
    get_langsmith_client = None
    get_llm_provider = None

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.DEBUG if os.environ.get("TURINGMIND_DEBUG") else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,  # MCP uses stdout for protocol, stderr for logs
)
logger = logging.getLogger("turingmind-mcp")

# ============================================================================
# CONFIGURATION
# ============================================================================

DEFAULT_API_URL = "https://api.turingmind.ai"
CONFIG_PATH = os.path.expanduser("~/.turingmind/config")
CONFIG_DIR = os.path.expanduser("~/.turingmind")

# Tools that don't require authentication
AUTH_FREE_TOOLS = {
    "turingmind_initiate_login",
    "turingmind_poll_login",
    "turingmind_index_codebase",
    "turingmind_get_related_code",
    "turingmind_get_project_structure",
    "turingmind_get_edit_reasoning",
    "turingmind_analyze_diff",
    "turingmind_apply_edit",
    "turingmind_log_reasoning",
    "turingmind_get_memory",
    "turingmind_save_memory",
    "turingmind_list_memory",
    "turingmind_get_audit_trail",
    "turingmind_validate_auth",
    # v2 Engine (Local constraint graph operations)
    "turingmind_create_spec_node",
    "turingmind_update_spec_node",
    "turingmind_get_spec_status",
    "turingmind_list_spec_nodes",
    "turingmind_get_ready_nodes",
    "turingmind_promote_node",
    "turingmind_generate_verification",
    "turingmind_run_verification",
    "turingmind_record_execution_stage",
    "turingmind_classify_failure",
    "turingmind_apply_fix",
    "turingmind_apply_spec_delta",
    "turingmind_get_impacted_nodes",
    "turingmind_request_approval",
    "turingmind_get_execution_state",
    "turingmind_ingest_runtime_signal",
    "turingmind_bootstrap_codebase",
    "turingmind_get_decision_queue",
    "turingmind_sync_codebase",
}


# Package version
__version__ = "0.2.0"


def get_api_url() -> str:
    """Get API URL from environment or config file."""
    api_url = os.environ.get("TURINGMIND_API_URL", "")

    if not api_url and os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("export TURINGMIND_API_URL="):
                        api_url = line.split("=", 1)[1].strip().strip("\"'")
        except Exception:
            pass

    return api_url or DEFAULT_API_URL


def save_api_key(api_key: str, api_url: str | None = None) -> str:
    """Save API key to config file. Returns the path saved to."""
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)

    # Use provided URL or default
    url_to_save = api_url or DEFAULT_API_URL

    # Write config file with restrictive permissions (0600 = owner read/write only)
    config_content = f"export TURINGMIND_API_KEY={api_key}\nexport TURINGMIND_API_URL={url_to_save}\n"
    
    # Use os.open with explicit permissions for security
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, config_content.encode())
    finally:
        os.close(fd)

    # Also set in environment for current session
    os.environ["TURINGMIND_API_KEY"] = api_key
    if url_to_save:
        os.environ["TURINGMIND_API_URL"] = url_to_save

    return CONFIG_PATH


def get_config() -> tuple[str, str]:
    """Get API URL and API key from environment or config file."""
    api_url = os.environ.get("TURINGMIND_API_URL", DEFAULT_API_URL)
    api_key = os.environ.get("TURINGMIND_API_KEY", "")

    # Try loading from config file if not in environment
    if not api_key and os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("export TURINGMIND_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip("\"'")
                    elif line.startswith("export TURINGMIND_API_URL="):
                        api_url = line.split("=", 1)[1].strip().strip("\"'")
        except Exception as e:
            logger.warning(f"Failed to read config: {e}")

    return api_url, api_key


# ============================================================================
# TOOL SCHEMAS (Pydantic models for type-safe input)
# ============================================================================


# ============================================================================
# MCP SERVER
# ============================================================================

server = Server("turingmind")

# Initialize database and memory manager (singleton)
_db_instance: Optional[MemoryDatabase] = None
_memory_manager_instance: Optional[MemoryManager] = None


def get_db() -> MemoryDatabase:
    """Get or create database instance."""
    global _db_instance
    if _db_instance is None:
        _db_instance = MemoryDatabase()
    return _db_instance


def get_memory_manager() -> MemoryManager:
    """Get or create memory manager instance."""
    global _memory_manager_instance
    if _memory_manager_instance is None:
        _memory_manager_instance = MemoryManager(get_db())
    return _memory_manager_instance


# Agent instances (lazy initialization)
_chat_analysis_agent = None


def get_chat_analysis_agent() -> Optional[object]:  # ChatAnalysisAgent when available, else None
    """Get or create ChatAnalysisAgent instance with LangSmith integration."""
    global _chat_analysis_agent
    
    if not AGENTS_AVAILABLE or ChatAnalysisAgent is None:
        return None
    
    if _chat_analysis_agent is None:
        # Get LLM provider
        if get_llm_provider is None:
            logger.warning("LLM provider factory not available. ChatAnalysisAgent unavailable.")
            return None
        
        llm_provider = get_llm_provider("azure")
        if not llm_provider:
            logger.warning("LLM provider not configured. ChatAnalysisAgent unavailable.")
            return None
        
        # Get LangSmith client (optional)
        langsmith_client = None
        if get_langsmith_client:
            langsmith_client = get_langsmith_client()
            if langsmith_client:
                logger.info("LangSmith tracing enabled for ChatAnalysisAgent")
            else:
                logger.debug("LangSmith not configured. Agent will run without tracing.")
        
        # Guard: only instantiate if ChatAnalysisAgent is actually a callable class
        if not callable(ChatAnalysisAgent):
            return None
        _chat_analysis_agent = ChatAnalysisAgent(
            llm_provider=llm_provider,
            langsmith_client=langsmith_client,
            use_heavy_task_model=False
        )

    return _chat_analysis_agent


# Register all tool handlers once at import time (not per-request)
register_all()


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available TuringMind tools.

    Filtered by TURINGMIND_ENABLED_TOOL_GROUPS env var.
    Set to 'all', 'v2_engine', 'code_intelligence', 'login', or a comma-separated list.
    Default: v2_engine,login,code_intelligence
    """
    enabled_tools = get_enabled_tools()
    logger.info(f"Enabled tools: {len(enabled_tools)} tools from groups")
    filtered_tools = [tool for tool in ALL_V2_TOOLS if tool.name in enabled_tools]
    logger.info(f"Returning {len(filtered_tools)} of {len(ALL_V2_TOOLS)} tools (filtered by tool_config)")
    return filtered_tools


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a TuringMind tool."""

    # Check if tool is enabled
    if not is_tool_enabled(name):
        return [
            TextContent(
                type="text",
                text=(
                    f"❌ **Tool `{name}` is disabled**\n\n"
                    f"This tool is not in the current enabled tool groups.\n"
                    f"To enable it, set the environment variable:\n"
                    f"```bash\n"
                    f"export TURINGMIND_ENABLED_TOOL_GROUPS=all\n"
                    f"```\n"
                    f"Or add the specific group containing this tool."
                ),
            )
        ]

    api_url, api_key = get_config()

    # Check if tool requires authentication
    if name not in AUTH_FREE_TOOLS and not api_key:
        return [
            TextContent(
                type="text",
                text=(
                    "❌ **TURINGMIND_API_KEY not configured**\n\n"
                    "Run `turingmind_initiate_login` to start authentication, "
                    "or set the environment variable:\n"
                    "```bash\n"
                    "export TURINGMIND_API_KEY=tmk_your_key_here\n"
                    "```"
                ),
            )
        ]

    logger.info(f"Executing tool: {name}")

    # ── Fast path: local handler (v2 engine, code intelligence, memory) ──
    # These never touch the network — no need to open an httpx client.
    handler = get_handler(name)
    if handler is not None:
        try:
            # Build a minimal ToolContext; cloud credentials are optional here.
            context = ToolContext(
                client=None,  # not used by local handlers
                api_url=api_url,
                headers={},
                logger=logger,
                save_api_key=save_api_key,
                version=__version__,
                get_db=get_db,
                get_memory_manager=get_memory_manager,
                get_repo_path=get_repo_path,
                get_config=get_config,
                entity_indexer_cls=EntityIndexer,
                get_chat_analysis_agent=get_chat_analysis_agent,
            )
            return await handler(arguments, context)
        except Exception as e:
            logger.exception(f"Local tool {name} failed")
            return [TextContent(type="text", text=f"❌ **Error:** {type(e).__name__}: {e}")]

    # ── Cloud path: tools that call the TuringMind API over HTTP ──
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"turingmind-mcp/{__version__}",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            # Build full context for cloud tools
            context = ToolContext(
                client=client,
                api_url=api_url,
                headers=headers,
                logger=logger,
                save_api_key=save_api_key,
                version=__version__,
                get_db=get_db,
                get_memory_manager=get_memory_manager,
                get_repo_path=get_repo_path,
                get_config=get_config,
                entity_indexer_cls=EntityIndexer,
                get_chat_analysis_agent=get_chat_analysis_agent,
            )
            # If we reach here with no handler, the tool is unknown
            return [
                TextContent(
                    type="text",
                    text=(
                        f"❌ **Unknown tool:** `{name}`\n\n"
                        f"Available v2 tools include:\n"
                        f"- `turingmind_create_spec_node` - Create a new constraint node\n"
                        f"- `turingmind_list_spec_nodes` - List nodes for a repo\n"
                        f"- `turingmind_get_execution_state` - Get DAG control plane state\n"
                        f"- `turingmind_apply_spec_delta` - Propagate a spec change\n"
                        f"- `turingmind_classify_failure` - Classify a node failure\n"
                        f"- `turingmind_request_approval` - Human-gate a node\n"
                        f"Run `list_tools` to see the full enabled tool surface."
                    ),
                )
            ]

        except httpx.ConnectError:
            return [
                TextContent(
                    type="text",
                    text=(
                        f"❌ **Connection Error**\n\n"
                        f"Could not connect to TuringMind API at `{api_url}`.\n"
                        f"Check your network connection or API URL configuration."
                    ),
                )
            ]
        except httpx.TimeoutException:
            return [
                TextContent(
                    type="text",
                    text="❌ **Request Timeout**\n\nTuringMind API did not respond in time. Try again.",
                )
            ]
        except Exception as e:
            logger.exception(f"Cloud tool {name} failed")
            return [TextContent(type="text", text=f"❌ **Error:** {type(e).__name__}: {e}")]

    # Unreachable: all branches above return, but satisfies type checker
    return [TextContent(type="text", text=f"❌ **Error:** unexpected code path for tool `{name}`.")]


# ============================================================================
# MAIN
# ============================================================================


def main() -> None:
    """Run the TuringMind MCP server."""
    logger.info("Starting TuringMind MCP server...")

    api_url, api_key = get_config()
    logger.info(f"API URL: {api_url}")
    logger.info(f"API Key: {'configured' if api_key else 'NOT SET'}")

    asyncio.run(run_server())


async def run_server() -> None:
    """Run the MCP server with stdio transport."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    main()
