"""
Cursor Database Reader

Reads chat metadata from Cursor's SQLite database (state.vscdb).
Provides functions to extract conversation data, detect exchanges, and get file information.
"""

import json
import logging
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("turingmind-mcp.cursor-reader")

# Constants
DATABASE_FILENAME = "state.vscdb"

def _get_cursor_global_storage() -> Path:
    """Get Cursor global storage path (allows mocking in tests)."""
    return Path.home() / "Library/Application Support/Cursor/User/globalStorage"

def _get_cursor_workspace_storage() -> Path:
    """Get Cursor workspace storage path (allows mocking in tests)."""
    return Path.home() / "Library/Application Support/Cursor/User/workspaceStorage"


def find_cursor_database(composer_id: Optional[str] = None) -> Optional[Path]:
    """
    Find Cursor database path.
    
    Args:
        composer_id: Optional composer ID to verify existence
        
    Returns:
        Path to database file, or None if not found
    """
    # Check globalStorage first (most common)
    global_db = _get_cursor_global_storage() / DATABASE_FILENAME
    if global_db.exists():
        if composer_id is None or composer_exists_in_database(str(global_db), composer_id):
            logger.debug(f"Found database in globalStorage: {global_db}")
            return global_db
    
    # Check workspaceStorage
    workspace_storage = _get_cursor_workspace_storage()
    if workspace_storage.exists():
        for workspace_dir in workspace_storage.iterdir():
            if workspace_dir.is_dir():
                workspace_db = workspace_dir / DATABASE_FILENAME
                if workspace_db.exists():
                    if composer_id is None or composer_exists_in_database(str(workspace_db), composer_id):
                        logger.debug(f"Found database in workspace: {workspace_db}")
                        return workspace_db
    
    logger.warning("Cursor database not found")
    return None


def composer_exists_in_database(db_path: str, composer_id: str) -> bool:
    """Check if composer exists in database."""
    try:
        # Check if any bubbles exist for this composer
        query = "SELECT 1 FROM cursorDiskKV WHERE key LIKE ? LIMIT 1"
        results = execute_sqlite_query(db_path, query, (f"bubbleId:{composer_id}:%",))
        return len(results) > 0
    except Exception as e:
        logger.error(f"Error checking composer existence: {e}")
        return False


def execute_sqlite_query(db_path: str, query: str, params: Tuple = ()) -> List[Dict[str, Any]]:
    """
    Execute SQLite query and return results as list of dicts.
    
    Args:
        db_path: Path to database file
        query: SQL query string
        params: Query parameters
        
    Returns:
        List of result rows as dictionaries
    """
    try:
        # Use sqlite3 command-line tool for readonly access
        cmd = ["sqlite3", db_path, "-readonly", "-json"]
        
        # Build query with parameters
        if params:
            # Escape single quotes in parameters
            escaped_params = []
            for param in params:
                if isinstance(param, (int, float)):
                    escaped_params.append(str(param))
                else:
                    escaped = str(param).replace("'", "''")
                    escaped_params.append(f"'{escaped}'")
            
            # Replace ? with parameters
            sql = query
            for param in escaped_params:
                sql = sql.replace("?", param, 1)
        else:
            sql = query
        
        # Execute query
        result = subprocess.run(
            cmd,
            input=sql,
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode != 0:
            logger.error(f"SQLite query failed: {result.stderr}")
            return []
        
        # Parse JSON results
        trimmed = result.stdout.strip()
        if not trimmed:
            return []
        
        # sqlite3 -json returns JSON arrays: [{"col1":"val1","col2":"val2"}]
        # Each line is a JSON array containing one or more row objects
        all_results = []
        
        # Try parsing as single JSON first (for single-row results)
        try:
            single_parse = json.loads(trimmed)
            if isinstance(single_parse, list):
                # Array of row objects
                all_results = single_parse
            else:
                # Single object (shouldn't happen with -json, but handle it)
                all_results = [single_parse]
        except json.JSONDecodeError:
            # If single parse fails, split by newlines (multiple rows)
            lines = trimmed.split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, list):
                        # Array of row objects - extract them
                        all_results.extend(parsed)
                    else:
                        # Single object
                        all_results.append(parsed)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse JSON line: {line[:100]}")
        
        # Results should now be list of dicts (row objects)
        # Each element is a dict like {"composerId": "...", "lastActivity": "..."}
        return all_results
    except Exception as e:
        logger.error(f"Error executing SQLite query: {e}")
        return []


