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
import logging
import os
import re
import sys
from enum import Enum
from typing import Optional

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from pydantic import BaseModel, Field

# Import new modules
from .database import MemoryDatabase
from .memory_manager import MemoryManager
from .entity_indexer import EntityIndexer, get_repo_path
from .auto_review_service import get_auto_review_service

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
    "turingmind_list_memory",
    "turingmind_get_memory",
    "turingmind_get_memory_stats",
    "turingmind_explain_decision",
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


class Severity(str, Enum):
    """Issue severity levels"""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ReviewType(str, Enum):
    """Code review types"""

    QUICK = "quick"
    DEEP = "deep"


class Issue(BaseModel):
    """A single code review issue"""

    title: str = Field(..., description="Short issue title (max 500 chars)")
    severity: Severity = Field(..., description="Issue severity: critical, high, medium, low")
    category: str = Field("bug", description="Category: security, bug, compliance, performance")
    file: str = Field(..., description="File path where issue was found")
    line: int = Field(..., ge=1, description="Line number (1-indexed)")
    description: Optional[str] = Field(None, description="Detailed description of the issue")
    cwe: Optional[str] = Field(None, description="CWE ID if security issue (e.g., CWE-79)")
    confidence: int = Field(85, ge=0, le=100, description="Confidence score 0-100")


class UploadReviewInput(BaseModel):
    """Input schema for turingmind_upload_review tool"""

    repo: str = Field(..., description="Repository identifier (owner/repo)")
    branch: Optional[str] = Field(None, description="Git branch name")
    commit: Optional[str] = Field(None, description="Git commit SHA (short or full)")
    review_type: ReviewType = Field(ReviewType.QUICK, description="Review type: quick or deep")
    issues: list[dict] = Field(default_factory=list, description="List of issues found")
    raw_content: Optional[str] = Field(None, description="Full review content as markdown")
    summary: Optional[dict] = Field(None, description="Summary with critical/high/medium/low counts")
    files_reviewed: list[dict] = Field(default_factory=list, description="Files that were reviewed")


class GetContextInput(BaseModel):
    """Input schema for turingmind_get_context tool"""

    repo: str = Field(..., description="Repository identifier (owner/repo)")


class FeedbackAction(str, Enum):
    """Actions for issue feedback"""

    FIXED = "fixed"
    DISMISSED = "dismissed"
    FALSE_POSITIVE = "false_positive"


