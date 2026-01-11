# TuringMind MCP Server

[![PyPI version](https://badge.fury.io/py/turingmind-mcp.svg)](https://badge.fury.io/py/turingmind-mcp)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Model Context Protocol (MCP) server for [TuringMind](https://turingmind.ai) cloud integration. Provides type-safe tools for Claude to authenticate, upload code reviews, fetch repository context, and submit feedback.

> **Requires Python 3.10+** (MCP SDK requirement)

## Why MCP?

Instead of Claude generating raw JSON and curl commands (which can fail silently due to field name mismatches or malformed data), MCP provides:

- **Type-safe tool definitions** — Claude sees the exact schema
- **Validated input** — Errors caught before sending
- **No endpoint guessing** — Correct URLs hardcoded
- **Better error messages** — Clear feedback on failures
- **Simplified login** — Device code flow handled by the server

## Installation

### From PyPI

```bash
pip install turingmind-mcp
```

### With pipx (recommended for CLI tools)

```bash
pipx install turingmind-mcp
```

### From Source

```bash
git clone https://github.com/turingmindai/turingmind-mcp.git
cd turingmind-mcp
pip install -e .
```

### Verify Installation

```bash
turingmind-mcp --help
```

## Quick Start

### 1. Configure Claude Desktop

Add to your Claude Desktop config file:

| Platform | Path |
|----------|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "turingmind": {
      "command": "turingmind-mcp"
    }
  }
}
```

### 2. Restart Claude Desktop

### 3. Login to TuringMind

In Claude, say: "Log me into TuringMind"

Claude will guide you through the device code flow.

## Available Tools

### Authentication

| Tool | Description |
|------|-------------|
| `turingmind_initiate_login` | Start device code auth flow (no API key needed) |
| `turingmind_poll_login` | Complete login and save API key |
| `turingmind_validate_auth` | Check API key and account status |

### Code Review

| Tool | Description |
|------|-------------|
| `turingmind_upload_review` | Upload review results to cloud |
| `turingmind_get_context` | Get memory context for a repository |
| `turingmind_submit_feedback` | Mark issues as fixed, dismissed, or false positive |

## Tool Reference

### `turingmind_initiate_login`

Start device code authentication flow. No API key required.

**Parameters:** None

**Returns:**
- `verification_url` — URL to open in browser
- `user_code` — Code to enter when prompted  
- `device_code` — Use with `turingmind_poll_login`

---

### `turingmind_poll_login`

Poll for authentication completion.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `device_code` | string | ✅ | Device code from `turingmind_initiate_login` |

**Returns:**
- On success: API key (automatically saved to `~/.turingmind/config`)
- On pending: Status message to wait and retry
- On expired: Error message to restart flow

---

### `turingmind_validate_auth`

Validate API key and get account info.

**Parameters:** None

**Returns:**
- Tier (free, pro, team, enterprise)
- Quota remaining
- User ID

---

### `turingmind_upload_review`

Upload code review results to TuringMind cloud.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `repo` | string | ✅ | Repository (owner/repo) |
| `branch` | string | | Git branch name |
| `commit` | string | | Git commit SHA |
| `review_type` | `"quick"` \| `"deep"` | | Review type (default: quick) |
| `issues` | array | | List of issues found |
| `raw_content` | string | | Full review as markdown |
| `summary` | object | | {critical, high, medium, low} counts |
| `files_reviewed` | array | | Files that were reviewed |

**Issue Schema:**

```json
{
  "title": "SQL Injection vulnerability",
  "severity": "critical",
  "category": "security",
  "file": "src/db.py",
  "line": 42,
  "description": "User input passed directly to query",
  "cwe": "CWE-89",
  "confidence": 95
}
```

---

### `turingmind_get_context`

Get memory context for a repository.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `repo` | string | ✅ | Repository (owner/repo) |

**Returns:**
- Recent open issues
- Hotspot files (frequent issues)
- Team conventions
- Known false positive patterns

---

### `turingmind_submit_feedback`

Submit feedback on a code review issue.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `issue_id` | string | ✅ | Issue ID (e.g., `iss_abc123`) |
| `action` | string | ✅ | One of: `fixed`, `dismissed`, `false_positive` |
| `repo` | string | ✅ | Repository (owner/repo) |
| `file` | string | | File path where issue was found |
| `line` | integer | | Line number of the issue |
| `pattern` | string | | For false_positive: pattern to skip in future |
| `reason` | string | | Explanation for the feedback |

**Actions:**
- `fixed` — Issue was resolved (tracks time-to-fix metrics)
- `dismissed` — Issue isn't important (lowers future priority)
- `false_positive` — Not a real issue (adds pattern to memory)

## Usage Examples

### Login Flow

```
User: Log me into TuringMind

