#!/usr/bin/env bash
# Install the TuringMind V2 API server as a launchd user agent (macOS).
#
# Usage:   ./scripts/install-launchd.sh            # install + start
#          ./scripts/install-launchd.sh uninstall  # stop + remove
#          ./scripts/install-launchd.sh status     # check health
#
# Env overrides: ~/.turingmind/env (see QUICK_START.md)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${REPO_DIR}/.venv/bin/python3"

if [ ! -x "$PYTHON" ]; then
  PYTHON="python3"
fi

case "${1:-install}" in
  uninstall)
    exec "$PYTHON" -m turingmind_mcp.daemon_setup uninstall
    ;;
  status)
    exec "$PYTHON" -m turingmind_mcp.daemon_setup status
    ;;
  install|"")
    exec "$PYTHON" -m turingmind_mcp.daemon_setup install
    ;;
  *)
    echo "Usage: $0 [install|uninstall|status]" >&2
    exit 1
    ;;
esac
