#!/usr/bin/env python3
"""
Reconstruct the exact LLM prompt from Cursor SQLite database.

Uses the same extraction logic as the extension to get conversation data,
then uses ChatAnalysisAgent to build the exact prompt that would be sent.
"""

import sys
import sqlite3
import json
import os
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))


def find_cursor_databases() -> List[Path]:
    """Find all Cursor SQLite databases."""
    home = Path.home()
    databases = []
    
    # macOS
    if sys.platform == "darwin":
        global_storage = home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
        if global_storage.exists():
            databases.append(global_storage)
        
        workspace_storage = home / "Library/Application Support/Cursor/User/workspaceStorage"
        if workspace_storage.exists():
            for workspace_dir in workspace_storage.iterdir():
                db_path = workspace_dir / "state.vscdb"
                if db_path.exists():
                    databases.append(db_path)
    
    # Windows
    elif sys.platform == "win32":
        appdata = os.getenv("APPDATA", "")
        if appdata:
            global_storage = Path(appdata) / "Cursor/User/globalStorage/state.vscdb"
            if global_storage.exists():
                databases.append(global_storage)
    
    # Linux
    else:
        global_storage = home / ".config/Cursor/User/globalStorage/state.vscdb"
        if global_storage.exists():
            databases.append(global_storage)
    
    return databases


def find_composer_in_database(db_path: Path, composer_id: str) -> bool:
    """Check if composer exists in database."""
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # Check cursorDiskKV table (where bubbles are stored)
        cursor.execute("""
            SELECT COUNT(*) 
            FROM cursorDiskKV 
            WHERE key LIKE ?
        """, (f"bubbleId:{composer_id}:%",))
        
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except:
        return False