Claude: I'll start the authentication flow.

[Calls turingmind_initiate_login]

🧠 TuringMind Login Started

Step 1: Open this URL in your browser:
https://app.turingmind.ai/cli-auth?device_code=...

Step 2: Sign in with Google or GitHub

User: Done!

Claude: [Calls turingmind_poll_login with device_code]

✅ Login Successful!
API key has been saved to ~/.turingmind/config
```

### Review with Upload

```
User: Review my code changes and upload to TuringMind

Claude: [Calls turingmind_validate_auth]
✅ TuringMind Authentication Valid
- Tier: team
- Quota: 495/500 reviews remaining

[Reviews code...]

[Calls turingmind_upload_review]
🧠 Review Uploaded to TuringMind
- Review ID: rev_abc123
- Issues: 3
```

### False Positive Feedback

```
User: That SQL injection issue was a false positive, we use parameterized queries

Claude: [Calls turingmind_submit_feedback]
🚫 Feedback Submitted
Issue iss_abc123 has been marked as false positive.
Pattern saved: `db.query(sql, params)`
This pattern will be skipped in future reviews.
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TURINGMIND_API_URL` | API server URL | `https://api.turingmind.ai` |
| `TURINGMIND_API_KEY` | API key | Read from `~/.turingmind/config` |
| `TURINGMIND_DEBUG` | Enable debug logging | `0` |

### Config File

API credentials are stored in `~/.turingmind/config`:

```bash
export TURINGMIND_API_KEY=tmk_your_key_here
export TURINGMIND_API_URL=https://api.turingmind.ai
```

### Claude Desktop with Custom API URL

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

## Development

### Setup

```bash
git clone https://github.com/turingmindai/turingmind-mcp.git
cd turingmind-mcp
pip install -e ".[dev]"
```

### Run Locally

```bash
python -m turingmind_mcp.server
```

### Test with MCP Inspector

```bash
npx @modelcontextprotocol/inspector turingmind-mcp
```

### Run Tests

```bash
pytest
```

### Lint & Format

```bash
ruff check .
black .
mypy src/
```

## Troubleshooting

### "TURINGMIND_API_KEY not configured"

Run the login flow in Claude, or set the environment variable:

```bash
export TURINGMIND_API_KEY=tmk_your_key_here
```

### "Permission Denied"

API key lacks required permission. Re-run login to create a new key with proper permissions.

### "Connection Error"

1. Check that `TURINGMIND_API_URL` is correct
2. Verify network connectivity
3. For local development, ensure backend is running

### Claude doesn't see the tools

1. Verify `turingmind-mcp` is in your PATH: `which turingmind-mcp`
2. Check Claude Desktop config is valid JSON
3. Restart Claude Desktop completely (Cmd+Q / close from tray)

## License

MIT — see [LICENSE](LICENSE) for details.

## Links

- [TuringMind](https://turingmind.ai) — AI-powered code review
- [Documentation](https://docs.turingmind.ai)
- [GitHub](https://github.com/turingmindai/turingmind-mcp)
- [Issue Tracker](https://github.com/turingmindai/turingmind-mcp/issues)
