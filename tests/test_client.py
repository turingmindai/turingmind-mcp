"""
Tests for MCP Client

Tests synchronous and asynchronous MCP client functionality.
"""

import asyncio
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from turingmind_mcp.client import AsyncTuringMindMCPClient, TuringMindMCPClient


class TestTuringMindMCPClient:
    """Test synchronous MCP client."""

    def test_client_initialization(self):
        """Test client initialization."""
        client = TuringMindMCPClient(
            command="echo",
            args=["test"],
            env={"TEST": "value"},
        )

        assert client.command == "echo"
        assert client.args == ["test"]
        assert client.env == {"TEST": "value"}
        assert client.process is None

    @patch("subprocess.Popen")
    def test_start(self, mock_popen):
        """Test starting MCP server."""
        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        client = TuringMindMCPClient(command="test-command")
        client.start()

        assert client.process is not None
        mock_popen.assert_called_once()

    def test_stop_no_process(self):
        """Test stopping when no process."""
        client = TuringMindMCPClient()
        # Should not raise
        client.stop()

    @patch("subprocess.Popen")
    def test_context_manager(self, mock_popen):
        """Test context manager usage."""
        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        with TuringMindMCPClient(command="test-command") as client:
            assert client.process is not None

        # Process should be stopped
        mock_process.terminate.assert_called()
        mock_process.wait.assert_called()


class TestAsyncTuringMindMCPClient:
    """Test asynchronous MCP client."""

    def test_client_initialization(self):
        """Test async client initialization."""
        client = AsyncTuringMindMCPClient(
            command="echo",
            args=["test"],
            env={"TEST": "value"},
        )

        assert client.command == "echo"
        assert client.args == ["test"]
        assert client.env == {"TEST": "value"}
        assert client.process is None

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Test starting and stopping async client."""
        client = AsyncTuringMindMCPClient(command="echo", args=["test"])

        # Mock subprocess creation
        with patch("asyncio.create_subprocess_exec") as mock_create:
            mock_process = MagicMock()
            mock_process.stdin = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_create.return_value = mock_process

            await client.start()
            assert client.process is not None

            await client.stop()
            mock_process.terminate.assert_called()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test async context manager usage."""
        with patch("asyncio.create_subprocess_exec") as mock_create:
            mock_process = MagicMock()
            mock_process.stdin = MagicMock()
            mock_process.stdout = MagicMock()
            mock_process.stderr = MagicMock()
            mock_create.return_value = mock_process

            async with AsyncTuringMindMCPClient(command="echo") as client:
                assert client.process is not None

            # Process should be stopped
            mock_process.terminate.assert_called()
