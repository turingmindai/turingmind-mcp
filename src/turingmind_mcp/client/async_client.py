"""
Asynchronous MCP Client for TuringMind

Provides async interface for calling TuringMind MCP tools.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

import logging

logger = logging.getLogger("turingmind-mcp.client")


class AsyncTuringMindMCPClient:
    """
    Asynchronous MCP client for TuringMind.
    
    Communicates with TuringMind MCP server via stdio protocol using asyncio.
    """

    def __init__(
        self,
        command: str = "turingmind-mcp",
        args: Optional[list[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize async MCP client.
        
        Args:
            command: Command to run MCP server
            args: Command arguments
            env: Environment variables
        """
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.process: Optional[asyncio.subprocess.Process] = None
        self.request_id = 0
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start MCP server process."""
        if self.process is not None:
            logger.warning("MCP server process already started")
            return

        try:
            # Prepare environment
            import os

            env = os.environ.copy()
            env.update(self.env)

            # Start process
            self.process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            logger.info(f"Started MCP server: {self.command}")
        except Exception as e:
            logger.error(f"Failed to start MCP server: {e}")
            raise

    async def stop(self) -> None:
        """Stop MCP server process."""
        if self.process is None:
            return

        try:
            self.process.terminate()
            await asyncio.wait_for(self.process.wait(), timeout=5.0)
            logger.info("Stopped MCP server")
        except asyncio.TimeoutError:
            logger.warning("MCP server did not terminate, forcing kill")
            self.process.kill()
            await self.process.wait()
        except Exception as e:
            logger.error(f"Error stopping MCP server: {e}")
        finally:
            self.process = None

    async def _send_request(
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

        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("MCP server process streams not available")

        async with self._lock:
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
                await self.process.stdin.drain()
            except Exception as e:
                logger.error(f"Failed to send request: {e}")
                raise

            # Read response
            response_line = await self.process.stdout.readline()
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

    async def list_tools(self) -> list[Dict[str, Any]]:
        """
        List available MCP tools.
        
        Returns:
            List of tool definitions
        """
        result = await self._send_request("tools/list")
        return result.get("tools", [])

    async def call_tool(
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

        result = await self._send_request("tools/call", params)
        return result.get("content", [])

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()
