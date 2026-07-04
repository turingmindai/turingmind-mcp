#!/usr/bin/env bash
# Post a branch-scoped CI observation to the local V2 API (TC-BR-32 / external CI).
#
# Requires:
#   TURINGMIND_LOCAL_API_URL  (default http://127.0.0.1:8477)
#   TURINGMIND_INGEST_KEY       (must match the API daemon env)
#
# Example (GitHub Actions self-hosted runner on a dev machine):
#   env REPO=org/repo BRANCH="${{ github.head_ref }}" HEAD_SHA="${{ github.sha }}" \
#       PR_NUMBER="${{ github.event.pull_request.number }}" \
#       ./scripts/post-ci-observation.sh success
set -euo pipefail

API_URL="${TURINGMIND_LOCAL_API_URL:-http://127.0.0.1:8477}"
INGEST_KEY="${TURINGMIND_INGEST_KEY:-}"
REPO="${REPO:?set REPO=owner/name}"
BRANCH="${BRANCH:?set BRANCH=feature/foo}"
HEAD_SHA="${HEAD_SHA:?set HEAD_SHA=40-char commit sha}"
PR_NUMBER="${PR_NUMBER:?set PR_NUMBER=123}"
CONCLUSION="${1:-success}"
CHECK_NAME="${CHECK_NAME:-ci_workflow}"

if [ -z "$INGEST_KEY" ]; then
  echo "TURINGMIND_INGEST_KEY is required" >&2
  exit 1
fi

curl -sf -X POST "${API_URL%/}/api/v2/observations/ci" \
  -H "Content-Type: application/json" \
  -H "X-TuringMind-Ingest-Key: ${INGEST_KEY}" \
  -d "$(cat <<EOF
{
  "repo": "${REPO}",
  "branch": "${BRANCH}",
  "head_sha": "${HEAD_SHA}",
  "pr_number": ${PR_NUMBER},
  "check_name": "${CHECK_NAME}",
  "conclusion": "${CONCLUSION}"
}
EOF
)"

echo
