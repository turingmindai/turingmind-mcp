# Bridge Server - How to Run

## Fixed Issues

✅ **Import errors fixed** - Can now run standalone or as module  
✅ **Deprecation warning fixed** - Updated websockets usage  
✅ **Multiple run methods** - Works in all scenarios

## Method 1: Run Script Directly (Easiest)

```bash
cd /Users/turingmindai/Documents/VSCodeProjects/Turingmind-App/turingmind-mcp
python3 src/turingmind_mcp/bridge_server.py
```

Or with custom host/port:

```bash
python3 src/turingmind_mcp/bridge_server.py --host 127.0.0.1 --port 9876
```

## Method 2: Use CLI Command

```bash
cd /Users/turingmindai/Documents/VSCodeProjects/Turingmind-App/turingmind-mcp
python3 src/turingmind_mcp/cli.py bridge
```

Or with options:

```bash
python3 src/turingmind_mcp/cli.py bridge --host 127.0.0.1 --port 9876
```

## Method 3: Run as Module (After Installation)

If you install the package:

```bash
pip3 install -e .
python3 -m turingmind_mcp.cli bridge
```

Or directly:

```bash
python3 -m turingmind_mcp.bridge_server
```

## Method 4: Use PYTHONPATH

```bash
cd /Users/turingmindai/Documents/VSCodeProjects/Turingmind-App/turingmind-mcp
PYTHONPATH=src python3 src/turingmind_mcp/bridge_server.py
```

## Expected Output

When running successfully, you should see:

```
Starting TuringMind Bridge Server on 127.0.0.1:9876
Bridge server running at ws://127.0.0.1:9876
```

## Troubleshooting

### If you see "websockets not installed":
```bash
pip3 install websockets
```

### If you see import errors:
Use Method 1 (run script directly) - it handles imports automatically.

### If port is already in use:
```bash
python3 src/turingmind_mcp/bridge_server.py --port 9877
```

Then update extension to use port 9877.

## For VS Code Extension Development

Keep this running in a terminal while debugging the extension:

```bash
cd /Users/turingmindai/Documents/VSCodeProjects/Turingmind-App/turingmind-mcp
python3 src/turingmind_mcp/bridge_server.py
```

The extension will connect to `ws://127.0.0.1:9876` automatically.
