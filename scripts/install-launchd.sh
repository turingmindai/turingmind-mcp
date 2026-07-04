#!/usr/bin/env bash
# Install the TuringMind V2 API server as a launchd user agent (macOS).
#
# The agent keeps uvicorn alive on 127.0.0.1:8477 across crashes and reboots,
# so hook events stop dead-ending when a terminal session tears the server
# down. Logs go to ~/.turingmind/api-server.log.
#
# Usage:   ./scripts/install-launchd.sh            # install + start
#          ./scripts/install-launchd.sh uninstall  # stop + remove

set -euo pipefail

LABEL="com.turingmind.api"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$REPO_DIR/.venv/bin/python"
PORT="${TURINGMIND_API_PORT:-8477}"
LOG_DIR="$HOME/.turingmind"

if [ "${1:-}" = "uninstall" ]; then
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Uninstalled ${LABEL}."
    exit 0
fi

if [ ! -x "$PYTHON" ]; then
    echo "Error: $PYTHON not found. Create the venv first:" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install -e ." >&2
    exit 1
fi

mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>turingmind_mcp.api_server:app</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>${PORT}</string>
        <string>--app-dir</string>
        <string>${REPO_DIR}/src</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>5</integer>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/api-server.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/api-server.log</string>
</dict>
</plist>
PLIST_EOF

# Reload cleanly if already installed
launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "Installed ${LABEL} (port ${PORT})."
echo "Waiting for health..."
for i in $(seq 1 10); do
    if curl -sf -m 2 "http://127.0.0.1:${PORT}/api/v2/health" > /dev/null 2>&1; then
        echo "API server healthy on ${PORT}."
        exit 0
    fi
    sleep 1
done
echo "Warning: server not responding yet — check ${LOG_DIR}/api-server.log" >&2
exit 1
