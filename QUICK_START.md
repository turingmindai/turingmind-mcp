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

**Next Steps:**
1. Restart Cursor IDE
2. Verify: Settings → MCP → Check "turingmind" is active
3. Say: "Log me into TuringMind"

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
4. **Check platform docs**: See `docs/platforms/` for detailed guides

## Documentation

- **Platform Guides**: `docs/platforms/`
- **Integration Assessment**: `docs/INTEGRATION_ASSESSMENT.md`
- **Implementation Summary**: `IMPLEMENTATION_SUMMARY.md`

## Support

For platform-specific help, see the troubleshooting sections in each platform guide.
