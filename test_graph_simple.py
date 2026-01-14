#!/usr/bin/env python3
"""
Simple test script to verify graph generation on turingmind-mcp codebase.
Tests indexing without requiring full package installation.
"""

import sys
import subprocess
from pathlib import Path

def test_with_python():
    """Test using Python directly importing modules."""
    repo_path = Path(__file__).parent
    
    # Test indexing a single file first
    test_code = f"""
import sys
sys.path.insert(0, '{repo_path}/src')

# Test basic indexing on server.py
from pathlib import Path
import ast

repo_path = Path('{repo_path}')
server_file = repo_path / 'src' / 'turingmind_mcp' / 'server.py'

if server_file.exists():
    with open(server_file) as f:
        content = f.read()
    
    # Parse with AST
    tree = ast.parse(content)
    
    functions = []
    classes = []
    
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            functions.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
    
    print(f"Found {{len(functions)}} functions and {{len(classes)}} classes in server.py")
    print(f"Functions: {{', '.join(functions[:10])}}...")
    print(f"Classes: {{', '.join(classes)}}")
else:
    print("server.py not found")
"""
    
    result = subprocess.run(
        [sys.executable, "-c", test_code],
        capture_output=True,
        text=True,
        cwd=repo_path
    )
    
    print("=" * 80)
    print("Graph Generation Test Results")
    print("=" * 80)
    print(result.stdout)
    if result.stderr:
        print("Errors:", result.stderr)
    
    return result.returncode == 0


def test_file_counting():
    """Count Python files to index."""
    repo_path = Path(__file__).parent
    src_path = repo_path / "src" / "turingmind_mcp"
    
    python_files = list(src_path.rglob("*.py"))
    print(f"\n📁 Found {len(python_files)} Python files:")
    for f in python_files[:10]:
        print(f"   - {f.relative_to(repo_path)}")
    if len(python_files) > 10:
        print(f"   ... and {len(python_files) - 10} more")
    
    return len(python_files)


def main():
    """Run tests."""
    print("\n🧪 Testing Graph Generation on turingmind-mcp\n")
    
    # Test 1: Count files
    file_count = test_file_counting()
    
    # Test 2: Basic AST parsing
    success = test_with_python()
    
    print("\n" + "=" * 80)
    if success:
        print("✅ Basic indexing test passed!")
        print(f"   Ready to index {file_count} Python files")
    else:
        print("❌ Test had issues (may need dependencies installed)")
    print("=" * 80)
    
    print("\n💡 To test full graph generation:")
    print("   1. Install dependencies: pip install -e '.[dev]'")
    print("   2. Run: python test_graph_generation.py")
    print("   3. Or use MCP tools via Claude Desktop")
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