def extract_metadata_from_db(db_path: Path, composer_id: str) -> Optional[Dict[str, Any]]:
    """
    Extract metadata from Cursor database using the same logic as the extension.
    
    This replicates the extractMetadata() method from CursorDatabaseReader.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get composerData for createdAt
        conversation_created_at = 0
        try:
            cursor.execute("""
                SELECT value 
                FROM cursorDiskKV 
                WHERE key = ?
            """, (f"composerData:{composer_id}",))
            
            row = cursor.fetchone()
            if row and row[0]:
                composer_data = json.loads(row[0])
                if composer_data.get("createdAt"):
                    conversation_created_at = composer_data["createdAt"]
        except:
            pass
        
        # Get all bubbles for this composer
        cursor.execute("""
            SELECT key, value 
            FROM cursorDiskKV 
            WHERE key LIKE ?
            ORDER BY key
        """, (f"bubbleId:{composer_id}:%",))
        
        rows = cursor.fetchall()
        if not rows:
            conn.close()
            return None
        
        # Parse bubbles
        bubbles = []
        for row in rows:
            try:
                bubble = json.loads(row[1])
                bubble["_key"] = row[0]  # Keep key for reference
                bubbles.append(bubble)
            except:
                continue
        
        conn.close()
        
        if not bubbles:
            return None
        
        # Sort by createdAt or sequence (same logic as extension)
        def get_timestamp(bubble):
            # Try createdAt first (ISO format)
            if bubble.get("createdAt"):
                try:
                    # Handle ISO format: "2025-10-24T20:57:19.887Z"
                    created_at = bubble["createdAt"]
                    if isinstance(created_at, str):
                        # Remove Z and add timezone if needed
                        dt_str = created_at.replace("Z", "+00:00")
                        dt = datetime.fromisoformat(dt_str)
                        return int(dt.timestamp() * 1000)
                except:
                    pass
            # Fallback to timestamp field
            return bubble.get("timestamp", 0)
        
        bubbles.sort(key=get_timestamp)
        
        # Extract metadata in the same format as extension
        user_prompts = []
        assistant_responses = []
        reasoning_blocks = []
        files_discussed = []
        ai_todos = []
        timestamps = []
        
        for i, bubble in enumerate(bubbles):
            bubble_type = bubble.get("type")
            text = bubble.get("text", "")
            bubble_id = bubble.get("bubbleId", "")
            timestamp = get_timestamp(bubble)
            
            timestamps.append(int(timestamp))
            
            if bubble_type == 1:  # User message
                user_prompts.append({
                    "text": text,
                    "timestamp": int(timestamp),
                    "sequence": len(user_prompts)  # 0-based sequence
                })
                
                # Extract files from attachedFileCodeChunksMetadataOnly
                if bubble.get("attachedFileCodeChunksMetadataOnly"):
                    for chunk in bubble["attachedFileCodeChunksMetadataOnly"]:
                        file_path = chunk.get("relativeWorkspacePath", "")
                        if file_path and file_path not in [f["path"] for f in files_discussed]:
                            files_discussed.append({
                                "path": file_path,
                                "mentionedAt": int(timestamp)
                            })
            
            elif bubble_type == 2:  # Assistant message
                # Match user prompt sequence (assistant response should match user prompt index)
                # In Cursor, they're interleaved, so we use the same sequence counter
                assistant_responses.append({
                    "text": text,
                    "timestamp": int(timestamp),
                    "hasReasoning": bool(bubble.get("allThinkingBlocks")),
                    "sequence": len(assistant_responses)  # Should match corresponding user prompt
                })
                
                # Extract reasoning blocks
                if bubble.get("allThinkingBlocks"):
                    reasoning_blocks.append({
                        "bubbleId": bubble_id,
                        "reasoning": bubble["allThinkingBlocks"],
                        "timestamp": int(timestamp),
                        "sequence": len(reasoning_blocks)
                    })
                
                # Extract AI todos
                if bubble.get("todos"):
                    for todo_str in bubble["todos"]:
                        try:
                            todo = json.loads(todo_str) if isinstance(todo_str, str) else todo_str
                            ai_todos.append({
                                "id": todo.get("id", ""),
                                "content": todo.get("content", ""),
                                "status": todo.get("status", "pending"),
                                "bubbleId": bubble_id,
                                "timestamp": int(timestamp)
                            })
                        except:
                            pass
        
        # Calculate conversation time range
        conversation_start = min(timestamps) if timestamps else 0
        conversation_end = max(timestamps) if timestamps else 0
        
        # Build thread name (simplified - extension has more logic)
        thread_name = user_prompts[0]["text"][:50] if user_prompts else "Chat Session"
        
        # Smart intent (first prompt, simplified)
        smart_intent = user_prompts[0]["text"] if user_prompts else "Unknown"
        
        # Intent evolution (simplified - extension has more logic)
        intent_evolution = []
        for i, prompt in enumerate(user_prompts[:5]):  # First 5 prompts
            intent_evolution.append({
                "stage": i,
                "intent": prompt["text"][:100],
                "timestamp": prompt["timestamp"]
            })
        
        # Token usage (sum from bubbles)
        total_input = 0
        total_output = 0
        for bubble in bubbles:
            if bubble.get("tokenCount"):
                total_input += bubble["tokenCount"].get("inputTokens", 0)
                total_output += bubble["tokenCount"].get("outputTokens", 0)
        
        metadata = {
            "threadName": thread_name,
            "smartIntent": smart_intent,
            "userPrompts": user_prompts,
            "assistantResponses": assistant_responses,
            "reasoning": reasoning_blocks,
            "timestamps": timestamps,
            "intentEvolution": intent_evolution,
            "filesDiscussed": files_discussed,
            "aiTodos": ai_todos,
            "tokenUsage": {
                "totalInput": total_input,
                "totalOutput": total_output
            },
            "conversationStart": int(conversation_start),
            "conversationEnd": int(conversation_end)
        }
        
        return metadata
        
    except Exception as e:
        print(f"❌ Error extracting metadata: {e}")
        import traceback
        traceback.print_exc()
        return None


def reconstruct_prompt(composer_id: str = None):
    """Reconstruct the exact prompt that would be sent to LLM."""
    print("🔍 Finding Cursor databases...")
    databases = find_cursor_databases()
    
    if not databases:
        print("❌ Could not find any Cursor databases")
        return
    
    print(f"✅ Found {len(databases)} database(s)")
    
    # If no composer_id, search for one
    if not composer_id:
        print("\n📋 Searching for recent conversations...")
        for db_path in databases:
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()
                
                # Get recent composer IDs from bubbleId keys
                cursor.execute("""
                    SELECT DISTINCT key 
                    FROM cursorDiskKV 
                    WHERE key LIKE 'bubbleId:%'
                    LIMIT 20
                """)
                
                rows = cursor.fetchall()
                if rows:
                    # Extract composer IDs
                    import re
                    composer_ids = set()
                    for row in rows:
                        match = re.search(r'bubbleId:([0-9a-f-]{36}):', row[0], re.I)
                        if match:
                            composer_ids.add(match.group(1))
                    
                    if composer_ids:
                        print(f"\nFound {len(composer_ids)} composer IDs in {db_path.name}:")
                        for i, cid in enumerate(list(composer_ids)[:5], 1):
                            print(f"  {i}. {cid}")
                        
                        composer_id = list(composer_ids)[0]
                        print(f"\n✅ Using first composer_id: {composer_id}")
                        break
                
                conn.close()
            except Exception as e:
                print(f"⚠️  Error searching {db_path}: {e}")
                continue
        
        if not composer_id:
            print("\n❌ Could not find any composer IDs")
            print("Usage: python3 reconstruct_prompt_from_db.py <composer-id>")
            return
    
    # Find the database containing this composer
    db_path = None
    for db in databases:
        if find_composer_in_database(db, composer_id):
            db_path = db
            break
    
    if not db_path:
        print(f"❌ Composer {composer_id} not found in any database")
        return
    
    print(f"\n✅ Found composer in: {db_path}")
    
    # Extract metadata
    print(f"\n📖 Extracting conversation data...")
    metadata = extract_metadata_from_db(db_path, composer_id)
    
    if not metadata:
        print("❌ Could not extract metadata")
        return
    
    print(f"\n✅ Extracted metadata:")
    print(f"   - Thread: {metadata['threadName']}")
    print(f"   - {len(metadata['userPrompts'])} user prompts")
    print(f"   - {len(metadata['assistantResponses'])} assistant responses")
    print(f"   - {len(metadata['filesDiscussed'])} files discussed")
    print(f"   - {len(metadata['aiTodos'])} AI todos")
    print(f"   - {len(metadata['reasoning'])} reasoning blocks")
    print(f"   - Duration: {datetime.fromtimestamp(metadata['conversationStart']/1000).isoformat()} to {datetime.fromtimestamp(metadata['conversationEnd']/1000).isoformat()}")
    
    # Convert to agent input format
    from turingmind_mcp.agents.chat_analysis_agent import ChatAnalysisAgent
    
    # Create mock LLM provider (we only need prompt building, not actual LLM call)
    class MockLLMProvider:
        pass
    
    agent = ChatAnalysisAgent(
        llm_provider=MockLLMProvider(),
        langsmith_client=None,
        use_heavy_task_model=False
    )
    
    # Prepare inputs in the format expected by agent
    inputs = {
        "user_prompts": metadata["userPrompts"],
        "assistant_responses": metadata["assistantResponses"],
        "files_discussed": [f["path"] for f in metadata["filesDiscussed"]],
        "ai_todos": [
            {"content": t["content"], "status": t["status"]}
            for t in metadata["aiTodos"]
        ],
        "reasoning": metadata["reasoning"] if metadata["reasoning"] else None,
        "previous_summary": None  # Full analysis mode
    }
    
    # Build the prompt
    print("\n🔨 Reconstructing prompt using ChatAnalysisAgent...")
    prompt = agent._build_prompt(inputs)
    
    print("\n" + "="*80)
    print("📝 RECONSTRUCTED PROMPT (exact message sent to LLM)")
    print("="*80)
    print(prompt)
    print("="*80)
    
    # Token estimate
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model("gpt-4")
        tokens = len(enc.encode(prompt))
        print(f"\n📊 Estimated tokens: ~{tokens:,}")
    except ImportError:
        # Rough estimate: ~1.3 tokens per word
        words = len(prompt.split())
        tokens = int(words * 1.3)
        print(f"\n📊 Estimated tokens: ~{tokens:,} (rough estimate, ~{words:,} words)")
    
    # Save to file
    output_file = Path(__file__).parent / f"reconstructed_prompt_{composer_id[:8]}.txt"
    with open(output_file, "w") as f:
        f.write(f"Composer ID: {composer_id}\n")
        f.write(f"Database: {db_path}\n")
        f.write(f"Extracted: {datetime.now().isoformat()}\n")
        f.write(f"\n{'='*80}\n")
        f.write("RECONSTRUCTED PROMPT\n")
        f.write(f"{'='*80}\n\n")
        f.write(prompt)
    
    print(f"\n💾 Saved to: {output_file}")


if __name__ == "__main__":
    composer_id = sys.argv[1] if len(sys.argv) > 1 else None
    reconstruct_prompt(composer_id)
