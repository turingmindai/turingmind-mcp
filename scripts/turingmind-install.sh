#!/usr/bin/env bash
# TuringMind unified installer — Memory (default) or Governed profile.
#
# Usage:
#   ./scripts/turingmind-install.sh [--profile memory|governed] [--repo /path/to/project]
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${TURINGMIND_PROFILE:-memory}"
TARGET_REPO=""
INSTALL_HOOKS=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      PROFILE="${2:-memory}"
      shift 2
      ;;
    --repo)
      TARGET_REPO="${2:-}"
      shift 2
      ;;
    --no-hooks)
      INSTALL_HOOKS=0
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--profile memory|governed] [--repo PATH] [--no-hooks]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ "$PROFILE" != "memory" && "$PROFILE" != "governed" ]]; then
  echo "Invalid profile: $PROFILE (use memory or governed)" >&2
  exit 1
fi

echo "==> TuringMind install (profile: $PROFILE)"

PYTHON="${REPO_DIR}/.venv/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
  echo "==> Creating venv..."
  python3 -m venv "${REPO_DIR}/.venv"
  PYTHON="${REPO_DIR}/.venv/bin/python3"
fi

echo "==> Installing turingmind-mcp (editable)..."
"$PYTHON" -m pip install -q -e "${REPO_DIR}"

echo "==> Writing ~/.turingmind/env ..."
"$PYTHON" - <<PY
from turingmind_mcp.profile_config import write_profile_env
path = write_profile_env("${PROFILE}", mcp_python="${PYTHON}")
print(f"   {path}")
PY

echo "==> Installing launchd API server (macOS)..."
if [[ "$(uname -s)" == "Darwin" ]]; then
  bash "${REPO_DIR}/scripts/install-launchd.sh" install || true
else
  echo "   (skipped — not macOS; start manually: python3 -m turingmind_mcp.api_server)"
fi

echo "==> Health check..."
sleep 2
if curl -sf "http://127.0.0.1:8477/api/v2/health" >/dev/null; then
  echo "   API healthy on :8477"
else
  echo "   WARNING: API not reachable yet — run: bash scripts/install-launchd.sh status"
fi

PLUGIN_ROOT="$(cd "${REPO_DIR}/../../turingmind-cursor-plugin" 2>/dev/null && pwd || true)"
if [[ -n "$TARGET_REPO" && -n "$PLUGIN_ROOT" && "$INSTALL_HOOKS" == "1" && "$PROFILE" == "governed" ]]; then
  echo "==> Installing git pre-push hook (governed — blocking)..."
  bash "${PLUGIN_ROOT}/scripts/install-git-hooks.sh" "$TARGET_REPO"
elif [[ -n "$TARGET_REPO" && -n "$PLUGIN_ROOT" && "$INSTALL_HOOKS" == "1" ]]; then
  echo "==> Installing git pre-push hook (memory — warn only)..."
  bash "${PLUGIN_ROOT}/scripts/install-git-hooks.sh" "$TARGET_REPO"
fi

echo ""
echo "Done. Profile: $PROFILE"
echo "  Env file:   ~/.turingmind/env"
echo "  API:        http://127.0.0.1:8477"
echo "  Smoke test: bash scripts/smoke-memory.sh"
if [[ "$PROFILE" == "memory" ]]; then
  echo "  Plugin:     sideload turingmind-cursor-plugin/plugins/turingmind-memory"
else
  echo "  Plugin:     sideload turingmind-cursor-plugin/plugins/turingmind-build-mode"
fi
