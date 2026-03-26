#!/usr/bin/env python3
"""Reconstruct the exact LLM prompt from SQLite database conversation."""

import sys
import sqlite3
import json
from pathlib import Path
from typing import Dict, List, Any, Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from turingmind_mcp.agents.chat_analysis_agent import ChatAnalysisAgent
from turingmind_mcp.llm.config import get_llm_provider


def find_cursor_database() -> Optional[Path]:
    """Find Cursor SQLite database path."""
    import os
    import platform
    
    system = platform.system()
    if system == "Darwin":  # macOS
        base_path = Path.home() / "Library" / "Application Support" / "Cursor"
    elif system == "Windows":
        base_path = Path(os.getenv("APPDATA", "")) / "Cursor"
    else:  # Linux
        base_path = Path.home() / ".config" / "Cursor"
    
    # Try different possible database locations
    possible_paths = [
        base_path / "User" / "globalStorage" / "state.vscdb",
        base_path / "state.vscdb",
        base_path / "User" / "workspaceStorage" / "**" / "state.vscdb",
    ]
    
    for path_pattern in possible_paths:
        if "*" in str(path_pattern):
            # Handle glob patterns
            import glob
            matches = glob.glob(str(path_pattern))
            if matches:
                return Path(matches[0])
        else:
            if path_pattern.exists():
                return path_pattern
    
    return None


def extract_conversation_data(db_path: Path, composer_id: str) -> Optional[Dict[str, Any]]:
    """Extract conversation data from SQLite database."""
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get conversation data
        cursor.execute("""
            SELECT key, value 
            FROM ItemTable 
            WHERE key LIKE ?
        """, (f"%{composer_id}%",))
        
        rows = cursor.fetchall()
        if not rows:
            print(f"❌ No data found for composer_id: {composer_id}")
            return None
        
        # Parse the data
        data = {}
        for row in rows:
            key = row[0]
            value = row[1]
            try:
                data[key] = json.loads(value) if isinstance(value, str) else value
            except:
                data[key] = value
        
        conn.close()
        return data
        
    except Exception as e:
        print(f"❌ Error reading database: {e}")
        return None


def extract_metadata_from_cursor_data(cursor_data: Dict[str, Any], composer_id: str) -> Dict[str, Any]:
    """Extract metadata in the format expected by the agent."""
    # This is a simplified extraction - actual implementation would parse Cursor's data structure
    metadata = {
        "user_prompts": [],
        "assistant_responses": [],
        "files_discussed": [],
        "ai_todos": [],
        "reasoning": []
    }
    
    # Try to find conversation data
    # Cursor stores data in various formats, we need to find the right structure
    for key, value in cursor_data.items():
        if composer_id in key.lower():
            if isinstance(value, dict):
                # Try to extract prompts/responses
                if "messages" in value:
                    messages = value["messages"]
                    for msg in messages:
                        if msg.get("role") == "user":
                            metadata["user_prompts"].append({
                                "text": msg.get("content", ""),
                                "timestamp": msg.get("timestamp", 0),
                                "sequence": len(metadata["user_prompts"])
                            })
                        elif msg.get("role") == "assistant":
                            metadata["assistant_responses"].append({
                                "text": msg.get("content", ""),
                                "timestamp": msg.get("timestamp", 0),
                                "sequence": len(metadata["assistant_responses"])
                            })
    
    return metadata


