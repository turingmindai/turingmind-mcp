"""
Configuration Management System

Handles multi-platform MCP server configuration:
- Claude Desktop: claude_desktop_config.json
- Claude Code CLI: mcp.json (project root)
- Cursor: .cursor/mcp.json (project root)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import logging

logger = logging.getLogger("turingmind-mcp")


class ConfigManager:
    """Manages MCP server configuration across platforms."""

    # Platform-specific config file locations
    CONFIG_PATHS = {
        "claude_desktop": {
            "macos": Path.home() / "Library/Application Support/Claude/claude_desktop_config.json",
            "windows": Path(os.environ.get("APPDATA", "")) / "Claude/claude_desktop_config.json",
            "linux": Path.home() / ".config/Claude/claude_desktop_config.json",
        },
        "claude_cli": {
            "project": Path("mcp.json"),  # Relative to project root
        },
        "cursor": {
            "project": Path(".cursor/mcp.json"),  # Relative to project root
        },
    }

    def __init__(self, project_root: Optional[Path] = None):
        """
        Initialize config manager.
        
        Args:
            project_root: Project root directory (for project-relative configs)
        """
        self.project_root = project_root or Path.cwd()
        self.platform = self._detect_platform()

    @staticmethod
    def _detect_platform() -> str:
        """Detect operating system platform."""
        import sys

        if sys.platform == "darwin":
            return "macos"
        elif sys.platform.startswith("win"):
            return "windows"
        else:
            return "linux"

    def get_claude_desktop_config_path(self) -> Path:
        """Get Claude Desktop config file path."""
        return self.CONFIG_PATHS["claude_desktop"][self.platform]

    def get_claude_cli_config_path(self) -> Path:
        """Get Claude CLI config file path (project root)."""
        return self.project_root / self.CONFIG_PATHS["claude_cli"]["project"]

    def get_cursor_config_path(self) -> Path:
        """Get Cursor config file path (project root)."""
        return self.project_root / self.CONFIG_PATHS["cursor"]["project"]

    def read_config(self, config_path: Path) -> Dict[str, Any]:
        """
        Read configuration from file.
        
        Args:
            config_path: Path to config file
            
        Returns:
            Config dictionary
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            json.JSONDecodeError: If config is invalid JSON
        """
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        try:
            with open(config_path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(
                f"Invalid JSON in config file {config_path}: {e.msg}",
                e.doc,
                e.pos,
            ) from e

    def write_config(self, config_path: Path, config: Dict[str, Any]) -> None:
        """
        Write configuration to file.
        
        Args:
            config_path: Path to config file
            config: Config dictionary
        """
        # Create parent directory if needed
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Backup existing config if it exists
        if config_path.exists():
            backup_path = config_path.with_suffix(config_path.suffix + ".backup")
            try:
                with open(config_path, "r") as src, open(backup_path, "w") as dst:
                    dst.write(src.read())
                logger.info(f"Backed up existing config to {backup_path}")
            except Exception as e:
                logger.warning(f"Failed to backup config: {e}")

        # Write new config
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        logger.info(f"Wrote config to {config_path}")

    def get_mcp_server_config(
        self, server_name: str = "turingmind"
    ) -> Optional[Dict[str, Any]]:
        """
        Get MCP server configuration from config file.
        
        Args:
            server_name: Name of MCP server
            
        Returns:
            Server config or None if not found
        """
        config = self.read_config(self.get_claude_desktop_config_path())
        return config.get("mcpServers", {}).get(server_name)

    def add_mcp_server(
        self,
        config_path: Path,
        server_name: str,
        command: str,
        args: Optional[list[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        Add MCP server to configuration file.
        
        Args:
            config_path: Path to config file
            server_name: Name of MCP server
            command: Command to run MCP server
            args: Command arguments
            env: Environment variables
            
        Returns:
            True if added, False if already exists
        """
        # Read existing config or create new
        try:
            config = self.read_config(config_path)
        except FileNotFoundError:
            config = {}

        # Initialize mcpServers if needed
        if "mcpServers" not in config:
            config["mcpServers"] = {}

        # Check if server already exists
        if server_name in config["mcpServers"]:
            logger.warning(f"MCP server '{server_name}' already exists in config")
            return False

        # Add server config
        server_config: Dict[str, Any] = {"command": command}
        if args:
            server_config["args"] = args
        if env:
            server_config["env"] = env

        config["mcpServers"][server_name] = server_config

        # Write config
        self.write_config(config_path, config)
        return True

    def update_mcp_server(
        self,
        config_path: Path,
        server_name: str,
        command: Optional[str] = None,
        args: Optional[list[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        Update existing MCP server configuration.
        
        Args:
            config_path: Path to config file
            server_name: Name of MCP server
            command: Command to run MCP server (optional)
            args: Command arguments (optional)
            env: Environment variables (optional)
            
        Returns:
            True if updated, False if not found
        """
        try:
            config = self.read_config(config_path)
        except FileNotFoundError:
            logger.error(f"Config file not found: {config_path}")
            return False

        if "mcpServers" not in config or server_name not in config["mcpServers"]:
            logger.error(f"MCP server '{server_name}' not found in config")
            return False

        # Update server config
        server_config = config["mcpServers"][server_name]
        if command:
            server_config["command"] = command
        if args is not None:
            server_config["args"] = args
        if env is not None:
            server_config["env"] = env

        # Write config
        self.write_config(config_path, config)
        return True

    def remove_mcp_server(self, config_path: Path, server_name: str) -> bool:
        """
        Remove MCP server from configuration.
        
        Args:
            config_path: Path to config file
            server_name: Name of MCP server
            
        Returns:
            True if removed, False if not found
        """
        try:
            config = self.read_config(config_path)
        except FileNotFoundError:
            return False

        if "mcpServers" not in config or server_name not in config["mcpServers"]:
            return False

        del config["mcpServers"][server_name]

        # Write config
        self.write_config(config_path, config)
        return True

    def validate_config(self, config_path: Path) -> tuple[bool, list[str]]:
        """
        Validate configuration file.
        
        Args:
            config_path: Path to config file
            
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        try:
            config = self.read_config(config_path)
        except FileNotFoundError:
            return False, [f"Config file not found: {config_path}"]
        except json.JSONDecodeError as e:
            return False, [f"Invalid JSON: {e}"]

        # Validate structure
        if "mcpServers" not in config:
            errors.append("Missing 'mcpServers' key")

        if "mcpServers" in config:
            if not isinstance(config["mcpServers"], dict):
                errors.append("'mcpServers' must be an object")

            for server_name, server_config in config["mcpServers"].items():
                if not isinstance(server_config, dict):
                    errors.append(f"Server '{server_name}' config must be an object")
                    continue

                if "command" not in server_config:
                    errors.append(f"Server '{server_name}' missing 'command'")

                if "args" in server_config and not isinstance(
                    server_config["args"], list
                ):
                    errors.append(f"Server '{server_name}' 'args' must be a list")

                if "env" in server_config and not isinstance(
                    server_config["env"], dict
                ):
                    errors.append(f"Server '{server_name}' 'env' must be an object")

        return len(errors) == 0, errors

    def get_turingmind_config(
        self, platform: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get TuringMind MCP server config for specific platform.
        
        Args:
            platform: Platform name (claude_desktop, claude_cli, cursor)
            
        Returns:
            Server config or None
        """
        if platform == "claude_desktop":
            config_path = self.get_claude_desktop_config_path()
        elif platform == "claude_cli":
            config_path = self.get_claude_cli_config_path()
        elif platform == "cursor":
            config_path = self.get_cursor_config_path()
        else:
            logger.error(f"Unknown platform: {platform}")
            return None

        try:
            config = self.read_config(config_path)
            return config.get("mcpServers", {}).get("turingmind")
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.error(f"Error reading config: {e}")
            return None
