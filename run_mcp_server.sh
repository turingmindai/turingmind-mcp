#!/bin/bash
# Wrapper script to run MCP server from local source
cd "$(dirname "$0")"
export PYTHONPATH="${PWD}/src:${PYTHONPATH}"
exec python3 -m turingmind_mcp.server "$@"
