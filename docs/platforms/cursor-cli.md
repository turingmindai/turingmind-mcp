# Cursor CLI Integration

## Overview

Cursor CLI automatically detects and uses the same MCP configuration as Cursor IDE. The `.cursor/mcp.json` file in your project root is shared between both.

## Quick Start

### 1. Install Cursor CLI

```bash
curl https://cursor.com/install -fsS | bash
```

Or follow the [official installation guide](https://docs.cursor.com/tools/cli).

### 2. Install TuringMind-MCP

```bash
pip install turingmind-mcp
```

### 3. Create MCP Configuration

Create `.cursor/mcp.json` in your project root (same as Cursor IDE):

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

### 4. Verify Integration

```bash
# Cursor CLI automatically detects .cursor/mcp.json
cursor agent "Review my code using TuringMind"
```

## Usage

### Basic Commands

**Agent Mode**:
```bash
cursor agent "Get memory context for owner/repo using TuringMind"
```

**Interactive Mode**:
```bash
cursor
# Then in Cursor: "Review my code with TuringMind context"
```

### Using MCP Tools

Cursor CLI can use MCP tools directly:

```bash
# Cursor will automatically use turingmind_get_context tool
cursor agent "What memory context do we have for this repo?"
```

## Configuration

### Shared Configuration

Cursor CLI uses the **same** `.cursor/mcp.json` file as Cursor IDE:

```
your-project/
├── .cursor/
│   └── mcp.json          # Shared by Cursor IDE and CLI
├── src/
│   └── ...
└── ...
```

### Environment Variables

```json
{
  "mcpServers": {
    "turingmind": {
      "command": "turingmind-mcp",
      "args": [],
      "env": {
        "TURINGMIND_API_URL": "https://api.turingmind.ai",
        "TURINGMIND_DEBUG": "1"
      }
    }
  }
}
```

## Integration with Workflows

### CI/CD Integration

```bash
#!/bin/bash
# Example CI script

# Install dependencies
pip install turingmind-mcp

# Run code review
cursor agent "Review code changes and upload to TuringMind" \
  --repo "owner/repo" \
  --branch "$CI_COMMIT_BRANCH"
```

### Git Hooks

You can use Cursor CLI in git hooks:

```bash
#!/bin/bash
# .git/hooks/pre-commit

# Run Cursor CLI review
cursor agent "Review staged changes using TuringMind" > review_output.txt

# Check for critical issues
if grep -q "CRITICAL" review_output.txt; then
    echo "Critical issues found!"
    exit 1
fi
```

## Troubleshooting

### CLI Not Found

**Error**: `cursor: command not found`

**Solution**:
1. Install Cursor CLI: `curl https://cursor.com/install -fsS | bash`
2. Verify installation: `cursor --version`
3. Check PATH includes Cursor CLI

### MCP Config Not Detected

**Error**: MCP tools not available

**Solutions**:
1. Verify `.cursor/mcp.json` exists in project root
2. Check JSON syntax is valid
3. Verify `turingmind-mcp` is in PATH
4. Test: `turingmind-mcp --help`

### Server Not Starting

**Error**: MCP server fails to start

**Solutions**:
1. Check `turingmind-mcp` is installed: `pip show turingmind-mcp`
2. Verify Python version: `python --version` (requires 3.10+)
3. Test manually: `turingmind-mcp --help`
4. Check Cursor CLI logs

## Advanced Usage

### Custom Commands

You can create wrapper scripts:

```bash
#!/bin/bash
# review.sh

cursor agent "Review code using TuringMind context for $1" --repo "$1"
```

Usage:
```bash
./review.sh owner/repo
```

### Automation

```bash
#!/bin/bash
# auto-review.sh

# Get changed files
FILES=$(git diff --name-only HEAD~1)

# Review with Cursor CLI
cursor agent "Review these files using TuringMind: $FILES" \
  --files "$FILES" \
  --upload
```

## Comparison: Cursor CLI vs IDE

| Feature | Cursor CLI | Cursor IDE |
|---------|-----------|------------|
| Config File | `.cursor/mcp.json` | `.cursor/mcp.json` |
| MCP Support | ✅ Yes | ✅ Yes |
| Auto-detection | ✅ Yes | ✅ Yes |
| Best For | Automation, CI/CD | Interactive development |
| Setup | Same | Same |

## Best Practices

1. **Shared Config**: Use same `.cursor/mcp.json` for both CLI and IDE
2. **Version Control**: Consider adding to `.gitignore` if sensitive
3. **Testing**: Test MCP server manually before automation
4. **Error Handling**: Always check exit codes in scripts

## Support

For Cursor CLI issues:
- Check [Cursor CLI documentation](https://docs.cursor.com/tools/cli)
- Verify MCP server works: `turingmind-mcp --help`
- Test config: `cursor agent "test"`
- Check Cursor CLI version: `cursor --version`
