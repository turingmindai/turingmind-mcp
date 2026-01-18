"""
Integration Tests

End-to-end integration tests for platform configurations.
"""

import json
import tempfile
from pathlib import Path

import pytest

from turingmind_mcp.config_manager import ConfigManager


class TestPlatformIntegration:
    """Test platform-specific integrations."""

    def test_claude_desktop_config_creation(self):
        """Test creating Claude Desktop config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Mock the config path
            config_path = Path(tmpdir) / "claude_desktop_config.json"

            manager = ConfigManager()
            # Override the path for testing
            original_method = manager.get_claude_desktop_config_path
            manager.get_claude_desktop_config_path = lambda: config_path

            # Add server
            success = manager.add_mcp_server(
                config_path=config_path,
                server_name="turingmind",
                command="turingmind-mcp",
                env={"TURINGMIND_API_URL": "https://api.turingmind.ai"},
            )

            assert success is True
            assert config_path.exists()

            # Verify structure
            config = manager.read_config(config_path)
            assert "mcpServers" in config
            assert "turingmind" in config["mcpServers"]
            assert config["mcpServers"]["turingmind"]["command"] == "turingmind-mcp"

    def test_claude_cli_config_creation(self):
        """Test creating Claude CLI config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            config_path = project_root / "mcp.json"

            manager = ConfigManager(project_root=project_root)

            # Add server
            success = manager.add_mcp_server(
                config_path=config_path,
                server_name="turingmind",
                command="turingmind-mcp",
                args=[],
                env={},
            )

            assert success is True
            assert config_path.exists()

            # Verify structure
            config = manager.read_config(config_path)
            assert "mcpServers" in config
            assert "turingmind" in config["mcpServers"]
            assert "args" in config["mcpServers"]["turingmind"]

    def test_cursor_config_creation(self):
        """Test creating Cursor config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            cursor_dir = project_root / ".cursor"
            config_path = cursor_dir / "mcp.json"

            manager = ConfigManager(project_root=project_root)

            # Add server
            success = manager.add_mcp_server(
                config_path=config_path,
                server_name="turingmind",
                command="turingmind-mcp",
                args=[],
                env={},
            )

            assert success is True
            assert config_path.exists()
            assert cursor_dir.exists()

            # Verify structure
            config = manager.read_config(config_path)
            assert "mcpServers" in config
            assert "turingmind" in config["mcpServers"]

    def test_config_merge_preserves_existing(self):
        """Test that adding server preserves existing config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"

            # Create existing config with other servers
            existing_config = {
                "mcpServers": {
                    "other-server": {
                        "command": "other-command",
                    }
                }
            }

            manager = ConfigManager()
            manager.write_config(config_path, existing_config)

            # Add turingmind server
            manager.add_mcp_server(
                config_path=config_path,
                server_name="turingmind",
                command="turingmind-mcp",
            )

            # Verify both servers exist
            config = manager.read_config(config_path)
            assert "other-server" in config["mcpServers"]
            assert "turingmind" in config["mcpServers"]

    def test_config_validation_all_platforms(self):
        """Test config validation for all platforms."""
        valid_config = {
            "mcpServers": {
                "turingmind": {
                    "command": "turingmind-mcp",
                    "args": [],
                    "env": {"TEST": "value"},
                }
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ConfigManager(project_root=Path(tmpdir))

            # Test all platform config paths
            configs = [
                Path(tmpdir) / "claude_desktop.json",
                Path(tmpdir) / "claude_cli.json",
                Path(tmpdir) / "cursor.json",
            ]

            for config_path in configs:
                manager.write_config(config_path, valid_config)
                is_valid, errors = manager.validate_config(config_path)
                assert is_valid is True
                assert len(errors) == 0
