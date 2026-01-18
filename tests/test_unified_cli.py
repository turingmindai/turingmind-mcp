"""
Tests for Unified CLI Tool

Tests the turingmind unified CLI command.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from turingmind_mcp.unified_cli import diagnose, setup_platform, validate_config


class TestUnifiedCLI:
    """Test unified CLI functionality."""

    def test_setup_platform_claude_desktop(self):
        """Test setting up Claude Desktop."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("turingmind_mcp.unified_cli.ConfigManager") as mock_manager_class:
                mock_manager = MagicMock()
                mock_manager_class.return_value = mock_manager
                mock_manager.get_claude_desktop_config_path.return_value = Path(tmpdir) / "config.json"
                mock_manager.add_mcp_server.return_value = True

                # Mock shutil.which to return True (turingmind-mcp found)
                with patch("shutil.which", return_value="/usr/bin/turingmind-mcp"):
                    result = setup_platform("claude_desktop", project_root=Path(tmpdir))
                    assert result == 0

    def test_setup_platform_cursor(self):
        """Test setting up Cursor."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("turingmind_mcp.unified_cli.ConfigManager") as mock_manager_class:
                mock_manager = MagicMock()
                mock_manager_class.return_value = mock_manager
                mock_manager.get_cursor_config_path.return_value = Path(tmpdir) / ".cursor" / "mcp.json"
                mock_manager.add_mcp_server.return_value = True

                with patch("shutil.which", return_value="/usr/bin/turingmind-mcp"):
                    result = setup_platform("cursor", project_root=Path(tmpdir))
                    assert result == 0

    def test_validate_config_valid(self):
        """Test validating valid config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            with patch("turingmind_mcp.unified_cli.ConfigManager") as mock_manager_class:
                mock_manager = MagicMock()
                mock_manager_class.return_value = mock_manager
                mock_manager.get_claude_desktop_config_path.return_value = config_path
                mock_manager.validate_config.return_value = (True, [])

                result = validate_config("claude_desktop", project_root=Path(tmpdir))
                assert result == 0

    def test_validate_config_invalid(self):
        """Test validating invalid config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            with patch("turingmind_mcp.unified_cli.ConfigManager") as mock_manager_class:
                mock_manager = MagicMock()
                mock_manager_class.return_value = mock_manager
                mock_manager.get_claude_desktop_config_path.return_value = config_path
                mock_manager.validate_config.return_value = (
                    False,
                    ["Missing 'mcpServers' key"],
                )

                result = validate_config("claude_desktop", project_root=Path(tmpdir))
                assert result == 1

    @patch("sys.version_info")
    @patch("shutil.which")
    @patch("subprocess.run")
    def test_diagnose_success(self, mock_run, mock_which, mock_version):
        """Test diagnose command success."""
        # Mock Python version
        mock_version.major = 3
        mock_version.minor = 10

        # Mock turingmind-mcp found
        mock_which.return_value = "/usr/bin/turingmind-mcp"

        # Mock subprocess success
        mock_run.return_value = MagicMock(returncode=0)

        # Mock ConfigManager
        with patch("turingmind_mcp.unified_cli.ConfigManager") as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.return_value = mock_manager
            mock_manager.get_claude_desktop_config_path.return_value = Path("/test/config.json")
            mock_manager.get_claude_cli_config_path.return_value = Path("/test/mcp.json")
            mock_manager.get_cursor_config_path.return_value = Path("/test/.cursor/mcp.json")
            mock_manager.validate_config.return_value = (True, [])
            mock_manager.get_turingmind_config.return_value = {"command": "turingmind-mcp"}

            result = diagnose()
            assert result == 0

    @patch("sys.version_info")
    def test_diagnose_python_version_fail(self, mock_version):
        """Test diagnose with incompatible Python version."""
        mock_version.major = 3
        mock_version.minor = 9  # Too old

        result = diagnose()
        assert result == 1
