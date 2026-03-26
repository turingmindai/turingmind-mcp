# Quick Start: Bridge Server

## ✅ Fixed and Ready to Use

The bridge server now works without installing the full package!

## Run the Bridge Server

### Simple Method (Recommended)

```bash
cd /Users/turingmindai/Documents/VSCodeProjects/Turingmind-App/turingmind-mcp
python3.10 src/turingmind_mcp/bridge_server.py
```

This will start the server on `ws://127.0.0.1:9876`

### With Custom Port

```bash
python3.10 src/turingmind_mcp/bridge_server.py --port 9877
```

### Via CLI

```bash
python3.10 src/turingmind_mcp/cli.py bridge
```

## Why Python 3.10?

- Your system has Python 3.9 (default) and Python 3.10.18
- The `mcp` package requires Python 3.10+
- But the bridge server only needs `websockets` (already installed for Python 3.10)

## For VS Code Extension Development

1. **Terminal 1: Start Bridge Server**
   ```bash
   cd /Users/turingmindai/Documents/VSCodeProjects/Turingmind-App/turingmind-mcp
   python3.10 src/turingmind_mcp/bridge_server.py
   ```

2. **VS Code: Debug Extension**
   - Open `turingmind-vscode` folder in VS Code
   - Press F5
   - Extension will connect to bridge automatically

## Troubleshooting

### If Python 3.10 not found:
```bash
# Check available Python versions
which python3.10
which python3.11
which python3.12

# Or install Python 3.10+ via Homebrew
brew install python@3.10
```

### If websockets not installed:
```bash
python3.10 -m pip install --user websockets
```

### If port 9876 is in use:
```bash
python3.10 src/turingmind_mcp/bridge_server.py --port 9877
```

Then update extension's `BRIDGE_URL` in `src/extension.ts` to use port 9877.

## What You'll See

When running successfully:
```
Starting TuringMind Bridge Server on 127.0.0.1:9876
Bridge server running at ws://127.0.0.1:9876
```

Keep this terminal open while using the extension!
