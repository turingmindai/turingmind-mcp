#!/usr/bin/env python3
"""
CLI interface for TuringMind MCP tools.

Provides command-line access to MCP functionality for use in git hooks and scripts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Handle imports - work both when installed and when run as script
# We need to avoid importing through __init__.py which imports server (requires mcp)
import importlib.util

# Get package directory
_package_dir = Path(__file__).parent
_src_dir = _package_dir.parent

# Add src to path if not already there (for when run as script)
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

# Import database first (no relative imports)
_database_path = _package_dir / "database.py"
_database_spec = importlib.util.spec_from_file_location("turingmind_mcp.database", _database_path)
if _database_spec is None or _database_spec.loader is None:
    print("❌ Could not load database module", file=sys.stderr)
    sys.exit(1)

# Create a fake turingmind_mcp module in sys.modules to satisfy relative imports
if "turingmind_mcp" not in sys.modules:
    import types
    sys.modules["turingmind_mcp"] = types.ModuleType("turingmind_mcp")
    sys.modules["turingmind_mcp"].__path__ = [str(_package_dir)]

# Load database module
_database_module = importlib.util.module_from_spec(_database_spec)
sys.modules["turingmind_mcp.database"] = _database_module
_database_spec.loader.exec_module(_database_module)
MemoryDatabase = _database_module.MemoryDatabase

# Now load memory_manager (it can import from .database)
_memory_manager_path = _package_dir / "memory_manager.py"
_memory_manager_spec = importlib.util.spec_from_file_location("turingmind_mcp.memory_manager", _memory_manager_path)
if _memory_manager_spec is None or _memory_manager_spec.loader is None:
    print("❌ Could not load memory_manager module", file=sys.stderr)
    sys.exit(1)

_memory_manager_module = importlib.util.module_from_spec(_memory_manager_spec)
sys.modules["turingmind_mcp.memory_manager"] = _memory_manager_module
_memory_manager_spec.loader.exec_module(_memory_manager_module)
MemoryManager = _memory_manager_module.MemoryManager


def store_edit_reasoning(
    repo: str,
    files_json: str,
    commit_message: str | None = None,
    commit_hash: str | None = None,
) -> int:
    """
    Store edit reasoning via CLI.
    
    Args:
        repo: Repository identifier (owner/repo)
        files_json: JSON string with files array
        commit_message: Optional commit message
        commit_hash: Optional commit hash
        
    Returns:
        0 on success, 1 on error
    """
    # Validate inputs
    if not repo or not repo.strip():
        print("❌ Error: repo is required", file=sys.stderr)
        return 1
    
    if not files_json or not files_json.strip():
        print("❌ Error: files_json is required", file=sys.stderr)
        return 1
    
    db = None
    try:
        # Parse files JSON
        try:
            files = json.loads(files_json)
        except json.JSONDecodeError as e:
            print(f"❌ Error: Invalid JSON in files: {e}", file=sys.stderr)
            return 1
        
        if not isinstance(files, list):
            print("❌ Error: files must be a JSON array", file=sys.stderr)
            return 1
        
        if not files:
            print("❌ Error: files array is empty", file=sys.stderr)
            return 1
        
        # Validate file objects
        for i, file_obj in enumerate(files):
            if not isinstance(file_obj, dict):
                print(f"❌ Error: files[{i}] must be an object", file=sys.stderr)
                return 1
            
            file_path = file_obj.get("file_path")
            if not file_path or not file_path.strip():
                print(f"❌ Error: files[{i}].file_path is required", file=sys.stderr)
                return 1
        
        # Initialize database and memory manager
        try:
            db = MemoryDatabase()
            memory_manager = MemoryManager(db)
        except Exception as e:
            print(f"❌ Error: Failed to initialize database: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        
        # Extract overall intent from commit message
        overall_intent = None
        if commit_message:
            if "Why:" in commit_message:
                overall_intent = commit_message.split("Why:")[1].strip().split("\n")[0]
        
        # Process per-file reasoning
        file_reasoning_map = {}
        for file_obj in files:
            file_path = file_obj.get("file_path", "").strip()
            reasoning = file_obj.get("reasoning")
            
            # Validate file_path (already checked above, but double-check)
            if not file_path:
                continue
            
            if not reasoning and commit_message:
                # Try to infer from commit message
                reasoning = overall_intent
            
            if reasoning:
                file_reasoning_map[file_path] = {
                    "reasoning": reasoning,
                    "change_type": file_obj.get("change_type", "other"),
                    "memory_category": file_obj.get("memory_category", "session_context"),
                    "scope": file_obj.get("scope", file_path),
                    "confidence": file_obj.get("confidence", 0.8),
                }
                
                # Create session context
                try:
                    memory_manager.create_session_context(
                        repo=repo,
                        content=reasoning,
                        scope=file_path,
                        evidence=[
                            {
                                "type": "commit" if commit_hash else "conversation",
                                "content": commit_message or f"File edit: {file_path}",
                                "file": file_path,
                            }
                        ],
                    )
                except Exception as e:
                    print(f"⚠️  Warning: Failed to create session context for {file_path}: {e}", file=sys.stderr)
                    # Continue processing other files
        
        # Save edit reasoning
        if file_reasoning_map:
            try:
                if commit_hash:
                    db.save_edit_reasoning(
                        repo=repo,
                        files=list(file_reasoning_map.values()),
                        commit_hash=commit_hash,
                        overall_reasoning=overall_intent,
                    )
                    print(f"✅ Stored reasoning for {len(file_reasoning_map)} file(s) in commit {commit_hash[:8]}")
                else:
                    # Store without commit hash (will be updated in post-commit if needed)
                    db.save_edit_reasoning(
                        repo=repo,
                        files=list(file_reasoning_map.values()),
                        commit_hash=None,
                        overall_reasoning=overall_intent,
                    )
                    print(f"✅ Stored reasoning for {len(file_reasoning_map)} file(s)")
            except Exception as e:
                print(f"❌ Error: Failed to save edit reasoning: {type(e).__name__}: {e}", file=sys.stderr)
                return 1
        else:
            print("⚠️  Warning: No reasoning to store", file=sys.stderr)
        
        return 0
        
    except Exception as e:
        print(f"❌ Error: Unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        # Ensure database connection is closed
        if db is not None:
            try:
                db.close()
            except Exception:
                pass  # Ignore errors during cleanup


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="TuringMind MCP CLI - Store edit reasoning from git hooks"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # store-reasoning command
    store_parser = subparsers.add_parser(
        "store-reasoning",
        help="Store edit reasoning for files",
    )
    store_parser.add_argument(
        "--repo",
        required=True,
        help="Repository identifier (owner/repo)",
    )
    store_parser.add_argument(
        "--files",
        required=True,
        help="JSON array of files with reasoning: [{\"file_path\": \"...\", \"reasoning\": \"...\"}, ...]",
    )
    store_parser.add_argument(
        "--commit-message",
        help="Commit message (optional)",
    )
    store_parser.add_argument(
        "--commit-hash",
        help="Commit hash (optional)",
    )
    
    args = parser.parse_args()
    
    if args.command == "store-reasoning":
        return store_edit_reasoning(
            repo=args.repo,
            files_json=args.files,
            commit_message=args.commit_message,
            commit_hash=args.commit_hash,
        )
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
