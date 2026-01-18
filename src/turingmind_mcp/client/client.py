"""
Synchronous MCP Client for TuringMind

Provides synchronous interface for calling TuringMind MCP tools.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, Optional

import logging

logger = logging.getLogger("turingmind-mcp.client")


class TuringMindMCPClient:
    """
    Synchronous MCP client for TuringMind.
    
    Communicates with TuringMind MCP server via stdio protocol.
    """

    def __init__(
        self,
        command: str = "turingmind-mcp",
        args: Optional[list[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize MCP client.
        
        Args:
            command: Command to run MCP server
            args: Command arguments
            env: Environment variables
        """
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.process: Optional[subprocess.Popen] = None
        self.request_id = 0

    def start(self) -> None:
        """Start MCP server process."""
        if self.process is not None:
            logger.warning("MCP server process already started")
            return

        try:
            # Prepare environment
            env = os.environ.copy()
            env.update(self.env)

            # Start process
            self.process = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=False,  # Use bytes for stdin/stdout
            )

            logger.info(f"Started MCP server: {self.command}")
        except Exception as e:
            logger.error(f"Failed to start MCP server: {e}")
            raise

    def stop(self) -> None:
        """Stop MCP server process."""
        if self.process is None:
            return

        try:
            self.process.terminate()
            self.process.wait(timeout=5)
            logger.info("Stopped MCP server")
        except subprocess.TimeoutExpired:
            logger.warning("MCP server did not terminate, forcing kill")
            self.process.kill()
            self.process.wait()
        except Exception as e:
            logger.error(f"Error stopping MCP server: {e}")
        finally:
            self.process = None

    def _send_request(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Send JSON-RPC request to MCP server.
        
        Args:
            method: JSON-RPC method name
            params: Method parameters
            
        Returns:
            Response dictionary
        """
        if self.process is None:
            raise RuntimeError("MCP server not started. Call start() first.")

        # Generate request ID
        self.request_id += 1
        request_id = self.request_id

        # Build request
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params:
            request["params"] = params

        # Send request
        request_json = json.dumps(request) + "\n"
        try:
            self.process.stdin.write(request_json.encode())
            self.process.stdin.flush()
        except Exception as e:
            logger.error(f"Failed to send request: {e}")
            raise

        # Read response
        response_line = self.process.stdout.readline()
        if not response_line:
            raise RuntimeError("No response from MCP server")

        try:
            response = json.loads(response_line.decode())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse response: {e}")
            raise

        # Check for errors
        if "error" in response:
            error = response["error"]
            raise RuntimeError(
                f"MCP error: {error.get('message', 'Unknown error')} "
                f"(code: {error.get('code', 'unknown')})"
            )

        return response.get("result", {})

    def list_tools(self) -> list[Dict[str, Any]]:
        """
        List available MCP tools.
        
        Returns:
            List of tool definitions
        """
        result = self._send_request("tools/list")
        return result.get("tools", [])

    def call_tool(
        self, name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> list[Dict[str, Any]]:
        """
        Call MCP tool.
        
        Args:
            name: Tool name
            arguments: Tool arguments
            
        Returns:
            Tool result (list of content items)
        """
        params = {"name": name}
        if arguments:
            params["arguments"] = arguments

        result = self._send_request("tools/call", params)
        return result.get("content", [])

    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()

    def __del__(self):
        """Cleanup on deletion."""
        if self.process is not None:
            try:
                self.stop()
            except Exception:
                pass
