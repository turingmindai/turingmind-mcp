# Claude Code CLI Integration

## Overview

Claude Code CLI supports MCP servers in two ways:
1. **MCP Configuration** (New): Direct MCP server integration
2. **Skills System** (Current): Via Claude Code Skills

## Method 1: MCP Configuration (Recommended)

### Setup

1. **Install TuringMind-MCP**:
```bash
pip install turingmind-mcp
```

2. **Create MCP Config**:
Create `mcp.json` in your project root:

```json
{
  "mcpServers": {
    "turingmind": {
      "command": "turingmind-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

3. **Verify MCP Servers**:
```bash
claude mcp
```

### Usage

**Headless Mode**:
```bash
claude -p "Review my code" --allowedTools "turingmind_*" --permission-mode acceptEdits
```

**Interactive Mode**:
```bash
claude
# Then in Claude: "Get memory context for owner/repo"
```

## Method 2: Skills System (Current)

### Setup

1. **Add Marketplace**:
```bash
/plugin marketplace add turingmindai/tmind
```

2. **Install Skill**:
```bash
/plugin install tmind@tmind
```

3. **Setup MCP Server**:
```bash
/tmind:setup
```

4. **Login**:
```bash
/tmind:login
```

### Usage

**Quick Review**:
```bash
/tmind:review
```

**Deep Review**:
```bash
/tmind:deep-review
```

**Git Hooks**:
The pre-commit and pre-push hooks automatically use `/tmind:review`:
```bash
git commit  # Pre-commit hook runs automatically
```

## Git Hooks Integration

### Pre-Commit Hook

Automatically reviews staged changes before commit:

```bash
# Install hook
cp tmind/hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

**What it does**:
- Captures developer intent (per-file reasoning)
- Runs `/tmind:review` on staged files
- Blocks commit if Critical issues found
- Stores reasoning in database

### Pre-Push Hook

Reviews changes before pushing:

```bash
# Install hook
cp tmind/hooks/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

**What it does**:
- Extracts reasoning from commit messages
- Analyzes impact with memory context
- Blocks push if Critical issues found

## Developer Intent Capture

### Per-File Reasoning

```bash
git config --local tmind.reasoning.src/auth.py "Fix SQL injection vulnerability"
git config --local tmind.reasoning.src/middleware.py "Add rate limiting"
```

### Overall Reasoning

```bash
git config --local tmind.reasoning "Refactoring authentication module"
```

### Commit Message Reasoning

```bash
git commit -m "Fix authentication bug

Why: Prevent SQL injection by using parameterized queries"
```

## Troubleshooting

### Claude CLI Not Found

**Error**: `claude: command not found`

**Solution**:
1. Install Claude Code CLI
2. Verify installation: `claude --version`
3. Check PATH includes Claude CLI

### MCP Server Not Detected

**Error**: `claude mcp` shows no servers

**Solution**:
1. Verify `mcp.json` exists in project root
2. Check JSON syntax is valid
3. Verify `turingmind-mcp` is in PATH
4. Test: `turingmind-mcp --help`

### Skills Not Working

**Error**: `/tmind:review` not found

**Solution**:
1. Verify skill is installed: `/plugin list`
2. Reinstall: `/plugin install tmind@tmind`
3. Restart Claude Code CLI
4. Run setup: `/tmind:setup`

## Comparison: MCP Config vs Skills

| Feature | MCP Config | Skills |
|---------|-----------|--------|
| Setup | Manual config | Automated |
| Git Hooks | Manual setup | Included |
| Commands | Direct tool calls | `/tmind:review` |
| Flexibility | High | Medium |
| Ease of Use | Medium | High |

**Recommendation**: Use Skills for quick setup, MCP Config for advanced control.

## Advanced Usage

### Custom Environment Variables

```json
{
  "mcpServers": {
    "turingmind": {
      "command": "turingmind-mcp",
      "env": {
        "TURINGMIND_API_URL": "https://api.turingmind.ai",
        "TURINGMIND_DEBUG": "1"
      }
    }
  }
}
```

### Multiple Projects

Each project can have its own `mcp.json` with different configurations.

## Support

For issues:
- Check Claude Code CLI documentation
- Verify MCP server is running
- Test with: `claude mcp` and `turingmind-mcp --help`
