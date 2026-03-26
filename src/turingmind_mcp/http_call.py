#!/usr/bin/env python3
"""
HTTP bridge for TuringMind MCP tools.

Exposes POST /call with body { "tool": str, "arguments": dict } and returns { "result": ... }.
Used by the Build Mode plugin E2E tests (TURINGMIND_MCP_URL). Run with:

  python -m turingmind_mcp.http_call [--port 8080] [--host 127.0.0.1]

If you get ModuleNotFoundError for 'turingmind_mcp', install the package (pip install -e .).
If you get ModuleNotFoundError for 'mcp', install deps too: use a venv and run
  pip install -e .   then   python -m turingmind_mcp.http_call [--port 8080] [--host 127.0.0.1]

Contract (matches turingmind-build-plugin tests/e2e/createTransport.ts):
  POST /call
  Body: { "tool": "<name>", "arguments": { ... } }
  Response: { "result": <first TextContent.text> } or { "error": "<message>" }
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logging.basicConfig(
    level=logging.DEBUG if os.environ.get("TURINGMIND_DEBUG") else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("turingmind-mcp-http")


def _run_tool_sync(tool: str, arguments: dict[str, Any]) -> Any:
    """Run the MCP tool in a new event loop (for use from sync HTTP handler)."""
    from turingmind_mcp.server import call_tool

    async def _run() -> Any:
        contents = await call_tool(tool, arguments)
        if not contents:
            return None
        text = getattr(contents[0], "text", None)
        if text is None:
            return None
        # If the tool returned JSON string, parse so client gets object when possible
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    return asyncio.run(_run())


class CallHandler(BaseHTTPRequestHandler):
    """Handle POST /call for tool invocation."""

    def do_OPTIONS(self) -> None:
        self._send_cors_headers(204)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/call":
            self.send_error(404, "Not Found")
            return
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            self._send_json(400, {"error": f"Invalid JSON body: {e}"})
            return
        tool = data.get("tool")
        arguments = data.get("arguments")
        if not tool or not isinstance(arguments, dict):
            self._send_json(400, {"error": "Missing 'tool' (string) or 'arguments' (object)"})
            return
        try:
            result = _run_tool_sync(tool, arguments)
        except Exception as e:
            logger.exception("Tool %s failed", tool)
            self._send_json(500, {"error": str(e)})
            return
        self._send_json(200, {"result": result})

    def _send_cors_headers(self, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status: int, obj: dict[str, Any]) -> None:
        self._send_cors_headers(status)
        self.end_headers()
        self.wfile.write(json.dumps(obj, default=str).encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        logger.info(format % args)


def run_http_server(host: str = "127.0.0.1", port: int = 8080) -> None:
    """Run the HTTP server (blocking)."""
    server = HTTPServer((host, port), CallHandler)
    logger.info("HTTP tool bridge listening on http://%s:%s/call", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="TuringMind MCP HTTP bridge (POST /call)")
    p.add_argument("--host", default=os.environ.get("TURINGMIND_HTTP_HOST", "127.0.0.1"), help="Bind host")
    p.add_argument("--port", type=int, default=int(os.environ.get("TURINGMIND_HTTP_PORT", "8080")), help="Bind port")
    args = p.parse_args()
    run_http_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
