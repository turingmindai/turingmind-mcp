"""Tool handlers for TuringMind MCP. Each tool name maps to an async handler (arguments, context) -> list[TextContent]."""

from __future__ import annotations

from typing import Any, Callable

from mcp.types import TextContent

from .context import ToolContext

# Type for async handler: (arguments: dict, context: ToolContext) -> list[TextContent]
ToolHandler = Callable[[dict[str, Any], ToolContext], Any]

# Registry populated by register_all()
HANDLERS: dict[str, ToolHandler] = {}


def register_all() -> None:
    """Import all tool modules so they register their handlers."""
    from . import auth, auto_plan, chat_analysis, code_index, code_review, edit_tools, login, memory
    from ..v2_engine import handlers as v2_handlers

    login.register(HANDLERS)
    auth.register(HANDLERS)
    code_review.register(HANDLERS)
    code_index.register(HANDLERS)
    edit_tools.register(HANDLERS)
    memory.register(HANDLERS)
    auto_plan.register(HANDLERS)
    chat_analysis.register(HANDLERS)
    # v2 Constraint Engine — 14 new tools registered last so they take precedence
    v2_handlers.register(HANDLERS)


def get_handler(name: str) -> ToolHandler | None:
    """Return the handler for the given tool name, or None."""
    return HANDLERS.get(name)
