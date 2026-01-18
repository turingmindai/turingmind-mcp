"""
Tests for Error Handling

Tests error handling and platform-specific messages.
"""

import pytest

from turingmind_mcp.errors import (
    AuthenticationError,
    ConfigError,
    ConnectionError,
    ToolError,
    TuringMindMCPError,
    handle_error,
)


class TestErrorClasses:
    """Test error class functionality."""

    def test_base_error(self):
        """Test base error class."""
        error = TuringMindMCPError("Test error", platform="macos")
        assert error.message == "Test error"
        assert error.platform == "macos"
        assert error.troubleshooting is not None

    def test_config_error(self):
        """Test config error."""
        error = ConfigError("Config error", config_path="/test/path", platform="macos")
        assert error.config_path == "/test/path"
        assert "Config file" in error.troubleshooting

    def test_connection_error(self):
        """Test connection error."""
        error = ConnectionError("Connection failed", command="test-cmd", platform="linux")
        assert error.command == "test-cmd"
        assert "turingmind-mcp" in error.troubleshooting

    def test_authentication_error(self):
        """Test authentication error."""
        error = AuthenticationError("Auth failed", platform="windows")
        assert "login" in error.troubleshooting.lower()

    def test_tool_error(self):
        """Test tool error."""
        error = ToolError("Tool failed", tool_name="test_tool", platform="macos")
        assert error.tool_name == "test_tool"
        assert "Tool" in error.troubleshooting

    def test_error_str_formatting(self):
        """Test error string formatting includes troubleshooting."""
        error = ConfigError("Test error", config_path="/test")
        error_str = str(error)
        assert "Test error" in error_str
        assert "Troubleshooting" in error_str


class TestErrorHandler:
    """Test error handling utility."""

    def test_handle_turingmind_error(self):
        """Test handling TuringMind-specific errors."""
        error = ConfigError("Config error", config_path="/test")
        result = handle_error(error)
        assert "Config error" in result
        assert "Troubleshooting" in result

    def test_handle_file_not_found(self):
        """Test handling FileNotFoundError."""
        error = FileNotFoundError("/test/path")
        result = handle_error(error, context="test context")
        assert "Config file not found" in result
        assert "test context" in result

    def test_handle_permission_error(self):
        """Test handling PermissionError."""
        error = PermissionError("/test/path")
        result = handle_error(error)
        assert "Permission denied" in result

    def test_handle_generic_error(self):
        """Test handling generic errors."""
        error = ValueError("Generic error")
        result = handle_error(error, context="test")
        assert "Generic error" in result
        assert "test" in result