def reconstruct_prompt(composer_id: str = None):
    """Reconstruct the exact prompt that would be sent to LLM."""
    print("🔍 Finding Cursor database...")
    db_path = find_cursor_database()
    
    if not db_path:
        print("❌ Could not find Cursor database")
        print("\nTried locations:")
        print("  - macOS: ~/Library/Application Support/Cursor/User/globalStorage/state.vscdb")
        print("  - Windows: %APPDATA%/Cursor/User/globalStorage/state.vscdb")
        print("  - Linux: ~/.config/Cursor/User/globalStorage/state.vscdb")
        return
    
    print(f"✅ Found database: {db_path}")
    
    # If no composer_id provided, try to find one
    if not composer_id:
        print("\n📋 Searching for recent conversations...")
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Get all keys that might be conversations
            cursor.execute("""
                SELECT key 
                FROM ItemTable 
                WHERE key LIKE '%composer%' OR key LIKE '%chat%' OR key LIKE '%conversation%'
                LIMIT 10
            """)
            
            rows = cursor.fetchall()
            if rows:
                print(f"\nFound {len(rows)} potential conversation keys:")
                for i, row in enumerate(rows[:5], 1):
                    key = row[0]
                    print(f"  {i}. {key[:80]}...")
                
                # Try to extract composer_id from first key
                first_key = rows[0][0]
                # Look for UUID-like patterns
                import re
                uuid_match = re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', first_key, re.I)
                if uuid_match:
                    composer_id = uuid_match.group(0)
                    print(f"\n✅ Using composer_id: {composer_id}")
                else:
                    print("\n⚠️  Could not extract composer_id automatically")
                    print("Please provide a composer_id as argument")
                    return
            else:
                print("❌ No conversation data found in database")
                return
                
            conn.close()
        except Exception as e:
            print(f"❌ Error searching database: {e}")
            return
    
    print(f"\n📖 Extracting conversation data for: {composer_id}")
    cursor_data = extract_conversation_data(db_path, composer_id)
    
    if not cursor_data:
        print("❌ Could not extract conversation data")
        return
    
    print(f"✅ Extracted {len(cursor_data)} data entries")
    
    # Extract metadata
    metadata = extract_metadata_from_cursor_data(cursor_data, composer_id)
    
    if not metadata["user_prompts"]:
        print("\n⚠️  Could not extract conversation messages from database structure")
        print("\n📋 Available keys in data:")
        for key in list(cursor_data.keys())[:10]:
            print(f"  - {key}")
        print("\n💡 You may need to provide the exact data structure or use the extension's extractMetadata method")
        return
    
    print(f"\n✅ Extracted metadata:")
    print(f"   - {len(metadata['user_prompts'])} user prompts")
    print(f"   - {len(metadata['assistant_responses'])} assistant responses")
    print(f"   - {len(metadata['files_discussed'])} files discussed")
    print(f"   - {len(metadata['ai_todos'])} AI todos")
    print(f"   - {len(metadata['reasoning'])} reasoning blocks")
    
    # Create agent (mock LLM provider for prompt building only)
    class MockLLMProvider:
        pass
    
    agent = ChatAnalysisAgent(
        llm_provider=MockLLMProvider(),
        langsmith_client=None,
        use_heavy_task_model=False
    )
    
    # Build the prompt
    print("\n🔨 Reconstructing prompt...")
    inputs = {
        "user_prompts": metadata["user_prompts"],
        "assistant_responses": metadata["assistant_responses"],
        "files_discussed": metadata["files_discussed"],
        "ai_todos": metadata["ai_todos"],
        "reasoning": metadata["reasoning"] if metadata["reasoning"] else None,
        "previous_summary": None
    }
    
    prompt = agent._build_prompt(inputs)
    
    print("\n" + "="*80)
    print("📝 RECONSTRUCTED PROMPT (exact message sent to LLM)")
    print("="*80)
    print(prompt)
    print("="*80)
    
    # Also show token estimate
    import tiktoken
    try:
        enc = tiktoken.encoding_for_model("gpt-4")
        tokens = len(enc.encode(prompt))
        print(f"\n📊 Estimated tokens: ~{tokens:,}")
    except:
        # Fallback estimate
        tokens = len(prompt.split()) * 1.3
        print(f"\n📊 Estimated tokens: ~{int(tokens):,} (rough estimate)")


if __name__ == "__main__":
    composer_id = sys.argv[1] if len(sys.argv) > 1 else None
    reconstruct_prompt(composer_id)
