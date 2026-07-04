#!/usr/bin/env bash
# Launchd-friendly wrapper for the V2 API server.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${TURINGMIND_PYTHON:-$REPO_DIR/.venv/bin/python3}"
PORT="${TURINGMIND_API_PORT:-8477}"
LOG_DIR="${HOME}/.turingmind"

mkdir -p "$LOG_DIR"

if [ ! -x "$PYTHON" ]; then
  echo "Python not found: $PYTHON" >&2
  exit 1
fi

exec "$PYTHON" -m uvicorn turingmind_mcp.api_server:app \
  --host 127.0.0.1 \
  --port "$PORT" \
  --app-dir "$REPO_DIR/src"
