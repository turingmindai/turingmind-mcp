# Quick Start Guide

## Installation

```bash
pip install turingmind-mcp
```

## Choose Your Platform

### Claude Desktop

```bash
# Option 1: Unified CLI (Recommended)
turingmind setup claude_desktop

# Option 2: Script
./scripts/setup-claude-desktop.sh

# Option 3: Manual
# Edit: ~/Library/Application Support/Claude/claude_desktop_config.json
# Add: {"mcpServers": {"turingmind": {"command": "turingmind-mcp"}}}
```

**Next Steps:**
1. Restart Claude Desktop
2. Say: "Log me into TuringMind"

### Cursor IDE/CLI

```bash
# Option 1: Unified CLI (Recommended)
turingmind setup cursor

# Option 2: Script
./scripts/setup-cursor.sh

# Option 3: Manual
# Create: .cursor/mcp.json in project root
# Add: {"mcpServers": {"turingmind": {"command": "turingmind-mcp"}}}
```

#### Cursor Build Mode plugin (background API server)

Plugin **hooks** call `http://127.0.0.1:8477` — separate from MCP stdio. Install once per machine:

```bash
cd /path/to/turingmind-mcp
python3 -m venv .venv
.venv/bin/python -m pip install -e .

# macOS: launchd (survives crash + reboot)
bash ./scripts/install-launchd.sh
# or: turingmind install-api-daemon

# verify (use -m 3 so a stuck server doesn't hang your terminal)
curl -m 3 http://127.0.0.1:8477/api/v2/health
turingmind install-api-daemon status
```

Optional env file (cloud sync, default repo) — loaded by launchd **and** `api_server`:

```bash
mkdir -p ~/.turingmind
cat >> ~/.turingmind/env <<'EOF'
TURINGMIND_DEFAULT_REPO=org/repo-name
TURINGMIND_LOCAL_API_URL=http://127.0.0.1:8477
TURINGMIND_CLOUD_SYNC=1
TURINGMIND_API_URL=https://api.turingmind.ai
TURINGMIND_API_KEY=tmk_...
TURINGMIND_BRANCH_MEMORY=1
TURINGMIND_INGEST_KEY=generate-with-openssl-rand-hex-16
TURINGMIND_WORKSPACE_DIR=/path/to/your/git/repo
EOF
bash ./scripts/install-launchd.sh   # re-run to refresh launchd env
```

CI ingest from GitHub Actions (self-hosted runner or machine with daemon):

```bash
# scripts/post-ci-observation.sh — see turingmind-mcp/scripts/
export TURINGMIND_INGEST_KEY=...  # same value as in ~/.turingmind/env
REPO=org/repo BRANCH=feature/x HEAD_SHA=$GITHUB_SHA PR_NUMBER=42 \
  ./scripts/post-ci-observation.sh success
```

Add to `~/.zshrc` so **Cursor plugin hooks** see machine paths (repo id is auto-detected from git):

```bash
export TURINGMIND_MCP_PYTHON="/path/to/turingmind-mcp/.venv/bin/python3"
export TURINGMIND_LOCAL_API_URL="http://127.0.0.1:8477"
```

Optional fallback for **non-git folders only** — put in `~/.turingmind/env`, not shell:

```bash
TURINGMIND_DEFAULT_REPO=org/repo-name
```

**Next Steps:**
1. Restart Cursor IDE
2. Verify: Settings → MCP → Check "turingmind" is active
3. Load plugin from `turingmind-cursor-plugin` repo root
4. Say: "Log me into TuringMind"

### Claude Code CLI

```bash
# Option 1: Unified CLI
turingmind setup claude_cli

# Option 2: Script
./scripts/setup-claude-cli.sh

# Option 3: Skills (Alternative)
/plugin install tmind@tmind
/tmind:setup
```

**Next Steps:**
1. Verify: `claude mcp`
2. Test: `claude -p "Review my code" --allowedTools "turingmind_*"`

### Claude SDK (Python)

```python
from turingmind_mcp.client import TuringMindMCPClient

with TuringMindMCPClient() as client:
    context = client.call_tool(
        "turingmind_get_context",
        {"repo": "owner/repo"}
    )
```

## Verify Installation

```bash
# Diagnose installation
turingmind diagnose

# Validate config
turingmind validate claude_desktop
turingmind validate cursor
```

## Troubleshooting

If something doesn't work:

1. **Check installation**: `pip show turingmind-mcp`
2. **Verify command**: `turingmind-mcp --help`
3. **Run diagnostics**: `turingmind diagnose`
4. **Plugin hooks / :8477**: `turingmind install-api-daemon status` then `tail ~/.turingmind/api-server.err.log`
5. **Check platform docs**: See `docs/platforms/` for detailed guides

## Documentation

- **Platform Guides**: `docs/platforms/`
- **Features**: `docs/FEATURES.md`
- **Development**: `docs/DEVELOPMENT.md`

## Support

For platform-specific help, see the troubleshooting sections in each platform guide.
