"""
TuringMind MCP Server

Model Context Protocol server for TuringMind cloud integration.
Provides type-safe tools for Claude to authenticate, upload code reviews,
fetch repository context, and submit feedback.
"""

__version__ = "0.2.0"
__author__ = "TuringMind"
__email__ = "support@turingmind.ai"

from turingmind_mcp.server import main

__all__ = ["main", "__version__"]
