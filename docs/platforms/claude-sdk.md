# Claude SDK Integration

## Overview

The TuringMind MCP client provides programmatic access to TuringMind MCP tools via Python. This enables integration with Claude SDK and other Python applications.

## Installation

```bash
pip install turingmind-mcp
```

The client is included in the main package.

## Quick Start

### Synchronous Client

```python
from turingmind_mcp.client import TuringMindMCPClient

# Initialize client
client = TuringMindMCPClient()

# Start MCP server
client.start()

try:
    # List available tools
    tools = client.list_tools()
    print(f"Available tools: {len(tools)}")
    
    # Call a tool
    result = client.call_tool(
        "turingmind_get_context",
        {"repo": "owner/repo"}
    )
    
    print(result)
finally:
    # Stop MCP server
    client.stop()
```

### Asynchronous Client

```python
import asyncio
from turingmind_mcp.client import AsyncTuringMindMCPClient

async def main():
    # Initialize async client
    client = AsyncTuringMindMCPClient()
    
    # Start MCP server
    await client.start()
    
    try:
        # List available tools
        tools = await client.list_tools()
        print(f"Available tools: {len(tools)}")
        
        # Call a tool
        result = await client.call_tool(
            "turingmind_get_context",
            {"repo": "owner/repo"}
        )
        
        print(result)
    finally:
        # Stop MCP server
        await client.stop()

# Run
asyncio.run(main())
```

### Context Manager (Recommended)

```python
from turingmind_mcp.client import TuringMindMCPClient

# Automatic start/stop
with TuringMindMCPClient() as client:
    # Get memory context
    context = client.call_tool(
        "turingmind_get_context",
        {"repo": "owner/repo"}
    )
    
    # Use context in your application
    print(context)
```

## Integration with Claude SDK

### Example: Code Review with Memory Context

```python
from anthropic import Anthropic
from turingmind_mcp.client import TuringMindMCPClient

# Initialize clients
claude = Anthropic(api_key="your-claude-api-key")
turingmind = TuringMindMCPClient()

# Start TuringMind client
turingmind.start()

try:
    # Get memory context
    memory_result = turingmind.call_tool(
        "turingmind_get_context",
        {"repo": "owner/repo"}
    )
    
    # Extract context from result
    context_text = "\n".join([
        item.get("text", "") 
        for item in memory_result 
        if item.get("type") == "text"
    ])
    
    # Use in Claude API call
    response = claude.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": f"Review this code with context:\n\n{context_text}\n\nCode:\n```python\ndef auth(user, password):\n    query = f'SELECT * FROM users WHERE user={user}'\n    return db.execute(query)\n```"
        }]
    )
    
    print(response.content[0].text)
finally:
    turingmind.stop()
```

### Example: Upload Review Results

```python
from turingmind_mcp.client import TuringMindMCPClient

with TuringMindMCPClient() as client:
    # Upload review results
    result = client.call_tool(
        "turingmind_upload_review",
        {
            "repo": "owner/repo",
            "branch": "main",
            "commit": "abc123",
            "review_type": "quick",
            "issues": [
                {
                    "title": "SQL Injection",
                    "severity": "critical",
                    "category": "security",
                    "file": "src/auth.py",
                    "line": 23,
                    "description": "User input directly in SQL query",
                    "cwe": "CWE-89",
                    "confidence": 95
                }
            ],
            "summary": {
                "critical": 1,
                "high": 0,
                "medium": 0,
                "low": 0
            }
        }
    )
    
    print(f"Review uploaded: {result}")
```

## Available Tools

All 17 TuringMind MCP tools are available:

### Authentication
- `turingmind_initiate_login`
- `turingmind_poll_login`
- `turingmind_validate_auth`

### Code Review
- `turingmind_upload_review`
- `turingmind_get_context`
- `turingmind_submit_feedback`

### Memory Management
- `turingmind_list_memory`
- `turingmind_get_memory`
- `turingmind_create_memory`
- `turingmind_update_memory`
- `turingmind_delete_memory`

### Code Indexing
- `turingmind_index_codebase`
- `turingmind_get_related_code`
- `turingmind_get_project_structure`

### And more...

## Error Handling

```python
from turingmind_mcp.client import TuringMindMCPClient
from turingmind_mcp.errors import ToolError, ConnectionError

client = TuringMindMCPClient()

try:
    client.start()
    result = client.call_tool("turingmind_get_context", {"repo": "owner/repo"})
except ConnectionError as e:
    print(f"Connection failed: {e}")
    print(e.troubleshooting)
except ToolError as e:
    print(f"Tool error: {e}")
    print(e.troubleshooting)
except Exception as e:
    print(f"Unexpected error: {e}")
finally:
    client.stop()
```

## Advanced Configuration

### Custom Command

```python
client = TuringMindMCPClient(
    command="python3",
    args=["-m", "turingmind_mcp.server"],
    env={"TURINGMIND_DEBUG": "1"}
)
```

### Environment Variables

```python
client = TuringMindMCPClient(
    env={
        "TURINGMIND_API_URL": "https://api.turingmind.ai",
        "TURINGMIND_API_KEY": "tmk_your_key_here"
    }
)
```

## Best Practices

1. **Use Context Managers**: Always use `with` statement for automatic cleanup
2. **Handle Errors**: Wrap calls in try/except blocks
3. **Reuse Clients**: Create client once, reuse for multiple calls
4. **Async for Performance**: Use async client for concurrent operations

## Troubleshooting

### MCP Server Not Starting

**Error**: `Failed to start MCP server`

**Solution**:
1. Verify installation: `pip show turingmind-mcp`
2. Check command: `turingmind-mcp --help`
3. Verify Python version: `python --version` (requires 3.10+)

### Connection Errors

**Error**: `No response from MCP server`

**Solution**:
1. Check server is running: `ps aux | grep turingmind-mcp`
2. Verify stdin/stdout streams
3. Check for errors in stderr

### Tool Not Found

**Error**: `Tool 'turingmind_xyz' not found`

**Solution**:
1. List available tools: `client.list_tools()`
2. Verify tool name spelling
3. Check tool is available in your MCP server version

## Support

For SDK-specific issues:
- Check Python version compatibility
- Verify MCP server is installed correctly
- Test with simple tool calls first
- Check error messages for troubleshooting hints