class SubmitFeedbackInput(BaseModel):
    """Input schema for turingmind_submit_feedback tool"""

    issue_id: str = Field(..., description="Issue ID from the review")
    action: FeedbackAction = Field(..., description="Action: fixed, dismissed, or false_positive")
    repo: str = Field(..., description="Repository identifier (owner/repo)")
    file: Optional[str] = Field(None, description="File path where issue was found")
    line: Optional[int] = Field(None, description="Line number of the issue")
    pattern: Optional[str] = Field(None, description="For false_positive: pattern to remember and skip")
    reason: Optional[str] = Field(None, description="Reason for the feedback")


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


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available TuringMind tools."""
    return [
        # ─────────────────────────────────────────────────────────────
        # LOGIN TOOLS (no auth required)
        # ─────────────────────────────────────────────────────────────
        Tool(
            name="turingmind_initiate_login",
            description=(
                "Start device code authentication flow for TuringMind. "
                "Returns a verification URL and user code. The user should open the URL "
                "in their browser and enter the code. Then call turingmind_poll_login "
                "with the device_code to complete authentication. "
                "No API key required to call this tool."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="turingmind_poll_login",
            description=(
                "Poll for device code authentication completion. "
                "Call this after turingmind_initiate_login, passing the device_code. "
                "Returns the API key when authentication is complete, or 'pending' status. "
                "On success, automatically saves API key to ~/.turingmind/config. "
                "No API key required to call this tool."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_code": {
                        "type": "string",
                        "description": "Device code from turingmind_initiate_login",
                    }
                },
                "required": ["device_code"],
            },
        ),
        # ─────────────────────────────────────────────────────────────
        # AUTH TOOLS
        # ─────────────────────────────────────────────────────────────
        Tool(
            name="turingmind_validate_auth",
            description=(
                "Validate TuringMind API key and get account information. "
                "Returns tier, quota remaining, and user info. "
                "Call this first to verify cloud features are available."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="turingmind_upload_review",
            description=(
                "Upload code review results to TuringMind cloud for analytics and memory. "
                "Stores issues found, files reviewed, and review metadata. "
                "Returns review ID on success. Requires code_review:write permission."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo format)",
                    },
                    "branch": {"type": "string", "description": "Git branch name (optional)"},
                    "commit": {"type": "string", "description": "Git commit SHA (optional)"},
                    "review_type": {
                        "type": "string",
                        "enum": ["quick", "deep"],
                        "default": "quick",
                        "description": "Type of review performed",
                    },
                    "issues": {
                        "type": "array",
                        "description": "List of issues found during review",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "Issue title"},
                                "severity": {
                                    "type": "string",
                                    "enum": ["critical", "high", "medium", "low"],
                                },
                                "category": {
                                    "type": "string",
                                    "description": "Category: security, bug, compliance",
                                },
                                "file": {"type": "string", "description": "File path"},
                                "line": {"type": "integer", "description": "Line number"},
                                "description": {"type": "string", "description": "Details"},
                                "cwe": {"type": "string", "description": "CWE ID if applicable"},
                                "confidence": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": 100,
                                },
                            },
                            "required": ["title", "severity", "file", "line"],
                        },
                    },
                    "raw_content": {
                        "type": "string",
                        "description": "Full review as markdown (optional)",
                    },
                    "summary": {
                        "type": "object",
                        "description": "Summary counts",
                        "properties": {
                            "critical": {"type": "integer"},
                            "high": {"type": "integer"},
                            "medium": {"type": "integer"},
                            "low": {"type": "integer"},
                        },
                    },
                    "files_reviewed": {
                        "type": "array",
                        "description": "Files that were reviewed",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "lines_added": {"type": "integer"},
                                "lines_removed": {"type": "integer"},
                            },
                        },
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="turingmind_get_context",
            description=(
                "Get memory context for a repository from TuringMind cloud. "
                "Returns recent open issues, hotspot files, false positive patterns, "
                "and team conventions. Use this before reviewing to avoid duplicate reports."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo format)",
                    }
                },
                "required": ["repo"],
            },
        ),
        # ─────────────────────────────────────────────────────────────
        # FEEDBACK TOOL
        # ─────────────────────────────────────────────────────────────
        Tool(
            name="turingmind_submit_feedback",
            description=(
                "Submit feedback on a code review issue. Use this when user indicates "
                "an issue was fixed, should be dismissed, or is a false positive. "
                "For false positives, provide pattern and reason to improve future reviews."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {
                        "type": "string",
                        "description": "Issue ID from the review (e.g., iss_abc123)",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["fixed", "dismissed", "false_positive"],
                        "description": "Feedback action type",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo)",
                    },
                    "file": {
                        "type": "string",
                        "description": "File path where issue was found (optional)",
                    },
                    "line": {
                        "type": "integer",
                        "description": "Line number of the issue (optional)",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "For false_positive: code pattern to remember and skip in future",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for the feedback (especially important for false_positive)",
                    },
                },
                "required": ["issue_id", "action", "repo"],
            },
        ),
        # ─────────────────────────────────────────────────────────────
        # CODE ENTITY INDEXING TOOLS
        # ─────────────────────────────────────────────────────────────
        Tool(
            name="turingmind_index_codebase",
            description=(
                "Index codebase using AST parsing to extract code entities "
                "(functions, classes, files) and relationships. "
                "Enables relationship-aware code review and impact analysis."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository identifier (owner/repo)",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Git branch (default: main)",
                        "default": "main",
                    },
                    "languages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Languages to parse (js, ts, py)",
                        "default": ["javascript", "typescript", "python"],
                    },
                    "force_reindex": {
                        "type": "boolean",
                        "description": "Force reindex even if already indexed",
                        "default": False,
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="turingmind_get_related_code",
            description=(
                "Get code entities related to a specific function/class/file. "
                "Uses relationship graph to find callers, callees, and imports for impact analysis."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository (owner/repo)"},
                    "file": {"type": "string", "description": "File path"},
                    "entity_name": {
                        "type": "string",
                        "description": "Function/class name (optional)",
                    },
                    "relationship_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Types: calls, imports (default: both)",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["both", "outgoing", "incoming"],
                        "description": "Relationship direction",
                        "default": "both",
                    },
                },
                "required": ["repo", "file"],
            },
        ),
        Tool(
            name="turingmind_get_project_structure",
            description=(
                "Get comprehensive project structure summary. "
                "Returns language distribution, entity type counts, and basic architecture info."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository (owner/repo)"}
                },
                "required": ["repo"],
            },
        ),
        # ─────────────────────────────────────────────────────────────
        # DEVELOPER INTENT TOOLS
        # ─────────────────────────────────────────────────────────────
        Tool(
            name="turingmind_get_edit_reasoning",
            description=(
                "Get or capture developer reasoning for file changes. "
                "Extracts intent from commit messages or prompts developer. "
                "Supports per-file reasoning. Helps code review understand intent and reduce false positives."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository (owner/repo)"},
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "file_path": {"type": "string"},
                                "reasoning": {"type": "string"},
                                "change_type": {
                                    "type": "string",
                                    "enum": ["bug_fix", "feature", "refactoring", "security", "other"],
                                },
                                "memory_category": {
                                    "type": "string",
                                    "enum": ["repo_fact", "learned_pattern", "explicit_rule", "session_context"],
                                },
                                "scope": {"type": "string"},
                                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            },
                        },
                        "description": "List of files with optional per-file reasoning",
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Optional commit message to parse",
                    },
                    "commit_hash": {
                        "type": "string",
                        "description": "Optional commit hash for historical lookups",
                    },
                    "conversation_id": {
                        "type": "string",
                        "description": "Optional conversation ID for context",
                    },
                    "interactive": {
                        "type": "boolean",
                        "description": "Whether to prompt user if reasoning not found",
                        "default": False,
                    },
                },
                "required": ["repo", "files"],
            },
        ),
        # ─────────────────────────────────────────────────────────────
        # MEMORY MANAGEMENT TOOLS
        # ─────────────────────────────────────────────────────────────
        Tool(
            name="turingmind_list_memory",
            description=(
                "List memory entries with filtering and pagination. "
                "Supports filtering by category, status, scope, and security tags."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository (owner/repo)"},
                    "category": {
                        "type": "string",
                        "enum": ["repo_fact", "learned_pattern", "explicit_rule", "session_context", "all"],
                        "description": "Memory category filter",
                        "default": "all",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["active", "pending", "conflict", "deprecated", "all"],
                        "description": "Status filter",
                        "default": "all",
                    },
                    "scope": {"type": "string", "description": "Filter by scope"},
                    "security_tag": {
                        "type": "string",
                        "enum": ["auth", "crypto", "secrets", "compliance"],
                        "description": "Filter by security tag",
                    },
                    "page": {"type": "integer", "description": "Page number", "default": 1},
                    "limit": {"type": "integer", "description": "Items per page", "default": 50},
                    "search": {"type": "string", "description": "Search content"},
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="turingmind_get_memory",
            description=(
                "Get detailed information about a specific memory entry including evidence."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository (owner/repo)"},
                    "memory_id": {"type": "string", "description": "Memory entry ID"},
                },
                "required": ["repo", "memory_id"],
            },
        ),
        Tool(
            name="turingmind_save_memory",
            description=(
                "Create or update a memory entry. "
                "Supports learned patterns, explicit rules, and session context."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository (owner/repo)"},
                    "memory_id": {
                        "type": "string",
                        "description": "Memory ID (optional for updates)",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["learned_pattern", "explicit_rule", "session_context"],
                        "description": "Memory type",
                    },
                    "content": {"type": "string", "description": "Memory content"},
                    "scope": {"type": "string", "description": "Scope (repo, file, function)"},
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "Confidence score",
                    },
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string"},
                                "content": {"type": "string"},
                                "file": {"type": "string"},
                                "line": {"type": "integer"},
                            },
                        },
                        "description": "Evidence snippets",
                    },
                    "security_tags": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["auth", "crypto", "secrets", "compliance"]},
                        "description": "Security tags",
                    },
                    "yaml_definition": {
                        "type": "string",
                        "description": "YAML representation",
                    },
                },
                "required": ["repo", "type", "content", "scope"],
            },
        ),
        Tool(
            name="turingmind_delete_memory",
            description=(
                "Delete or deprecate a memory entry. "
                "Deprecation preserves history, deletion removes completely."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository (owner/repo)"},
                    "memory_id": {"type": "string", "description": "Memory entry ID"},
                    "action": {
                        "type": "string",
                        "enum": ["delete", "deprecate"],
                        "description": "Action type",
                        "default": "deprecate",
                    },
                },
                "required": ["repo", "memory_id"],
            },
        ),
        Tool(
            name="turingmind_detect_conflicts",
            description=(
                "Detect conflicts between memory entries. "
                "Identifies contradictions, overlaps, and scope conflicts."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository (owner/repo)"},
                    "memory_id": {"type": "string", "description": "New/updated entry ID"},
                },
                "required": ["repo", "memory_id"],
            },
        ),
        Tool(
            name="turingmind_resolve_conflict",
            description=(
                "Resolve conflicts between memory entries. "
                "Supports priority, scope-narrow, time-bound, and merge strategies."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository (owner/repo)"},
                    "conflict_id": {"type": "string", "description": "Conflict ID"},
                    "strategy": {
                        "type": "string",
                        "enum": ["priority", "scope_narrow", "time_bound", "merge"],
                        "description": "Resolution strategy",
                    },
                    "resolution": {
                        "type": "object",
                        "properties": {
                            "keep_memory_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Memory IDs to keep",
                            },
                            "new_content": {"type": "string", "description": "Merged content"},
                            "new_scope": {"type": "string", "description": "New scope"},
                        },
                    },
                },
                "required": ["repo", "conflict_id", "strategy"],
            },
        ),
        Tool(
            name="turingmind_simulate_impact",
            description=(
                "Simulate how memory entries affect code review. "
                "Shows before/after comparison of review results."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository (owner/repo)"},
                    "memory_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Memory IDs to simulate",
                    },
                    "test_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Test files to review (optional)",
                    },
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="turingmind_explain_decision",
            description=(
                "Explain why AI made a specific decision in code review. "
                "Shows weighted memory contributions and reasoning."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository (owner/repo)"},
                    "issue_id": {
                        "type": "string",
                        "description": "Review issue ID (optional)",
                    },
                    "file": {"type": "string", "description": "File path (optional)"},
                    "line": {"type": "integer", "description": "Line number (optional)"},
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="turingmind_get_memory_stats",
            description=(
                "Get statistics about memory entries for a repository. "
                "Returns counts by category, status, and other metrics."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository (owner/repo)"}
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="turingmind_enable_auto_review",
            description=(
                "Enable automatic code review on git commits. "
                "Monitors repository for new commits and triggers reviews automatically."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository (owner/repo)"},
                    "branch": {
                        "type": "string",
                        "description": "Branch to monitor",
                        "default": "main",
                    },
                    "review_type": {
                        "type": "string",
                        "enum": ["quick", "deep"],
                        "description": "Review type",
                        "default": "quick",
                    },
                    "webhook_url": {
                        "type": "string",
                        "description": "Optional webhook for notifications",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "Enable/disable monitoring",
                        "default": True,
                    },
                },
                "required": ["repo"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a TuringMind tool."""
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

    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"turingmind-mcp/{__version__}",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            # ─────────────────────────────────────────────────────────────
            # INITIATE LOGIN (no auth required)
            # ─────────────────────────────────────────────────────────────
            if name == "turingmind_initiate_login":
                logger.info("Initiating device code login flow")

                response = await client.get(
                    f"{api_url}/api/v1/cli/auth",
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": f"turingmind-mcp/{__version__}",
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
                else:
                    return [
                        TextContent(
                            type="text",
                            text=f"❌ **Login initiation failed:** HTTP {response.status_code}\n{response.text[:300]}",
                        )
                    ]

            # ─────────────────────────────────────────────────────────────
            # POLL LOGIN (no auth required)
            # ─────────────────────────────────────────────────────────────
            elif name == "turingmind_poll_login":
                device_code = arguments.get("device_code", "")
                if not device_code:
                    return [
                        TextContent(
                            type="text",
                            text="❌ **Missing required field:** `device_code`\n\nCall `turingmind_initiate_login` first to get a device code.",
                        )
                    ]

                logger.info(f"Polling for login completion: {device_code[:10]}...")

                response = await client.get(
                    f"{api_url}/api/v1/cli/token",
                    params={"device_code": device_code},
                    headers={"User-Agent": f"turingmind-mcp/{__version__}"},
                )

                data = response.json() if response.status_code in (200, 400, 401, 403) else {}

                # Success - got access token
                if response.status_code == 200 and "access_token" in data:
                    access_token = data["access_token"]

                    # Save to config
                    config_path = save_api_key(access_token, api_url)
                    logger.info(f"API key saved to {config_path}")

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

                # Still pending
                elif data.get("error") == "authorization_pending":
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

                # Expired
                elif data.get("error") == "expired":
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

                # Access denied (user rejected or error)
                elif data.get("error") == "access_denied":
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

                # Other error
                else:
                    error_desc = data.get("error_description", data.get("error", "Unknown error"))
                    return [
                        TextContent(
                            type="text",
                            text=f"❌ **Login poll failed:** {error_desc}\n\nHTTP {response.status_code}",
                        )
                    ]

            # ─────────────────────────────────────────────────────────────
            # VALIDATE AUTH
            # ─────────────────────────────────────────────────────────────
            elif name == "turingmind_validate_auth":
                response = await client.get(
                    f"{api_url}/api/v1/code-review/auth/validate",
                    headers=headers,
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
                elif response.status_code == 401:
                    return [
                        TextContent(
                            type="text",
                            text=(
                                "❌ **Authentication Failed**\n\n"
                                "API key is invalid or expired. Run `/tmind:login` to re-authenticate."
                            ),
                        )
                    ]
                else:
                    return [
                        TextContent(
                            type="text",
                            text=f"❌ **Auth check failed:** HTTP {response.status_code}\n{response.text[:200]}",
                        )
                    ]

            # ─────────────────────────────────────────────────────────────
            # UPLOAD REVIEW
            # ─────────────────────────────────────────────────────────────
            elif name == "turingmind_upload_review":
                # Validate input
                try:
                    review = UploadReviewInput(**arguments)
                except Exception as e:
                    return [
                        TextContent(
                            type="text",
                            text=f"❌ **Invalid input:** {e}\n\nRequired field: `repo`",
                        )
                    ]

                # Validate repo format (owner/repo)
                if not re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", review.repo):
                    return [
                        TextContent(
                            type="text",
                            text=f"❌ **Invalid repo format:** `{review.repo}`\n\nExpected format: `owner/repo`"
                        )
                    ]

                # Count issues by severity for auto-summary
                issues = review.issues or []
                auto_summary = {
                    "critical": sum(1 for i in issues if i.get("severity") == "critical"),
                    "high": sum(1 for i in issues if i.get("severity") == "high"),
                    "medium": sum(1 for i in issues if i.get("severity") == "medium"),
                    "low": sum(1 for i in issues if i.get("severity") == "low"),
                }

                # Build request body
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

                logger.info(f"Uploading review for {review.repo} with {len(issues)} issues")

                response = await client.post(
                    f"{api_url}/api/v1/code-review/reviews",
                    headers=headers,
                    json=body,
                )

                if response.status_code in (200, 201):
                    data = response.json()
                    review_id = data.get('review_id', 'unknown')
                    
                    # Track memory usage for explainability
                    try:
                        db = get_db()
                        memory_manager = get_memory_manager()
                        
                        # Get active memory entries for this repo
                        active_memories = db.list_memory_entries(
                            repo=review.repo, status="active"
                        )
                        
                        # For each issue, determine which memories influenced it
                        for issue in issues:
                            issue_file = issue.get("file", "")
                            issue_line = issue.get("line")
                            
                            # Find influencing memories
                            influencing_memories = []
                            for memory in active_memories:
                                # Check scope match
                                scope = memory.get("scope", "")
                                if scope == "repo" or scope == issue_file or issue_file.startswith(scope):
                                    # Calculate weight based on memory type and confidence
                                    weight = memory.get("confidence", 0.8) * 0.5  # Base weight
                                    
                                    if memory.get("type") == "explicit_rule":
                                        # Explicit rules have higher weight
                                        weight = memory.get("confidence", 1.0) * 0.8
                                    elif memory.get("type") == "learned_pattern":
                                        # Learned patterns filter false positives
                                        weight = memory.get("confidence", 0.7) * 0.3
                                    elif memory.get("type") == "session_context":
                                        # Session context has moderate weight
                                        weight = memory.get("confidence", 0.8) * 0.4
                                    
                                    if weight > 0.1:  # Only track significant influences
                                        influencing_memories.append({
                                            "memory_id": memory["memory_id"],
                                            "weight": weight,
                                        })
                            
                            # Track memory usage for each influencing memory
                            for mem in influencing_memories:
                                db.track_memory_usage(
                                    repo=review.repo,
                                    memory_id=mem["memory_id"],
                                    context="code_review",
                                    weight=mem["weight"],
                                    issue_id=review_id,  # Use review_id as issue identifier
                                    file_path=issue_file,
                                    line_number=issue_line,
                                )
                    except Exception as e:
                        logger.warning(f"Failed to track memory usage: {e}")
                    
                    return [
                        TextContent(
                            type="text",
                            text=(
                                f"🧠 **Review Uploaded to TuringMind**\n\n"
                                f"- **Review ID:** `{review_id}`\n"
                                f"- **Repository:** {review.repo}\n"
                                f"- **Issues:** {len(issues)}\n"
                                f"- **Summary:** {auto_summary['critical']} critical, "
                                f"{auto_summary['high']} high, {auto_summary['medium']} medium, "
                                f"{auto_summary['low']} low\n\n"
                                f"Review data is now available in TuringMind cloud for analytics "
                                f"and future context. Memory usage tracked for explainability."
                            ),
                        )
                    ]
                elif response.status_code == 403:
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
                elif response.status_code == 422:
                    return [
                        TextContent(
                            type="text",
                            text=(
                                f"❌ **Validation Error**\n\n"
                                f"Request body failed validation:\n```\n{response.text[:500]}\n```"
                            ),
                        )
                    ]
                else:
                    return [
                        TextContent(
                            type="text",
                            text=f"❌ **Upload failed:** HTTP {response.status_code}\n{response.text[:200]}",
                        )
                    ]

            # ─────────────────────────────────────────────────────────────
            # GET CONTEXT
            # ─────────────────────────────────────────────────────────────
            elif name == "turingmind_get_context":
                repo = arguments.get("repo", "")
                if not repo:
                    return [
                        TextContent(type="text", text="❌ **Missing required field:** `repo`")
                    ]
                
                # Validate repo format (owner/repo) to prevent path traversal
                if not re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", repo):
                    return [
                        TextContent(
                            type="text",
                            text=f"❌ **Invalid repo format:** `{repo}`\n\nExpected format: `owner/repo`"
                        )
                    ]

                logger.info(f"Fetching context for {repo}")

                response = await client.get(
                    f"{api_url}/api/v1/code-review/context/{repo}",
                    headers=headers,
                )

                if response.status_code == 200:
                    data = response.json()

                    # Format open issues
                    open_issues = data.get("recent_open_issues", [])
                    issues_text = ""
                    if open_issues:
                        issues_text = "\n**Recent Open Issues:**\n"
                        for issue in open_issues[:5]:
                            issues_text += f"- `{issue.get('file', '?')}:{issue.get('line', '?')}` - {issue.get('title', 'Unknown')}\n"

                    # Format hotspots
                    hotspots = data.get("hotspot_files", [])
                    hotspots_text = ""
                    if hotspots:
                        hotspots_text = "\n**Hotspot Files (frequent issues):**\n"
                        for hs in hotspots[:5]:
                            hotspots_text += (
                                f"- `{hs.get('path', '?')}` ({hs.get('issue_count', 0)} issues)\n"
                            )

                    # Format conventions
                    conventions = data.get("team_conventions", [])
                    conventions_text = ""
                    if conventions:
                        conventions_text = "\n**Team Conventions:**\n"
                        for conv in conventions[:5]:
                            conventions_text += f"- {conv}\n"

                    # Format false positives
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
                elif response.status_code == 400:
                    return [
                        TextContent(
                            type="text",
                            text=f"⚠️ **No context available for {repo}**\n\nThis may be a new repository or invalid identifier.",
                        )
                    ]
                else:
                    return [
                        TextContent(
                            type="text",
                            text=f"⚠️ **Context fetch failed:** HTTP {response.status_code}",
                        )
                    ]

            # ─────────────────────────────────────────────────────────────
            # SUBMIT FEEDBACK
            # ─────────────────────────────────────────────────────────────
            elif name == "turingmind_submit_feedback":
                # Validate input
                try:
                    feedback = SubmitFeedbackInput(**arguments)
                except Exception as e:
                    return [
                        TextContent(
                            type="text",
                            text=f"❌ **Invalid input:** {e}\n\nRequired fields: `issue_id`, `action`, `repo`",
                        )
                    ]

                # Validate repo format (owner/repo)
                if not re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", feedback.repo):
                    return [
                        TextContent(
                            type="text",
                            text=f"❌ **Invalid repo format:** `{feedback.repo}`\n\nExpected format: `owner/repo`"
                        )
                    ]

                logger.info(
                    f"Submitting feedback for issue {feedback.issue_id}: {feedback.action.value}"
                )

                # Build request body
                body: dict = {
                    "action": feedback.action.value,
                    "repo": feedback.repo,
                    "timestamp": None,  # Let server set timestamp
                }

                # Add optional fields
                if feedback.file:
                    body["file"] = feedback.file
                if feedback.line:
                    body["line"] = feedback.line
                if feedback.pattern:
                    body["pattern"] = feedback.pattern
                if feedback.reason:
                    body["reason"] = feedback.reason

                response = await client.post(
                    f"{api_url}/api/v1/code-review/issues/{feedback.issue_id}/feedback",
                    headers=headers,
                    json=body,
                )

                if response.status_code in (200, 201):
                    action_emoji = {
                        "fixed": "✅",
                        "dismissed": "🔇",
                        "false_positive": "🚫",
                    }.get(feedback.action.value, "📝")

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
                elif response.status_code == 404:
                    return [
                        TextContent(
                            type="text",
                            text=f"❌ **Issue not found:** `{feedback.issue_id}`\n\nThe issue may not exist or has already been resolved.",
                        )
                    ]
                elif response.status_code == 403:
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
                else:
                    return [
                        TextContent(
                            type="text",
                            text=f"❌ **Feedback submission failed:** HTTP {response.status_code}\n{response.text[:200]}",
                        )
                    ]

            # ─────────────────────────────────────────────────────────────
            # CODE ENTITY INDEXING TOOLS
            # ─────────────────────────────────────────────────────────────
            elif name == "turingmind_index_codebase":
                repo = arguments.get("repo", "")
                if not repo:
                    return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]

                languages = arguments.get("languages", ["javascript", "typescript", "python"])
                force_reindex = arguments.get("force_reindex", False)

                try:
                    repo_path = get_repo_path()
                    if not repo_path:
                        return [
                            TextContent(
                                type="text",
                                text="❌ **Could not determine repository path**\n\nRun this from within a git repository.",
                            )
                        ]

                    indexer = EntityIndexer(repo_path)
                    result = indexer.index_codebase(languages=languages, force_reindex=force_reindex)

                    # Track indexing errors for reporting
                    failed_files = result.get("failed_files", [])
                    
                    # Store entities in database using transaction
                    db = get_db()
                    entity_id_map = {}  # Map (file_path, name, entity_type) to database entity_id
                    entities_stored = 0
                    relationships_stored = 0
                    
                    # Use transaction for atomic indexing
                    with db.transaction() as cursor:
                        # Clear existing entities if force_reindex
                        if force_reindex:
                            db.clear_entities_for_repo(repo, _cursor=cursor)
                        
                        # Store entities
                        for entity in result.get("entities", []):
                            db_entity_id = db.create_code_entity(
                                repo=repo,
                                file_path=entity["file_path"],
                                entity_type=entity["entity_type"],
                                name=entity["name"],
                                start_line=entity.get("start_line"),
                                end_line=entity.get("end_line"),
                                language=entity.get("language"),
                                _cursor=cursor,
                            )
                            entities_stored += 1
                            
                            # Map by composite key for relationship lookup
                            key = (entity["file_path"], entity["name"], entity["entity_type"])
                            entity_id_map[key] = db_entity_id
                            # Also map by indexer's entity_id string if present
                            indexer_entity_id = entity.get("entity_id")
                            if indexer_entity_id:
                                entity_id_map[indexer_entity_id] = db_entity_id
                        
                        # Prepare and store relationships
                        relationship_tuples = []
                        for rel in result.get("relationships", []):
                            source_entity_id_str = rel.get("source_entity_id", "")
                            source_id = None
                            
                            # Try to find source entity by indexer ID or by parsing
                            if source_entity_id_str in entity_id_map:
                                source_id = entity_id_map[source_entity_id_str]
                            else:
                                # Parse indexer ID format: "file_path:name:type"
                                if ":" in source_entity_id_str:
                                    parts = source_entity_id_str.split(":")
                                    if len(parts) >= 3:
                                        file_path = parts[0]
                                        name = parts[1]
                                        entity_type = ":".join(parts[2:])  # Handle types with colons
                                        key = (file_path, name, entity_type)
                                        source_id = entity_id_map.get(key)
                            
                            # Try to find target entity if target_entity_id is provided
                            target_id = None
                            target_entity_id_str = rel.get("target_entity_id")
                            if target_entity_id_str and target_entity_id_str in entity_id_map:
                                target_id = entity_id_map[target_entity_id_str]
                            
                            if source_id:  # Only store if source entity was found
                                relationship_tuples.append((
                                    repo,
                                    source_id,
                                    target_id,
                                    rel.get("target_symbol_name"),
                                    rel.get("relationship_type", "calls"),
                                ))
                        
                        # Batch store relationships
                        if relationship_tuples:
                            relationships_stored = db.create_relationship_batch(
                                relationship_tuples, _cursor=cursor
                            )
                    
                    # Build response with error reporting
                    response_parts = [
                        f"✅ **Codebase Indexed**\n\n",
                        f"- **Entities indexed:** {entities_stored}\n",
                        f"- **Languages:** {', '.join(languages)}\n",
                        f"- **Entity types:** {', '.join(f'{k}: {v}' for k, v in result['entities_by_type'].items())}\n",
                        f"- **Relationships:** {relationships_stored}\n",
                    ]
                    
                    if force_reindex:
                        response_parts.append(f"- **Mode:** Full re-index (previous data cleared)\n")
                    
                    if failed_files:
                        response_parts.append(f"\n⚠️ **{len(failed_files)} files failed to index:**\n")
                        for file_path, error in failed_files[:5]:  # Show first 5
                            response_parts.append(f"  - `{file_path}`: {error}\n")
                        if len(failed_files) > 5:
                            response_parts.append(f"  - ... and {len(failed_files) - 5} more\n")
                    
                    response_parts.append("\nCode entities are now available for relationship-aware reviews.")
                    
                    return [
                        TextContent(
                            type="text",
                            text="".join(response_parts),
                        )
                    ]
                except Exception as e:
                    logger.exception("Indexing failed")
                    return [
                        TextContent(
                            type="text", 
                            text=(
                                f"❌ **Indexing failed:** {type(e).__name__}: {e}\n\n"
                                f"This may indicate a parsing error or database issue. "
                                f"Try running with `force_reindex: true` to clear and rebuild the index."
                            )
                        )
                    ]

            elif name == "turingmind_get_related_code":
                repo = arguments.get("repo", "")
                file_path = arguments.get("file", "")
                if not repo or not file_path:
                    return [
                        TextContent(
                            type="text", text="❌ **Missing required fields:** `repo`, `file`"
                        )
                    ]

                entity_name = arguments.get("entity_name")
                relationship_types = arguments.get("relationship_types", ["calls", "imports"])
                direction = arguments.get("direction", "both")

                try:
                    db = get_db()
                    entities = db.get_entities_by_file(repo, file_path)

                    if entity_name:
                        # Find specific entity
                        entity = next(
                            (e for e in entities if e["name"] == entity_name), None
                        )
                        if not entity:
                            return [
                                TextContent(
                                    type="text",
                                    text=f"❌ **Entity not found:** `{entity_name}` in `{file_path}`",
                                )
                            ]
                        related = db.get_related_entities(
                            entity["entity_id"], relationship_types, direction
                        )
                    else:
                        # Get all related entities for file
                        related = []
                        for entity in entities:
                            related.extend(
                                db.get_related_entities(
                                    entity["entity_id"], relationship_types, direction
                                )
                            )

                    return [
                        TextContent(
                            type="text",
                            text=(
                                f"🔗 **Related Code Entities**\n\n"
                                f"- **File:** `{file_path}`\n"
                                f"- **Entity:** {entity_name or 'all'}\n"
                                f"- **Related entities:** {len(related)}\n\n"
                                + "\n".join(
                                    f"- `{r['file_path']}:{r['name']}` ({r.get('relationship_type', 'unknown')})"
                                    for r in related[:20]
                                )
                            ),
                        )
                    ]
                except Exception as e:
                    logger.exception("Get related code failed")
                    return [
                        TextContent(
                            type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}"
                        )
                    ]

            elif name == "turingmind_get_project_structure":
                repo = arguments.get("repo", "")
                if not repo:
                    return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]

                try:
                    db = get_db()
                    # Get entity counts using transaction context manager
                    with db.transaction() as cursor:
                        cursor.execute(
                            """
                            SELECT entity_type, language, COUNT(*) as count
                            FROM code_entities
                            WHERE repo = ?
                            GROUP BY entity_type, language
                            """,
                            (repo,),
                        )
                        stats = cursor.fetchall()

                    structure = {}
                    for row in stats:
                        entity_type = row[0]
                        language = row[1] or "unknown"
                        count = row[2]
                        if entity_type not in structure:
                            structure[entity_type] = {}
                        structure[entity_type][language] = count

                    return [
                        TextContent(
                            type="text",
                            text=(
                                f"📊 **Project Structure for {repo}**\n\n"
                                + "\n".join(
                                    f"**{et}:**\n"
                                    + "\n".join(f"  - {lang}: {count}" for lang, count in langs.items())
                                    for et, langs in structure.items()
                                )
                            ),
                        )
                    ]
                except Exception as e:
                    logger.exception("Get project structure failed")
                    return [
                        TextContent(
                            type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}"
                        )
                    ]

            # ─────────────────────────────────────────────────────────────
            # DEVELOPER INTENT TOOLS
            # ─────────────────────────────────────────────────────────────
            elif name == "turingmind_get_edit_reasoning":
                repo = arguments.get("repo", "")
                files = arguments.get("files", [])
                if not repo or not files:
                    return [
                        TextContent(
                            type="text", text="❌ **Missing required fields:** `repo`, `files`"
                        )
                    ]

                commit_message = arguments.get("commit_message", "")
                commit_hash = arguments.get("commit_hash")
                conversation_id = arguments.get("conversation_id")
                interactive = arguments.get("interactive", False)

                try:
                    db = get_db()
                    memory_manager = get_memory_manager()

                    # Extract reasoning from commit message if available
                    overall_intent = None
                    if commit_message:
                        # Try to extract "Why:" section
                        if "Why:" in commit_message:
                            overall_intent = commit_message.split("Why:")[1].strip().split("\n")[0]

                    # Process per-file reasoning
                    file_reasoning_map = {}
                    for file_obj in files:
                        file_path = file_obj.get("file_path")
                        reasoning = file_obj.get("reasoning")

                        if not reasoning and commit_message:
                            # Try to infer from commit message
                            reasoning = overall_intent

                        if reasoning:
                            file_reasoning_map[file_path] = {
                                "reasoning": reasoning,
                                "change_type": file_obj.get("change_type", "other"),
                                "memory_category": file_obj.get(
                                    "memory_category", "session_context"
                                ),
                                "scope": file_obj.get("scope", file_path),
                                "confidence": file_obj.get("confidence", 0.8),
                            }

                            # Create session context
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

                    # Save edit reasoning
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
                    logger.exception("Get edit reasoning failed")
                    return [
                        TextContent(
                            type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}"
                        )
                    ]

            # ─────────────────────────────────────────────────────────────
            # MEMORY MANAGEMENT TOOLS
            # ─────────────────────────────────────────────────────────────
            elif name == "turingmind_list_memory":
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

                try:
                    db = get_db()
                    entries = db.list_memory_entries(
                        repo=repo,
                        memory_type=category if category != "all" else None,
                        status=status if status != "all" else None,
                        scope=scope,
                        page=page,
                        limit=limit,
                        search=search,
                    )

                    # Filter by security tag if specified
                    if security_tag:
                        entries = [
                            e
                            for e in entries
                            if e.get("security_tags") and security_tag in e.get("security_tags", [])
                        ]

                    return [
                        TextContent(
                            type="text",
                            text=(
                                f"📚 **Memory Entries ({len(entries)})**\n\n"
                                + "\n".join(
                                    f"- **{e['type']}** [{e['status']}]: {e['content'][:60]}... "
                                    f"(scope: {e['scope']}, confidence: {e['confidence']:.2f})"
                                    for e in entries[:20]
                                )
                            ),
                        )
                    ]
                except Exception as e:
                    logger.exception("List memory failed")
                    return [
                        TextContent(
                            type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}"
                        )
                    ]

            elif name == "turingmind_get_memory":
                repo = arguments.get("repo", "")
                memory_id = arguments.get("memory_id", "")
                if not repo or not memory_id:
                    return [
                        TextContent(
                            type="text", text="❌ **Missing required fields:** `repo`, `memory_id`"
                        )
                    ]

                try:
                    db = get_db()
                    entry = db.get_memory_entry(memory_id)
                    if not entry:
                        return [
                            TextContent(
                                type="text", text=f"❌ **Memory entry not found:** `{memory_id}`"
                            )
                        ]

                    evidence = db.get_evidence(memory_id)

                    return [
                        TextContent(
                            type="text",
                            text=(
                                f"📖 **Memory Entry Details**\n\n"
                                f"- **ID:** {memory_id}\n"
                                f"- **Type:** {entry['type']}\n"
                                f"- **Content:** {entry['content']}\n"
                                f"- **Scope:** {entry['scope']}\n"
                                f"- **Confidence:** {entry['confidence']:.2f}\n"
                                f"- **Status:** {entry['status']}\n"
                                f"- **Evidence:** {len(evidence)} items\n"
                                + "\n".join(
                                    f"  - {e['evidence_type']}: {e['content'][:50]}..."
                                    for e in evidence[:5]
                                )
                            ),
                        )
                    ]
                except Exception as e:
                    logger.exception("Get memory failed")
                    return [
                        TextContent(
                            type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}"
                        )
                    ]

            elif name == "turingmind_save_memory":
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

                try:
                    memory_manager = get_memory_manager()
                    memory_id = arguments.get("memory_id")

                    if memory_id:
                        # Update existing
                        db = get_db()
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
                                TextContent(
                                    type="text", text=f"❌ **Memory entry not found:** `{memory_id}`"
                                )
                            ]
                    else:
                        # Create new
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
                            db = get_db()
                            memory_id = db.create_memory_entry(
                                repo=repo,
                                memory_type=memory_type,
                                content=content,
                                scope=scope,
                                confidence=arguments.get("confidence", 0.8),
                                security_tags=arguments.get("security_tags"),
                                yaml_definition=arguments.get("yaml_definition"),
                            )

                    # Add evidence if provided
                    if arguments.get("evidence"):
                        db = get_db()
                        for ev in arguments["evidence"]:
                            db.add_evidence(
                                memory_id=memory_id,
                                evidence_type=ev.get("type", "manual"),
                                content=ev.get("content", ""),
                                file_path=ev.get("file"),
                                line_number=ev.get("line"),
                            )

                    return [
                        TextContent(
                            type="text",
                            text=(
                                f"✅ **Memory Entry Saved**\n\n"
                                f"- **ID:** {memory_id}\n"
                                f"- **Type:** {memory_type}\n"
                                f"- **Content:** {content[:100]}...\n"
                                f"- **Scope:** {scope}"
                            ),
                        )
                    ]
                except Exception as e:
                    logger.exception("Save memory failed")
                    return [
                        TextContent(
                            type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}"
                        )
                    ]

            elif name == "turingmind_delete_memory":
                repo = arguments.get("repo", "")
                memory_id = arguments.get("memory_id", "")
                action = arguments.get("action", "deprecate")
                if not repo or not memory_id:
                    return [
                        TextContent(
                            type="text", text="❌ **Missing required fields:** `repo`, `memory_id`"
                        )
                    ]

                try:
                    db = get_db()
                    success = db.delete_memory_entry(memory_id, deprecate=(action == "deprecate"))
                    if not success:
                        return [
                            TextContent(
                                type="text", text=f"❌ **Memory entry not found:** `{memory_id}`"
                            )
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
                    logger.exception("Delete memory failed")
                    return [
                        TextContent(
                            type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}"
                        )
                    ]

            elif name == "turingmind_detect_conflicts":
                repo = arguments.get("repo", "")
                memory_id = arguments.get("memory_id", "")
                if not repo or not memory_id:
                    return [
                        TextContent(
                            type="text", text="❌ **Missing required fields:** `repo`, `memory_id`"
                        )
                    ]

                try:
                    memory_manager = get_memory_manager()
                    conflicts = memory_manager.detect_conflicts(repo, memory_id)

                    if not conflicts:
                        return [
                            TextContent(
                                type="text", text="✅ **No conflicts detected**"
                            )
                        ]

                    return [
                        TextContent(
                            type="text",
                            text=(
                                f"⚠️ **Conflicts Detected ({len(conflicts)})**\n\n"
                                + "\n".join(
                                    f"- **{c['type']}** [{c['severity']}]: {c.get('description', 'N/A')}"
                                    for c in conflicts
                                )
                            ),
                        )
                    ]
                except Exception as e:
                    logger.exception("Detect conflicts failed")
                    return [
                        TextContent(
                            type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}"
                        )
                    ]

            elif name == "turingmind_resolve_conflict":
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

                try:
                    db = get_db()
                    success = db.resolve_conflict(conflict_id, strategy)
                    if not success:
                        return [
                            TextContent(
                                type="text", text=f"❌ **Conflict not found:** `{conflict_id}`"
                            )
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
                    logger.exception("Resolve conflict failed")
                    return [
                        TextContent(
                            type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}"
                        )
                    ]

            elif name == "turingmind_simulate_impact":
                repo = arguments.get("repo", "")
                memory_ids = arguments.get("memory_ids", [])
                test_files = arguments.get("test_files")

                if not repo:
                    return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]

                return [
                    TextContent(
                        type="text",
                        text=(
                            f"🔮 **Impact Simulation**\n\n"
                            f"This feature simulates how memory entries affect code review.\n"
                            f"**Note:** Full simulation requires integration with review engine.\n\n"
                            f"- **Repository:** {repo}\n"
                            f"- **Memory IDs:** {len(memory_ids)}\n"
                            f"- **Test files:** {len(test_files) if test_files else 'auto'}"
                        ),
                    )
                ]

            elif name == "turingmind_explain_decision":
                repo = arguments.get("repo", "")
                issue_id = arguments.get("issue_id")
                file_path = arguments.get("file")
                line = arguments.get("line")

                if not repo:
                    return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]

                try:
                    db = get_db()
                    usage = db.get_memory_usage(
                        repo=repo, issue_id=issue_id, file_path=file_path, line_number=line
                    )

                    if not usage:
                        return [
                            TextContent(
                                type="text", text="ℹ️ **No memory usage found for this decision**"
                            )
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
                    logger.exception("Explain decision failed")
                    return [
                        TextContent(
                            type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}"
                        )
                    ]

            elif name == "turingmind_get_memory_stats":
                repo = arguments.get("repo", "")
                if not repo:
                    return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]

                try:
                    db = get_db()
                    # Get memory statistics using transaction context manager
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

                    result_text = f"📊 **Memory Statistics for {repo}**\n\n"
                    for row in stats:
                        result_text += f"- **{row[0]}** [{row[1]}]: {row[2]}\n"

                    return [TextContent(type="text", text=result_text)]
                except Exception as e:
                    logger.exception("Get memory stats failed")
                    return [
                        TextContent(
                            type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}"
                        )
                    ]

            elif name == "turingmind_enable_auto_review":
                repo = arguments.get("repo", "")
                branch = arguments.get("branch", "main")
                review_type = arguments.get("review_type", "quick")
                enabled = arguments.get("enabled", True)

                if not repo:
                    return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]

                try:
                    db = get_db()
                    memory_manager = get_memory_manager()
                    api_url, api_key = get_config()
                    
                    auto_review_service = get_auto_review_service(
                        db=db,
                        memory_manager=memory_manager,
                        api_url=api_url,
                        api_key=api_key,
                    )

                    if enabled:
                        success = await auto_review_service.start_monitoring(
                            repo=repo,
                            branch=branch,
                            review_type=review_type,
                            poll_interval=60,  # Poll every 60 seconds
                        )
                        if success:
                            return [
                                TextContent(
                                    type="text",
                                    text=(
                                        f"✅ **Auto-Review Enabled**\n\n"
                                        f"- **Repository:** {repo}\n"
                                        f"- **Branch:** {branch}\n"
                                        f"- **Review type:** {review_type}\n"
                                        f"- **Poll interval:** 60 seconds\n\n"
                                        f"Monitoring for new commits and triggering automatic reviews."
                                    ),
                                )
                            ]
                        else:
                            return [
                                TextContent(
                                    type="text",
                                    text=f"⚠️ **Already monitoring** {repo}",
                                )
                            ]
                    else:
                        success = await auto_review_service.stop_monitoring(repo)
                        if success:
                            return [
                                TextContent(
                                    type="text",
                                    text=f"✅ **Auto-Review Disabled** for {repo}",
                                )
                            ]
                        else:
                            return [
                                TextContent(
                                    type="text",
                                    text=f"⚠️ **Not monitoring** {repo}",
                                )
                            ]
                except Exception as e:
                    logger.exception("Enable auto-review failed")
                    return [
                        TextContent(
                            type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}"
                        )
                    ]

            # ─────────────────────────────────────────────────────────────
            # UNKNOWN TOOL
            # ─────────────────────────────────────────────────────────────
            else:
                return [
                    TextContent(
                        type="text",
                        text=(
                            f"❌ **Unknown tool:** `{name}`\n\n"
                            f"Available tools:\n"
                            f"- `turingmind_initiate_login` - Start login flow\n"
                            f"- `turingmind_poll_login` - Complete login\n"
                            f"- `turingmind_validate_auth` - Check auth status\n"
                            f"- `turingmind_upload_review` - Upload review results\n"
                            f"- `turingmind_get_context` - Get memory context\n"
                            f"- `turingmind_submit_feedback` - Submit issue feedback"
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
            logger.exception(f"Tool {name} failed")
            return [TextContent(type="text", text=f"❌ **Error:** {type(e).__name__}: {e}")]


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
