#!/usr/bin/env python3
"""
JSON helper for git hooks.

Provides a simple interface for encoding file paths and reasoning into JSON
from bash scripts, handling all edge cases properly.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List


def encode_files_json(files: List[Dict[str, str]]) -> str:
    """
    Encode a list of file objects into a JSON string.
    
    Args:
        files: List of dicts with 'file_path' and optionally 'reasoning'
        
    Returns:
        JSON string
    """
    return json.dumps(files, ensure_ascii=False)


def main() -> int:
    """CLI entry point for JSON encoding."""
    if len(sys.argv) < 2:
        print("Usage: json_helper.py encode <file_path> [reasoning] [file_path2] [reasoning2] ...", file=sys.stderr)
        print("   OR: json_helper.py encode < <input_file>", file=sys.stderr)
        return 1
    
    if sys.argv[1] != "encode":
        print("Unknown command. Use 'encode'", file=sys.stderr)
        return 1
    
    files = []
    
    # Check if reading from stdin (for more reliable input)
    if len(sys.argv) == 2 and not sys.stdin.isatty():
        # Read from stdin: format is "file_path|reasoning" per line
        # Reasoning may contain newlines, so we read the entire stdin first
        stdin_content = sys.stdin.read()
        for line in stdin_content.splitlines():
            line = line.rstrip('\n\r')  # Only strip trailing newlines, preserve content
            if not line:
                continue
            parts = line.split("|", 1)
            file_path = parts[0]
            reasoning = parts[1] if len(parts) > 1 else None
            
            file_obj: Dict[str, Any] = {"file_path": file_path}
            if reasoning:
                file_obj["reasoning"] = reasoning
            files.append(file_obj)
    else:
        # Parse arguments: file_path, reasoning pairs
        i = 2
        while i < len(sys.argv):
            file_path = sys.argv[i]
            reasoning = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
            
            file_obj: Dict[str, Any] = {"file_path": file_path}
            if reasoning:
                file_obj["reasoning"] = reasoning
            
            files.append(file_obj)
            i += 2
    
    if not files:
        print("No files provided", file=sys.stderr)
        return 1
    
    # Output JSON
    print(encode_files_json(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