def get_most_recently_active_composer(db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Get the most recently active composer.
    
    Returns:
        Dict with composerId, lastActivityAt, bubbleCount, or None
    """
    if db_path is None:
        db_path = find_cursor_database()
        if db_path is None:
            return None
        db_path = str(db_path)
    
    try:
        # Query for the composer with the most recent bubble (by createdAt timestamp)
        # Key format: bubbleId:<composer-id>:<bubble-id>
        # We need to extract just the composer-id (36 chars after "bubbleId:")
        query = """
            SELECT 
                substr(key, 10, 36) as composerId,
                json_extract(value, '$.createdAt') as lastActivity
            FROM cursorDiskKV 
            WHERE key LIKE 'bubbleId:%' 
                AND json_extract(value, '$.createdAt') IS NOT NULL
            ORDER BY json_extract(value, '$.createdAt') DESC 
            LIMIT 1
        """
        
        results = execute_sqlite_query(db_path, query)
        if not results:
            logger.debug("No recent bubbles found")
            return None
        
        most_recent = results[0]
        composer_id_full = most_recent.get("composerId", "")
        # Extract just the composer ID
        # Key format: bubbleId:<composer-id>:<bubble-id>
        # substr(key, 10, 36) extracts 36 chars starting at position 10
        # For UUIDs (36 chars), this is correct. For shorter IDs, we need to stop at the next colon
        if ":" in composer_id_full:
            # If there's a colon, extract just the part before it (the composer ID)
            composer_id = composer_id_full.split(":")[0]
        else:
            # No colon means we got the full composer ID (might be shorter than 36 chars in tests)
            composer_id = composer_id_full
        last_activity_str = most_recent.get("lastActivity")
        
        # Convert ISO string to timestamp
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(last_activity_str.replace("Z", "+00:00"))
            last_activity_at = int(dt.timestamp() * 1000)
        except Exception:
            logger.warning(f"Failed to parse timestamp: {last_activity_str}")
            return None
        
        # Get bubble count for this composer
        count_query = "SELECT COUNT(*) as count FROM cursorDiskKV WHERE key LIKE ?"
        count_results = execute_sqlite_query(db_path, count_query, (f"bubbleId:{composer_id}:%",))
        bubble_count = count_results[0].get("count", 0) if count_results else 0
        
        logger.debug(
            f"Most recent composer: {composer_id[:8]}... "
            f"(last activity: {last_activity_at}, bubbles: {bubble_count})"
        )
        
        return {
            "composerId": composer_id,
            "lastActivityAt": last_activity_at,
            "bubbleCount": bubble_count
        }
    except Exception as e:
        logger.error(f"Error getting most recently active composer: {e}", exc_info=True)
        return None


def get_last_exchange_state(db_path: str, composer_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the state of the last exchange for a composer.
    Handles multi-bubble assistant responses correctly.
    
    Returns:
        Dict with:
        - lastBubbleType (1=user, 2=assistant)
        - previousBubbleType (1=user, 2=assistant)
        - totalBubbles (count)
        - userMessageCount (count)
        - assistantResponseCount (count)
        - lastBubbleTimestamp
        - isCompleteExchange (bool)
    """
    try:
        # Get last bubble
        last_bubble_query = """
            SELECT 
                json_extract(value, '$.type') as bubbleType,
                json_extract(value, '$.createdAt') as createdAt,
                json_extract(value, '$.bubbleId') as bubbleId
            FROM cursorDiskKV
            WHERE key LIKE ?
            ORDER BY json_extract(value, '$.createdAt') DESC
            LIMIT 1
        """
        last_bubble_results = execute_sqlite_query(
            db_path, 
            last_bubble_query, 
            (f"bubbleId:{composer_id}:%",)
        )
        
        if not last_bubble_results:
            return None
        
        last_bubble = last_bubble_results[0]
        bubble_type = last_bubble.get("bubbleType")
        created_at_str = last_bubble.get("createdAt")
        
        # Parse timestamp
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            created_at = int(dt.timestamp() * 1000)
        except Exception:
            created_at = 0
        
        # Get counts
        count_query = """
            SELECT 
                COUNT(*) as totalBubbles,
                SUM(CASE WHEN json_extract(value, '$.type') = 1 THEN 1 ELSE 0 END) as userCount,
                SUM(CASE WHEN json_extract(value, '$.type') = 2 THEN 1 ELSE 0 END) as assistantCount
            FROM cursorDiskKV
            WHERE key LIKE ?
        """
        count_results = execute_sqlite_query(db_path, count_query, (f"bubbleId:{composer_id}:%",))
        counts = count_results[0] if count_results else {}
        
        # Find the last user message (type 1)
        last_user_query = """
            SELECT 
                json_extract(value, '$.type') as bubbleType,
                json_extract(value, '$.createdAt') as createdAt
            FROM cursorDiskKV
            WHERE key LIKE ? AND json_extract(value, '$.type') = 1
            ORDER BY json_extract(value, '$.createdAt') DESC
            LIMIT 1
        """
        last_user_results = execute_sqlite_query(
            db_path,
            last_user_query,
            (f"bubbleId:{composer_id}:%",)
        )
        
        # Find the bubble before the last user message
        previous_bubble_type = None
        previous_bubble_timestamp = 0
        
        if last_user_results:
            last_user = last_user_results[0]
            last_user_time = last_user.get("createdAt")
            
            # Get bubble immediately before last user message
            previous_query = """
                SELECT 
                    json_extract(value, '$.type') as bubbleType,
                    json_extract(value, '$.createdAt') as createdAt
                FROM cursorDiskKV
                WHERE key LIKE ? 
                    AND json_extract(value, '$.createdAt') < ?
                ORDER BY json_extract(value, '$.createdAt') DESC
                LIMIT 1
            """
            previous_results = execute_sqlite_query(
                db_path,
                previous_query,
                (f"bubbleId:{composer_id}:%", last_user_time)
            )
            
            if previous_results:
                previous_bubble = previous_results[0]
                previous_bubble_type = previous_bubble.get("bubbleType")
                prev_time_str = previous_bubble.get("createdAt")
                try:
                    prev_dt = datetime.fromisoformat(prev_time_str.replace("Z", "+00:00"))
                    previous_bubble_timestamp = int(prev_dt.timestamp() * 1000)
                except Exception:
                    pass
        
        # Exchange is complete if we found a user message with an assistant before it
        # This handles multi-bubble assistant responses correctly
        is_complete_exchange = (
            last_user_results and 
            previous_bubble_type == 2
        )
        
        return {
            "lastBubbleType": bubble_type,
            "previousBubbleType": previous_bubble_type,
            "totalBubbles": counts.get("totalBubbles", 0) or 0,
            "userMessageCount": counts.get("userCount", 0) or 0,
            "assistantResponseCount": counts.get("assistantCount", 0) or 0,
            "lastBubbleTimestamp": created_at,
            "isCompleteExchange": is_complete_exchange,
            "previousBubbleTimestamp": previous_bubble_timestamp
        }
    except Exception as e:
        logger.error(f"Error getting last exchange state: {e}", exc_info=True)
        return None


def extract_timestamp(bubble: Dict[str, Any], composer_created_at: int = 0) -> int:
    """
    Extract timestamp from bubble's createdAt field.
    Falls back to composerCreatedAt if bubble doesn't have timestamp.
    """
    if bubble.get("createdAt"):
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(bubble["createdAt"].replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except Exception:
            pass
    
    if composer_created_at > 0:
        return composer_created_at
    
    return 0


def extract_smart_intent(prompts: List[Dict[str, Any]]) -> str:
    """Extract smart intent - skip filler messages."""
    FILLER_MESSAGES = [
        'no', 'yes', 'ok', 'okay', 'sure', 'thanks', 'thank you',
        'y', 'n', 'yep', 'nope', 'yeah', 'nah', 'continue',
        'go ahead', 'proceed', 'do it', 'sounds good', 'perfect',
        'great', 'good', 'fine', 'alright', 'k', 'kk'
    ]
    
    # Find first substantive message
    for prompt in prompts:
        text = prompt.get("text", "").strip().lower()
        
        # Skip if too short
        if len(text) < 15:
            continue
        
        # Skip if it's a filler message
        if text in FILLER_MESSAGES:
            continue
        
        # Skip if mostly punctuation or single word
        words = prompt.get("text", "").strip().split()
        if len(words) < 3:
            continue
        
        # Found a real intent
        intent = prompt.get("text", "").strip()
        if len(intent) > 150:
            intent = intent[:147] + "..."
        return intent
    
    # Fallback to first prompt
    if prompts:
        return prompts[0].get("text", "")[:100] or "Chat session"
    
    return "Chat session"


def generate_thread_name(intent: str) -> str:
    """Generate thread name from smart intent."""
    if not intent or intent == "Chat session":
        return "Chat session"
    
    # Clean up the intent for a thread name
    import re
    name = re.sub(r"^(can you |please |i want to |i need to |help me |let's )", "", intent, flags=re.IGNORECASE)
    name = re.sub(r"\?+$", "", name).strip()
    
    # Capitalize first letter
    if name:
        name = name[0].upper() + name[1:]
    
    # Truncate for thread name
    if len(name) > 60:
        name = name[:57] + "..."
    
    return name


def extract_metadata(composer_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Extract full metadata from Cursor database for a composer.
    
    Returns:
        Dict with all metadata fields or None if not found
    """
    if db_path is None:
        db_path = find_cursor_database(composer_id)
        if db_path is None:
            logger.warning(f"Database not found for composer: {composer_id}")
            return None
        db_path = str(db_path)
    
    try:
        # Get composerData to extract real createdAt timestamp
        conversation_created_at = 0
        try:
            composer_data_rows = execute_sqlite_query(
                db_path,
                "SELECT value FROM cursorDiskKV WHERE key = ?",
                (f"composerData:{composer_id}",)
            )
            
            if composer_data_rows and composer_data_rows[0].get("value"):
                composer_data = json.loads(composer_data_rows[0]["value"])
                if composer_data.get("createdAt"):
                    conversation_created_at = composer_data["createdAt"]
                    logger.debug(f"Found createdAt from composerData: {conversation_created_at}")
        except Exception as e:
            logger.warning(f"Could not read composerData: {e}")
        
        # Query bubbles for this composer, ordered by createdAt
        query_pattern = f"bubbleId:{composer_id}:%"
        rows = execute_sqlite_query(
            db_path,
            """SELECT key, value 
            FROM cursorDiskKV 
            WHERE key LIKE ? 
            ORDER BY 
                CASE 
                    WHEN json_extract(value, '$.createdAt') IS NOT NULL 
                    THEN json_extract(value, '$.createdAt')
                    ELSE '1970-01-01T00:00:00.000Z'
                END ASC""",
            (query_pattern,)
        )
        
        logger.debug(f"Found {len(rows)} rows (ordered by timestamp)")
        
        metadata = {
            "threadName": "",
            "smartIntent": "",
            "userPrompts": [],
            "reasoning": [],
            "assistantResponses": [],
            "timestamps": [],
            "intentEvolution": [],
            "filesDiscussed": [],
            "relatedCommits": [],
            "tokenUsage": {"totalInput": 0, "totalOutput": 0},
            "aiTodos": [],
            "conversationStart": conversation_created_at,
            "conversationEnd": conversation_created_at
        }
        
        sequence = 0
        files_set = set()
        
        for row in rows:
            try:
                value_str = row.get("value", "")
                if isinstance(value_str, bytes):
                    value_str = value_str.decode("utf-8")
                
                bubble = json.loads(value_str)
                sequence += 1
                
                # Extract timestamp
                timestamp = extract_timestamp(bubble, conversation_created_at)
                
                # Track conversation time range
                if metadata["conversationStart"] == 0 or timestamp < metadata["conversationStart"]:
                    metadata["conversationStart"] = timestamp
                if timestamp > metadata["conversationEnd"]:
                    metadata["conversationEnd"] = timestamp
                
                # Extract user prompts (type === 1)
                if bubble.get("type") == 1:
                    metadata["userPrompts"].append({
                        "text": bubble.get("text", ""),
                        "timestamp": timestamp,
                        "sequence": sequence
                    })
                    
                    # Track intent evolution
                    metadata["intentEvolution"].append({
                        "stage": len(metadata["userPrompts"]),
                        "intent": bubble.get("text", ""),
                        "timestamp": timestamp
                    })
                    
                    # Extract files from user message
                    attached_files = bubble.get("attachedFileCodeChunksMetadataOnly", [])
                    if attached_files:
                        for file_info in attached_files:
                            file_path = file_info.get("relativeWorkspacePath")
                            if file_path and file_path not in files_set:
                                files_set.add(file_path)
                                metadata["filesDiscussed"].append({
                                    "path": file_path,
                                    "mentionedAt": timestamp
                                })
                
                # Extract reasoning (type === 2, assistant)
                if bubble.get("type") == 2:
                    thinking_blocks = bubble.get("allThinkingBlocks", [])
                    if thinking_blocks:
                        metadata["reasoning"].append({
                            "bubbleId": bubble.get("bubbleId", ""),
                            "reasoning": thinking_blocks,
                            "timestamp": timestamp,
                            "sequence": sequence
                        })
                    
                    # Extract assistant response (FULL TEXT)
                    full_response_text = bubble.get("text", "")
                    metadata["assistantResponses"].append({
                        "text": full_response_text,
                        "timestamp": timestamp,
                        "hasReasoning": bool(thinking_blocks),
                        "sequence": sequence
                    })
                    
                    # Accumulate token usage
                    token_count = bubble.get("tokenCount", {})
                    if token_count:
                        metadata["tokenUsage"]["totalInput"] += token_count.get("inputTokens", 0)
                        metadata["tokenUsage"]["totalOutput"] += token_count.get("outputTokens", 0)
                    
                    # Extract AI's todo list
                    todos = bubble.get("todos", [])
                    if todos:
                        for todo_str in todos:
                            try:
                                todo = json.loads(todo_str) if isinstance(todo_str, str) else todo_str
                                # Avoid duplicates by checking ID
                                existing = next((t for t in metadata["aiTodos"] if t.get("id") == todo.get("id")), None)
                                if not existing:
                                    metadata["aiTodos"].append({
                                        "id": todo.get("id", f"todo-{len(metadata['aiTodos'])}"),
                                        "content": todo.get("content", ""),
                                        "status": todo.get("status", "pending"),
                                        "dependencies": todo.get("dependencies"),
                                        "bubbleId": bubble.get("bubbleId"),
                                        "timestamp": timestamp
                                    })
                                elif existing.get("status") != todo.get("status"):
                                    existing["status"] = todo.get("status")
                            except Exception:
                                # Skip unparseable todos
                                pass
                
                metadata["timestamps"].append(timestamp)
            except Exception as e:
                logger.warning(f"Error parsing bubble: {e}")
                continue
        
        # Extract smart intent
        metadata["smartIntent"] = extract_smart_intent(metadata["userPrompts"])
        
        # Generate thread name
        metadata["threadName"] = generate_thread_name(metadata["smartIntent"])
        logger.debug(f"Generated thread name: {metadata['threadName']}")
        
        logger.info(
            f"Extracted: {len(metadata['userPrompts'])} prompts, "
            f"{len(metadata['reasoning'])} reasoning blocks, "
            f"{len(metadata['assistantResponses'])} responses, "
            f"{len(metadata['filesDiscussed'])} files, "
            f"{len(metadata['aiTodos'])} AI todos, "
            f"{metadata['tokenUsage']['totalInput'] + metadata['tokenUsage']['totalOutput']} tokens"
        )
        
        return metadata
    except Exception as e:
        logger.error(f"Error extracting metadata: {e}", exc_info=True)
        return None
