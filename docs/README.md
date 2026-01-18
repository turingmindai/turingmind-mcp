# TuringMind-MCP Documentation

## Quick Links

- **[Quick Start](../QUICK_START.md)** - Get started in 5 minutes
- **[Platform Guides](#platform-integration-guides)** - Setup for your platform
- **[Features](FEATURES.md)** - All available features
- **[Development](DEVELOPMENT.md)** - Architecture and implementation
- **[Testing](TESTING_GUIDE.md)** - Testing guide and results

## Platform Integration Guides

Choose your platform for detailed setup instructions:

- **[Claude Desktop](platforms/claude-desktop.md)** - Native MCP integration in Claude Desktop
- **[Claude Code CLI](platforms/claude-cli.md)** - Command-line integration with MCP config or Skills
- **[Claude SDK](platforms/claude-sdk.md)** - Programmatic access via Python client
- **[Cursor IDE](platforms/cursor-ide.md)** - Native MCP integration in Cursor IDE
- **[Cursor CLI](platforms/cursor-cli.md)** - Command-line integration (shares config with IDE)

## Documentation Structure

### User Documentation
- **[Features](FEATURES.md)** - Complete feature list and user flows
- **[Platform Guides](platforms/)** - Platform-specific setup guides
- **[Quick Start](../QUICK_START.md)** - Quick setup guide

### Developer Documentation
- **[Development](DEVELOPMENT.md)** - Architecture, implementation, parser improvements, and development guidelines
- **[Testing Guide](TESTING_GUIDE.md)** - Testing documentation, test results, and graph functionality testing
- **[Review](REVIEW.md)** - Code review findings, fixes, and issue prioritization
- **[Compatibility Matrix](COMPATIBILITY_MATRIX.md)** - Platform and version compatibility

### Planning & Assessment
- **[Integration Assessment](INTEGRATION_ASSESSMENT.md)** - Current state and gap analysis

## Quick Setup

### Option 1: Unified CLI (Recommended)

```bash
# Install
pip install turingmind-mcp

# Setup for your platform
turingmind setup claude_desktop
turingmind setup cursor
turingmind setup claude_cli

# Diagnose installation
turingmind diagnose
```

### Option 2: Platform-Specific Scripts

```bash
# Claude Desktop
./scripts/setup-claude-desktop.sh

# Claude CLI
./scripts/setup-claude-cli.sh

# Cursor
./scripts/setup-cursor.sh
```

## Support

For platform-specific issues, see the troubleshooting sections in each platform guide.

For development questions, see [Development Documentation](DEVELOPMENT.md).
