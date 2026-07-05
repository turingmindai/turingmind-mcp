#!/usr/bin/env bash
# End-to-end smoke test for Memory profile REST paths (no Cursor required).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${REPO_DIR}/.venv/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi

API_URL="${TURINGMIND_LOCAL_API_URL:-http://127.0.0.1:8477}"
TEST_REPO="smoke/test-repo-$$"
SCOPE="memory"

echo "==> Smoke test (repo=$TEST_REPO, api=$API_URL)"

if ! curl -sf "${API_URL}/api/v2/health" >/dev/null; then
  echo "ERROR: API not healthy at ${API_URL}" >&2
  echo "Start with: bash scripts/install-launchd.sh install" >&2
  exit 1
fi

echo "==> POST /api/v2/memory (learned_pattern)"
CREATE=$(curl -sf -X POST "${API_URL}/api/v2/memory" \
  -H "Content-Type: application/json" \
  -d "{\"repo\":\"${TEST_REPO}\",\"type\":\"learned_pattern\",\"content\":\"JWT validation belongs in middleware\",\"scope\":\"auth/middleware.py\"}")
echo "$CREATE" | "$PYTHON" -c "import json,sys; d=json.load(sys.stdin); assert d.get('memory_id'), d"

echo "==> GET /api/v2/memory (search)"
SEARCH=$(curl -sf "${API_URL}/api/v2/memory?repo=${TEST_REPO}&search=JWT")
echo "$SEARCH" | "$PYTHON" -c "import json,sys; d=json.load(sys.stdin); assert len(d.get('entries',[]))>=1, d"

echo "==> POST /api/v2/observations"
curl -sf -X POST "${API_URL}/api/v2/observations" \
  -H "Content-Type: application/json" \
  -d "{\"repo\":\"${TEST_REPO}\",\"observations\":[{\"event_type\":\"edit_cluster\",\"content\":\"smoke/targeted_fix\",\"source\":\"smoke-test\",\"confidence\":0.3}]}" >/dev/null

echo "==> POST /api/v2/reconcile"
curl -sf -X POST "${API_URL}/api/v2/reconcile" \
  -H "Content-Type: application/json" \
  -d "{\"repo\":\"${TEST_REPO}\"}" >/dev/null || true

echo "==> GET /api/v2/decision-queue?scope=memory"
QUEUE=$(curl -sf "${API_URL}/api/v2/decision-queue?repo=${TEST_REPO}&scope=${SCOPE}&limit=10")
echo "$QUEUE" | "$PYTHON" -c "import json,sys; d=json.load(sys.stdin); assert d.get('scope')=='memory', d"

echo "==> Reinforcement (duplicate learned_pattern)"
curl -sf -X POST "${API_URL}/api/v2/memory" \
  -H "Content-Type: application/json" \
  -d "{\"repo\":\"${TEST_REPO}\",\"type\":\"learned_pattern\",\"content\":\"JWT validation belongs in middleware\",\"scope\":\"auth/middleware.py\"}" >/dev/null

echo ""
echo "OK — smoke-memory passed"
