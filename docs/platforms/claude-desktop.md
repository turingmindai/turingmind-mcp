# Claude Desktop Integration

## Overview

Claude Desktop natively supports MCP servers. TuringMind-MCP integrates seamlessly with Claude Desktop to provide code review capabilities directly in your chat interface.

## Quick Start

### 1. Install TuringMind-MCP

```bash
pip install turingmind-mcp
```

### 2. Configure Claude Desktop

Add to your Claude Desktop config file:

**macOS:**
```bash
~/Library/Application Support/Claude/claude_desktop_config.json
```

**Windows:**
```bash
%APPDATA%\Claude\claude_desktop_config.json
```

**Linux:**
```bash
~/.config/Claude/claude_desktop_config.json
```

**Config:**
```json
{
  "mcpServers": {
    "turingmind": {
      "command": "turingmind-mcp"
    }
  }
}
```

### 3. Restart Claude Desktop

Completely quit and restart Claude Desktop (Cmd+Q on macOS, close from system tray on Windows/Linux).

### 4. Login to TuringMind

In Claude Desktop chat, say:
```
Log me into TuringMind
```

Claude will guide you through the device code authentication flow.

## Usage

### Available Tools

All 17 TuringMind MCP tools are available in Claude Desktop:

- **Authentication**: `turingmind_initiate_login`, `turingmind_poll_login`, `turingmind_validate_auth`
- **Code Review**: `turingmind_upload_review`, `turingmind_get_context`, `turingmind_submit_feedback`
- **Memory Management**: `turingmind_list_memory`, `turingmind_get_memory`, `turingmind_create_memory`
- **Code Indexing**: `turingmind_index_codebase`, `turingmind_get_related_code`
- **And more...**

### Example Interactions

**Get Memory Context:**
```
Get memory context for owner/repo
```

**Upload Code Review:**
```
Review my code changes and upload to TuringMind
```

**Submit Feedback:**
```
That SQL injection issue was a false positive, we use parameterized queries
```

## Troubleshooting

### MCP Server Not Found

**Error**: `turingmind-mcp: command not found`

**Solution**:
1. Verify installation: `pip show turingmind-mcp`
2. Check PATH: `which turingmind-mcp`
3. Reinstall: `pip install --upgrade turingmind-mcp`

### Config Not Loading

**Error**: Tools not appearing in Claude Desktop

**Solution**:
1. Verify config file exists and is valid JSON
2. Check config path is correct for your OS
3. Restart Claude Desktop completely (not just close window)
4. Check Claude Desktop logs for errors

### Authentication Issues

**Error**: `TURINGMIND_API_KEY not configured`

**Solution**:
1. Run login flow: "Log me into TuringMind" in Claude
2. Check API key: `cat ~/.turingmind/config`
3. Verify key format: Should start with `tmk_`
4. Re-login if needed

## Advanced Configuration

### Custom API URL

```json
{
  "mcpServers": {
    "turingmind": {
      "command": "turingmind-mcp",
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
      "env": {
        "TURINGMIND_DEBUG": "1"
      }
    }
  }
}
```

## Verification

To verify integration is working:

1. Open Claude Desktop
2. Check if you can see TuringMind tools in Claude's tool list
3. Try: "What TuringMind tools are available?"
4. Claude should list all 17 tools

## Support

For issues specific to Claude Desktop integration:
- Check Claude Desktop logs
- Verify MCP server is running
- Test MCP server manually: `turingmind-mcp --help`
