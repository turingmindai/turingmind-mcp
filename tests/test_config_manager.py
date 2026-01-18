"""
Tests for Configuration Manager

Tests multi-platform configuration management functionality.
"""

import json
import tempfile
from pathlib import Path

import pytest

from turingmind_mcp.config_manager import ConfigManager


class TestConfigManager:
    """Test configuration manager functionality."""

    def test_platform_detection(self):
        """Test platform detection."""
        manager = ConfigManager()
        platform = manager._detect_platform()
        assert platform in ["macos", "windows", "linux"]

    def test_read_write_config(self):
        """Test reading and writing config files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.json"
            manager = ConfigManager(project_root=Path(tmpdir))

            # Write config
            test_config = {"mcpServers": {"test": {"command": "test-cmd"}}}
            manager.write_config(config_path, test_config)

            # Read config
            read_config = manager.read_config(config_path)
            assert read_config == test_config

    def test_config_validation_valid(self):
        """Test validation of valid config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.json"
            manager = ConfigManager(project_root=Path(tmpdir))

            valid_config = {
                "mcpServers": {
                    "turingmind": {
                        "command": "turingmind-mcp",
                        "args": [],
                        "env": {},
                    }
                }
            }
            manager.write_config(config_path, valid_config)

            is_valid, errors = manager.validate_config(config_path)
            assert is_valid is True
            assert len(errors) == 0

    def test_config_validation_invalid(self):
        """Test validation of invalid config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.json"
            manager = ConfigManager(project_root=Path(tmpdir))

            # Missing mcpServers
            invalid_config = {}
            manager.write_config(config_path, invalid_config)

            is_valid, errors = manager.validate_config(config_path)
            assert is_valid is False
            assert len(errors) > 0
            assert "Missing 'mcpServers' key" in errors

    def test_add_mcp_server(self):
        """Test adding MCP server to config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.json"
            manager = ConfigManager(project_root=Path(tmpdir))

            # Create empty config
            manager.write_config(config_path, {})

            # Add server
            success = manager.add_mcp_server(
                config_path=config_path,
                server_name="turingmind",
                command="turingmind-mcp",
                env={"TEST": "value"},
            )

            assert success is True

            # Verify
            config = manager.read_config(config_path)
            assert "turingmind" in config["mcpServers"]
            assert config["mcpServers"]["turingmind"]["command"] == "turingmind-mcp"
            assert config["mcpServers"]["turingmind"]["env"]["TEST"] == "value"

    def test_add_mcp_server_duplicate(self):
        """Test adding duplicate MCP server."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.json"
            manager = ConfigManager(project_root=Path(tmpdir))

            # Add server first time
            manager.write_config(config_path, {})
            manager.add_mcp_server(
                config_path=config_path,
                server_name="turingmind",
                command="turingmind-mcp",
            )

            # Try to add again
            success = manager.add_mcp_server(
                config_path=config_path,
                server_name="turingmind",
                command="turingmind-mcp",
            )

            assert success is False  # Should fail because already exists

    def test_update_mcp_server(self):
        """Test updating existing MCP server."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.json"
            manager = ConfigManager(project_root=Path(tmpdir))

            # Add server
            manager.write_config(config_path, {})
            manager.add_mcp_server(
                config_path=config_path,
                server_name="turingmind",
                command="turingmind-mcp",
            )

            # Update server
            success = manager.update_mcp_server(
                config_path=config_path,
                server_name="turingmind",
                command="turingmind-mcp-updated",
                env={"NEW": "value"},
            )

            assert success is True

            # Verify
            config = manager.read_config(config_path)
            assert config["mcpServers"]["turingmind"]["command"] == "turingmind-mcp-updated"
            assert config["mcpServers"]["turingmind"]["env"]["NEW"] == "value"

    def test_remove_mcp_server(self):
        """Test removing MCP server."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.json"
            manager = ConfigManager(project_root=Path(tmpdir))

            # Add server
            manager.write_config(config_path, {})
            manager.add_mcp_server(
                config_path=config_path,
                server_name="turingmind",
                command="turingmind-mcp",
            )

            # Remove server
            success = manager.remove_mcp_server(config_path, "turingmind")

            assert success is True

            # Verify
            config = manager.read_config(config_path)
            assert "turingmind" not in config.get("mcpServers", {})

    def test_backup_creation(self):
        """Test backup is created when writing config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.json"
            manager = ConfigManager(project_root=Path(tmpdir))

            # Write initial config
            initial_config = {"test": "initial"}
            manager.write_config(config_path, initial_config)

            # Write updated config (should create backup)
            updated_config = {"test": "updated"}
            manager.write_config(config_path, updated_config)

            # Check backup exists
            backup_path = config_path.with_suffix(config_path.suffix + ".backup")
            assert backup_path.exists()

            # Verify backup has original content
            backup_config = manager.read_config(backup_path)
            assert backup_config == initial_config

    def test_get_claude_desktop_config_path(self):
        """Test getting Claude Desktop config path."""
        manager = ConfigManager()
        path = manager.get_claude_desktop_config_path()
        assert isinstance(path, Path)
        # Path should be absolute
        assert path.is_absolute()

    def test_get_claude_cli_config_path(self):
        """Test getting Claude CLI config path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ConfigManager(project_root=Path(tmpdir))
            path = manager.get_claude_cli_config_path()
            assert path == Path(tmpdir) / "mcp.json"

    def test_get_cursor_config_path(self):
        """Test getting Cursor config path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ConfigManager(project_root=Path(tmpdir))
            path = manager.get_cursor_config_path()
            assert path == Path(tmpdir) / ".cursor" / "mcp.json"
