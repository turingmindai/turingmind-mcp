#!/bin/bash

# Setup script for Cursor IDE/CLI integration
# This script configures TuringMind-MCP for Cursor

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}🧠 Setting up TuringMind-MCP for Cursor...${NC}"
echo ""

# Check if we're in a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Not in a git repository${NC}"
    echo "This script should be run from your project root"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
CURSOR_DIR="$PROJECT_ROOT/.cursor"
CONFIG_FILE="$CURSOR_DIR/mcp.json"

echo -e "${CYAN}Project root: ${PROJECT_ROOT}${NC}"
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

# Create .cursor directory if it doesn't exist
mkdir -p "$CURSOR_DIR"

# Read existing config or create new
if [ -f "$CONFIG_FILE" ]; then
    echo -e "${CYAN}Found existing config, backing up...${NC}"
    cp "$CONFIG_FILE" "$CONFIG_FILE.backup"
    echo -e "${GREEN}✅ Backup created: ${CONFIG_FILE}.backup${NC}"
    
    CONFIG=$(cat "$CONFIG_FILE")
else
    echo -e "${CYAN}Creating new config...${NC}"
    CONFIG="{}"
fi

# Use Python to safely merge JSON
python3 << PYTHON_SCRIPT
import json
import sys

config_path = "$CONFIG_FILE"

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
    "args": [],
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
    echo -e "${GREEN}✅ Cursor configuration complete!${NC}"
    echo ""
    echo -e "${CYAN}Next steps:${NC}"
    echo "1. Restart Cursor IDE (if using IDE)"
    echo "2. Verify: Settings → Tools & Integrations → MCP → Check 'turingmind' is active"
    echo "3. Login: In Cursor chat, say 'Log me into TuringMind'"
    echo ""
    echo -e "${YELLOW}Config file: ${CONFIG_FILE}${NC}"
    echo ""
    echo -e "${CYAN}For Cursor CLI:${NC}"
    echo "The same config file is used automatically"
    echo "Test: cursor agent 'Review my code using TuringMind'"
else
    echo -e "${RED}❌ Configuration failed${NC}"
    exit 1
fi
