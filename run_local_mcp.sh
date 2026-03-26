#!/bin/bash
# Run MCP server using LOCAL source code (not pipx-installed version)
# This ensures any local changes (like the datetime fix) are used

cd "$(dirname "$0")"

# Use pipx Python (has all dependencies) but load from local source
export PYTHONPATH="${PWD}/src:${PYTHONPATH}"

# Run using pipx Python but local source
exec /Users/turingmindai/.local/pipx/venvs/turingmind-mcp/bin/python -m turingmind_mcp.server "$@"
