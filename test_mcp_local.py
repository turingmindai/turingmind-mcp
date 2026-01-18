#!/usr/bin/env python3
"""Test script to verify local MCP server can be imported and run."""
import sys
import os
from pathlib import Path

# Add local src to path
repo_root = Path(__file__).parent
src_path = repo_root / "src"
sys.path.insert(0, str(src_path))

print(f"📁 Using local source: {src_path}")
print(f"🐍 Python: {sys.version}")
print("")

try:
    # Test imports
    print("🔍 Testing imports...")
    from turingmind_mcp.entity_indexer import EntityIndexer, get_repo_path
    from turingmind_mcp.database import MemoryDatabase
    print("✅ EntityIndexer and MemoryDatabase imported successfully")
    
    # Test repo path detection
    repo_path = get_repo_path()
    if repo_path:
        print(f"✅ Repository path detected: {repo_path}")
    else:
        print("⚠️  Could not detect repository path")
    
    # Test database
    print("\n💾 Testing database...")
    db = MemoryDatabase()
    print("✅ Database initialized")
    db.close()
    print("✅ Database closed")
    
    print("\n✅ All local imports working! MCP server should work with local source.")
    
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("\n💡 Make sure dependencies are installed:")
    print("   pipx inject turingmind-mcp mcp httpx pydantic tree-sitter tree-sitter-python tree-sitter-javascript tree-sitter-typescript")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
