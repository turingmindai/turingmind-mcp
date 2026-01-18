#!/bin/bash

# Setup script for Claude Desktop integration
# This script configures TuringMind-MCP for Claude Desktop

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}🧠 Setting up TuringMind-MCP for Claude Desktop...${NC}"
echo ""

# Detect platform
if [[ "$OSTYPE" == "darwin"* ]]; then
    PLATFORM="macos"
    CONFIG_DIR="$HOME/Library/Application Support/Claude"
elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ "$OSTYPE" == "win32" ]]; then
    PLATFORM="windows"
    CONFIG_DIR="${APPDATA:-$HOME/AppData/Roaming}/Claude"
else
    PLATFORM="linux"
    CONFIG_DIR="$HOME/.config/Claude"
fi

CONFIG_FILE="$CONFIG_DIR/claude_desktop_config.json"

echo -e "${CYAN}Platform: ${PLATFORM}${NC}"
echo -e "${CYAN}Config file: ${CONFIG_FILE}${NC}"
echo ""

# Check if turingmind-mcp is installed
if ! command -v turingmind-mcp &> /dev/null; then
    echo -e "${YELLOW}⚠️  turingmind-mcp not found in PATH${NC}"
    echo "Installing turingmind-mcp..."
    pip install turingmind-mcp
    echo -e "${GREEN}✅ Installed turingmind-mcp${NC}"
else
    echo -e "${GREEN}✅ turingmind-mcp found${NC}"
fi

# Create config directory if it doesn't exist
mkdir -p "$CONFIG_DIR"

# Read existing config or create new
if [ -f "$CONFIG_FILE" ]; then
    echo -e "${CYAN}Found existing config, backing up...${NC}"
    cp "$CONFIG_FILE" "$CONFIG_FILE.backup"
    echo -e "${GREEN}✅ Backup created: ${CONFIG_FILE}.backup${NC}"
    
    # Read existing config
    CONFIG=$(cat "$CONFIG_FILE")
else
    echo -e "${CYAN}Creating new config...${NC}"
    CONFIG="{}"
fi

# Use Python to safely merge JSON
python3 << PYTHON_SCRIPT
import json
import sys
import os

config_path = "$CONFIG_FILE"
backup_path = "${CONFIG_FILE}.backup"

# Read existing config
try:
    with open(config_path) as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}

# Initialize mcpServers if needed
if "mcpServers" not in config:
    config["mcpServers"] = {}

# Check if turingmind already exists
if "turingmind" in config["mcpServers"]:
    print("⚠️  TuringMind MCP server already configured")
    response = input("Update existing configuration? (y/N): ")
    if response.lower() != 'y':
        print("Skipping configuration update")
        sys.exit(0)

# Add/update turingmind server
config["mcpServers"]["turingmind"] = {
    "command": "turingmind-mcp",
    "env": {
        "TURINGMIND_API_URL": "https://api.turingmind.ai"
    }
}

# Write config
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)

print("✅ Configuration updated successfully")
PYTHON_SCRIPT

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✅ Claude Desktop configuration complete!${NC}"
    echo ""
    echo -e "${CYAN}Next steps:${NC}"
    echo "1. Restart Claude Desktop completely (Cmd+Q / close from system tray)"
    echo "2. In Claude Desktop, say: 'Log me into TuringMind'"
    echo "3. Follow the authentication flow"
    echo ""
    echo -e "${YELLOW}Config file: ${CONFIG_FILE}${NC}"
else
    echo -e "${RED}❌ Configuration failed${NC}"
    exit 1
fi
