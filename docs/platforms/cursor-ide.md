# Cursor IDE Integration

## Overview

Cursor IDE natively supports MCP servers. TuringMind-MCP integrates seamlessly to provide code review capabilities directly in Cursor's chat interface.

## Quick Start

### 1. Install TuringMind-MCP

```bash
pip install turingmind-mcp
```

### 2. Create MCP Configuration

Create `.cursor/mcp.json` in your project root:

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

**Note**: The `.cursor/` directory should be in your project root, not in your home directory.

### 3. Restart Cursor IDE

Close and reopen Cursor IDE to load the MCP configuration.

### 4. Verify Integration

1. Open Cursor Settings (Cmd/Ctrl + ,)
2. Navigate to **Tools & Integrations** → **MCP**
3. Check that "turingmind" appears in the list with a green active status

### 5. Login to TuringMind

In Cursor chat, say:
```
Log me into TuringMind
```

Cursor will use the MCP tools to guide you through authentication.

## Usage

### Available Tools

All 17 TuringMind MCP tools are available in Cursor:

- **Authentication**: `turingmind_initiate_login`, `turingmind_poll_login`, `turingmind_validate_auth`
- **Code Review**: `turingmind_upload_review`, `turingmind_get_context`, `turingmind_submit_feedback`
- **Memory Management**: `turingmind_list_memory`, `turingmind_get_memory`, `turingmind_create_memory`
- **Code Indexing**: `turingmind_index_codebase`, `turingmind_get_related_code`
- **And more...**

### Example Interactions

**Get Memory Context:**
```
@turingmind get memory context for owner/repo
```

**Review Code:**
```
Review my code changes using TuringMind
```

**Submit Feedback:**
```
That SQL injection issue was a false positive
```

### Auto-Run (Optional)

Enable auto-run in Cursor settings to allow automatic tool execution without approval prompts:

1. Settings → Tools & Integrations → MCP
2. Enable "Auto-run" for turingmind server
3. Tools will execute automatically when requested

## Configuration Options

### Custom API URL

```json
{
  "mcpServers": {
    "turingmind": {
      "command": "turingmind-mcp",
      "args": [],
      "env": {
        "TURINGMIND_API_URL": "https://api.turingmind.ai"
      }
    }
  }
}
```

### Debug Mode

```json
{
  "mcpServers": {
    "turingmind": {
      "command": "turingmind-mcp",
      "args": [],
      "env": {
        "TURINGMIND_DEBUG": "1"
      }
    }
  }
}
```

### Custom Python Path

If `turingmind-mcp` is not in your PATH:

```json
{
  "mcpServers": {
    "turingmind": {
      "command": "python3",
      "args": ["-m", "turingmind_mcp.server"],
      "env": {}
    }
  }
}
```

## Project Structure

```
your-project/
├── .cursor/
│   └── mcp.json          # MCP configuration
├── src/
│   └── ...
└── ...
```

## Troubleshooting

### MCP Server Not Detected

**Issue**: Cursor doesn't show turingmind in MCP list

**Solutions**:
1. Verify `.cursor/mcp.json` exists in project root
2. Check JSON syntax is valid
3. Restart Cursor IDE completely
4. Verify `turingmind-mcp` is in PATH: `which turingmind-mcp`

### Config File Location

**Issue**: Config not loading

**Solution**: 
- Config must be in **project root**: `.cursor/mcp.json`
- NOT in home directory: `~/.cursor/mcp.json`
- Each project can have its own config

### Server Not Starting

**Issue**: MCP server shows as inactive (red)

**Solutions**:
1. Check `turingmind-mcp` is installed: `pip show turingmind-mcp`
2. Test manually: `turingmind-mcp --help`
3. Check Cursor's MCP logs (Settings → Tools & Integrations → MCP → View Logs)
4. Verify Python version: `python --version` (requires 3.10+)

### Authentication Issues

**Issue**: `TURINGMIND_API_KEY not configured`

**Solutions**:
1. Run login: "Log me into TuringMind" in Cursor chat
2. Check API key: `cat ~/.turingmind/config`
3. Verify key format: Should start with `tmk_`
4. Re-login if needed

## Advanced Usage

### Multiple MCP Servers

You can configure multiple MCP servers:

```json
{
  "mcpServers": {
    "turingmind": {
      "command": "turingmind-mcp"
    },
    "other-server": {
      "command": "other-mcp-server"
    }
  }
}
```

### Per-Project Configuration

Each project can have different MCP configurations. This is useful for:
- Different API endpoints per project
- Project-specific environment variables
- Testing different MCP server versions

## Verification

To verify integration:

1. Open Cursor Settings → Tools & Integrations → MCP
2. Check "turingmind" is listed and active (green)
3. In Cursor chat, ask: "What TuringMind tools are available?"
4. Cursor should list all 17 tools

## Best Practices

1. **Version Control**: Add `.cursor/mcp.json` to `.gitignore` if it contains sensitive data
2. **Team Sharing**: Use environment variables for API keys instead of hardcoding
3. **Testing**: Test MCP server manually before relying on Cursor integration
4. **Updates**: Keep `turingmind-mcp` updated: `pip install --upgrade turingmind-mcp`

## Support

For Cursor-specific issues:
- Check Cursor's MCP logs (Settings → MCP → View Logs)
- Verify MCP server works outside Cursor: `turingmind-mcp --help`
- Check Cursor IDE documentation for MCP support
- Ensure Cursor IDE is up to date
