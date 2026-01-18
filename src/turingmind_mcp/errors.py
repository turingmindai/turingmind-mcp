"""
Enhanced Error Handling

Provides platform-specific error messages and troubleshooting guidance.
"""

from __future__ import annotations

import platform
from typing import Optional

import logging

logger = logging.getLogger("turingmind-mcp")


class TuringMindMCPError(Exception):
    """Base exception for TuringMind MCP errors."""

    def __init__(
        self,
        message: str,
        platform: Optional[str] = None,
        troubleshooting: Optional[str] = None,
    ):
        """
        Initialize error.
        
        Args:
            message: Error message
            platform: Platform name (for platform-specific guidance)
            troubleshooting: Troubleshooting steps
        """
        super().__init__(message)
        self.message = message
        self.platform = platform or self._detect_platform()
        self.troubleshooting = troubleshooting or self._get_default_troubleshooting()

    @staticmethod
    def _detect_platform() -> str:
        """Detect current platform."""
        system = platform.system().lower()
        if system == "darwin":
            return "macos"
        elif system == "windows":
            return "windows"
        else:
            return "linux"

    def _get_default_troubleshooting(self) -> str:
        """Get default troubleshooting steps."""
        return "See documentation at https://docs.turingmind.ai/mcp"

    def __str__(self) -> str:
        """Format error message with troubleshooting."""
        msg = f"{self.message}"
        if self.troubleshooting:
            msg += f"\n\nTroubleshooting:\n{self.troubleshooting}"
        return msg


class ConfigError(TuringMindMCPError):
    """Configuration-related errors."""

    def __init__(
        self,
        message: str,
        config_path: Optional[str] = None,
        platform: Optional[str] = None,
    ):
        """
        Initialize config error.
        
        Args:
            message: Error message
            config_path: Path to config file
            platform: Platform name
        """
        troubleshooting = self._get_config_troubleshooting(config_path, platform)
        super().__init__(message, platform, troubleshooting)
        self.config_path = config_path

    @staticmethod
    def _get_config_troubleshooting(
        config_path: Optional[str], platform: Optional[str]
    ) -> str:
        """Get config-specific troubleshooting."""
        steps = [
            "1. Verify the config file exists and is readable",
            "2. Check JSON syntax is valid",
            "3. Ensure 'mcpServers' key exists",
            "4. Verify 'turingmind' server is configured",
        ]

        if config_path:
            steps.append(f"5. Check config file: {config_path}")

        if platform == "macos":
            steps.append(
                "6. Config location: ~/Library/Application Support/Claude/claude_desktop_config.json"
            )
        elif platform == "windows":
            steps.append(
                "6. Config location: %APPDATA%\\Claude\\claude_desktop_config.json"
            )
        else:
            steps.append("6. Config location: ~/.config/Claude/claude_desktop_config.json")

        return "\n".join(steps)


class ConnectionError(TuringMindMCPError):
    """MCP server connection errors."""

    def __init__(
        self,
        message: str,
        command: Optional[str] = None,
        platform: Optional[str] = None,
    ):
        """
        Initialize connection error.
        
        Args:
            message: Error message
            command: MCP server command
            platform: Platform name
        """
        troubleshooting = self._get_connection_troubleshooting(command, platform)
        super().__init__(message, platform, troubleshooting)
        self.command = command

    @staticmethod
    def _get_connection_troubleshooting(
        command: Optional[str], platform: Optional[str]
    ) -> str:
        """Get connection-specific troubleshooting."""
        steps = [
            "1. Verify turingmind-mcp is installed: pip install turingmind-mcp",
            "2. Check turingmind-mcp is in PATH: which turingmind-mcp",
            "3. Test MCP server manually: turingmind-mcp --help",
        ]

        if command:
            steps.append(f"4. Verify command works: {command} --help")

        steps.extend([
            "5. Check Python version: python --version (requires 3.10+)",
            "6. Verify dependencies: pip list | grep turingmind-mcp",
        ])

        return "\n".join(steps)


class AuthenticationError(TuringMindMCPError):
    """Authentication-related errors."""

    def __init__(
        self,
        message: str,
        platform: Optional[str] = None,
    ):
        """
        Initialize authentication error.
        
        Args:
            message: Error message
            platform: Platform name
        """
        troubleshooting = self._get_auth_troubleshooting(platform)
        super().__init__(message, platform, troubleshooting)

    @staticmethod
    def _get_auth_troubleshooting(platform: Optional[str]) -> str:
        """Get auth-specific troubleshooting."""
        steps = [
            "1. Run login flow: In Claude/Cursor, say 'Log me into TuringMind'",
            "2. Check API key exists: cat ~/.turingmind/config",
            "3. Verify API key format: Should start with 'tmk_'",
            "4. Test API key: turingmind-mcp --validate-auth",
            "5. Re-login if needed: Delete ~/.turingmind/config and re-login",
        ]

        return "\n".join(steps)


class ToolError(TuringMindMCPError):
    """MCP tool execution errors."""

    def __init__(
        self,
        message: str,
        tool_name: Optional[str] = None,
        platform: Optional[str] = None,
    ):
        """
        Initialize tool error.
        
        Args:
            message: Error message
            tool_name: Tool name that failed
            platform: Platform name
        """
        troubleshooting = self._get_tool_troubleshooting(tool_name, platform)
        super().__init__(message, platform, troubleshooting)
        self.tool_name = tool_name

    @staticmethod
    def _get_tool_troubleshooting(
        tool_name: Optional[str], platform: Optional[str]
    ) -> str:
        """Get tool-specific troubleshooting."""
        steps = [
            "1. Verify MCP server is running",
            "2. Check tool is available: List tools in MCP client",
            "3. Verify tool arguments are correct",
            "4. Check MCP server logs for errors",
        ]

        if tool_name:
            steps.append(f"5. Tool: {tool_name}")

        steps.extend([
            "6. Restart MCP server if needed",
            "7. Check platform compatibility",
        ])

        return "\n".join(steps)


def handle_error(error: Exception, context: Optional[str] = None) -> str:
    """
    Handle error and return user-friendly message.
    
    Args:
        error: Exception that occurred
        context: Additional context
        
    Returns:
        Formatted error message
    """
    if isinstance(error, TuringMindMCPError):
        return str(error)

    # Handle common exceptions
    if isinstance(error, FileNotFoundError):
        return str(
            ConfigError(
                f"Config file not found: {error.filename}",
                config_path=error.filename,
            )
        )

    if isinstance(error, PermissionError):
        return str(
            ConfigError(
                f"Permission denied: {error.filename}",
                config_path=error.filename,
            )
        )

    if isinstance(error, ConnectionError):
        return str(
            ConnectionError(
                f"Connection failed: {error}",
            )
        )

    # Generic error
    msg = f"Error: {error}"
    if context:
        msg += f"\nContext: {context}"
    return msg
