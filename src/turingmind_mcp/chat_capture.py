"""
Chat Capture Module

Handles automatic detection and capture of Cursor chat exchanges.
Moved from VS Code extension to MCP for centralized business logic.
"""

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Callable, Awaitable

from .cursor_database_reader import (
    find_cursor_database,
    get_last_exchange_state,
    get_most_recently_active_composer,
)
from .database import MemoryDatabase

logger = logging.getLogger("turingmind-mcp.chat-capture")

# Constants
AUTO_CAPTURE_INTERVAL_MS = 3000
CURRENT_CHAT_UPDATE_COOLDOWN_MS = 30000  # 30 seconds
LLM_COOLDOWN_MS = 30000  # 30 seconds
MIN_NEW_MESSAGES_FOR_LLM = 1
RECENT_ACTIVITY_WINDOW_MS = 6 * 60 * 60 * 1000  # 6 hours
VERY_RECENT_ACTIVITY_MS = 2 * 60 * 60 * 1000  # 2 hours
MAX_CHAT_AGE_MS = 7 * 24 * 60 * 60 * 1000  # 7 days
OLD_CHAT_THRESHOLD_MS = 7 * 24 * 60 * 60 * 1000  # 7 days
RECENT_WINDOW_MS = 7 * 24 * 60 * 60 * 1000  # 7 days
MAX_FILES_TO_PROCESS = 50
MAX_DIFF_SIZE = 500000  # 500KB


async def check_exchanges(
    db: MemoryDatabase,
    repo: str,
    session_start_time: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Check for new exchanges and return those ready to capture.
    
    Args:
        db: MemoryDatabase instance
        repo: Repository identifier
        session_start_time: Optional session start timestamp (for filtering old chats)
        
    Returns:
        Dict with 'exchanges' list containing ready-to-capture exchanges
    """
    try:
        # Find Cursor database
        db_path = find_cursor_database()
        if db_path is None:
            logger.warning("Cursor database not found")
            return {"exchanges": []}
        
        db_path_str = str(db_path)
        
        # Get most recently active composer
        most_recent = get_most_recently_active_composer(db_path_str)
        if not most_recent or most_recent.get("bubbleCount", 0) < 2:
            return {"exchanges": []}
        
        composer_id = most_recent["composerId"]
        last_activity_at = most_recent["lastActivityAt"]
        
        # Get exchange state
        exchange_state = get_last_exchange_state(db_path_str, composer_id)
        if not exchange_state:
            return {"exchanges": []}
        
        # Get cached state
        cached_state = db.get_chat_capture_state(composer_id)
        now = int(time.time() * 1000)
        
        # Check exchange readiness
        exchange_is_complete = exchange_state.get("isCompleteExchange", False)
        has_new_exchange = exchange_state.get("totalBubbles", 0) > (
            cached_state.get("messageCount", 0) if cached_state else 0
        )
        
        # Check write completion (500ms debounce)
        time_since_last_activity = now - (last_activity_at or 0)
        is_write_complete = time_since_last_activity >= 500
        
        # Check cooldown
        last_captured_at = cached_state.get("lastCapturedAt", 0) if cached_state else 0
        cooldown_expired = not cached_state or (now - last_captured_at) >= CURRENT_CHAT_UPDATE_COOLDOWN_MS
        
        # Determine if should capture
        should_capture = (
            exchange_is_complete and
            has_new_exchange and
            is_write_complete and
            cooldown_expired
        )
        
        if not should_capture:
            # Log reasons for debugging
            reasons = []
            if not exchange_is_complete:
                reasons.append("exchange not complete")
            if not has_new_exchange:
                reasons.append("no new exchange")
            if not is_write_complete:
                reasons.append(f"write not complete ({time_since_last_activity}ms < 500ms)")
            if not cooldown_expired:
                reasons.append(f"in cooldown ({int((now - last_captured_at) / 1000)}s < {CURRENT_CHAT_UPDATE_COOLDOWN_MS / 1000}s)")
            
            if reasons and not (len(reasons) == 1 and "waiting for next user message" in reasons[0]):
                logger.debug(f"Exchange not ready for {composer_id[:8]}...: {', '.join(reasons)}")
            
            return {"exchanges": []}
        
        # Determine if should enhance with LLM
        time_since_llm = (
            now - cached_state.get("lastLLMEnhancedAt", 0)
            if cached_state else float("inf")
        )
        new_message_count = exchange_state.get("totalBubbles", 0) - (
            cached_state.get("messageCount", 0) if cached_state else 0
        )
        
        should_enhance_llm = (
            not cached_state or  # First capture
            (new_message_count >= MIN_NEW_MESSAGES_FOR_LLM and time_since_llm >= LLM_COOLDOWN_MS)
        )
        
        logger.info(
            f"✅ New exchange ready: {composer_id[:8]}... "
            f"({exchange_state.get('userMessageCount', 0)} user, "
            f"{exchange_state.get('assistantResponseCount', 0)} assistant, "
            f"LLM: {should_enhance_llm})"
        )
        
        return {
            "exchanges": [{
                "composerId": composer_id,
                "exchangeState": exchange_state,
                "shouldEnhanceLLM": should_enhance_llm,
                "isUpdate": cached_state is not None,
            }]
        }
    
    except Exception as e:
        logger.error(f"Error checking exchanges: {e}", exc_info=True)
        return {"exchanges": [], "error": str(e)}


def should_capture_chat(
    metadata: Dict[str, Any],
    cached_state: Optional[Dict[str, Any]],
    session_start_time: Optional[int],
    is_update: bool,
) -> Tuple[bool, Optional[str]]:
    """
    Determine if chat should be captured based on age and activity.
    
    Returns:
        Tuple of (should_capture, reason_if_skipped)
    """
    now = int(time.time() * 1000)
    chat_start = metadata.get("conversationStart", 0)
    chat_end = metadata.get("conversationEnd", chat_start)
    session_start = session_start_time or 0
    
    # Check for very recent messages
    user_prompts = metadata.get("userPrompts", [])
    assistant_responses = metadata.get("assistantResponses", [])
    
    all_timestamps = [
        p.get("timestamp", 0) for p in user_prompts
    ] + [
        r.get("timestamp", 0) for r in assistant_responses
    ]
    
    most_recent_message_time = max(all_timestamps) if all_timestamps else 0
    has_very_recent_activity = (
        most_recent_message_time > 0 and
        (now - most_recent_message_time) < VERY_RECENT_ACTIVITY_MS
    )
    
    # Calculate chat age
    chat_age = (now - chat_start) if chat_start > 0 else float("inf")
    is_old_chat = chat_age > MAX_CHAT_AGE_MS
    
    # Check conditions
    if session_start > 0 and chat_start > 0:
        # Chat started after session (always capture)
        if chat_start >= session_start:
            return True, None
        
        # Very recent activity (within 2 hours)
        if has_very_recent_activity:
            return True, None
        
        # Update to recent chat (within 7 days)
        if is_update and not is_old_chat:
            return True, None
        
        # Recent activity (ended within 6 hours) and not old
        if chat_end > 0 and (now - chat_end) < RECENT_ACTIVITY_WINDOW_MS and not is_old_chat:
            return True, None
        
        # Old cached chat (skip)
        if cached_state and is_old_chat:
            return False, f"old cached chat ({int(chat_age / (24 * 60 * 60 * 1000))} days old)"
        
        # Recent cached chat (allow update)
        if cached_state and not is_old_chat:
            return True, None
        
        # Old inactive chat (skip)
        return False, f"old inactive chat ({int(chat_age / (24 * 60 * 60 * 1000))} days old)"
    
    # No session start or invalid chat start
    if session_start == 0:
        logger.warning("Session start time not set, cannot filter old chats")
    if chat_start == 0:
        logger.warning("Chat timestamp is 0, cannot filter")
    
    return True, None  # Default to capturing if we can't determine


def extract_current_exchange(
    full_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Extract only the current exchange (last two user messages and responses between them).
    
    An exchange is: user message 1 → assistant response(s) → user message 2
    
    This ensures we only send the current exchange to LLM, not the entire conversation.
    Previous exchanges should be summarized and stored, then sent as summaries if needed.
    
    Args:
        full_metadata: Full conversation metadata
        
    Returns:
        Metadata containing only the current exchange
    """
    user_prompts = full_metadata.get("userPrompts", [])
    assistant_responses = full_metadata.get("assistantResponses", [])
    reasoning = full_metadata.get("reasoning", [])
    
    if len(user_prompts) < 2:
        # Not enough messages for an exchange - return all (single message)
        return full_metadata
    
    # Get last two user prompts (the exchange boundaries)
    last_two_prompts = user_prompts[-2:]
    exchange_start_timestamp = last_two_prompts[0].get("timestamp", 0)
    exchange_end_timestamp = last_two_prompts[1].get("timestamp", 0)
    
    # Filter to only the last two prompts
    filtered_prompts = last_two_prompts
    
    # Filter responses to only those between the two prompts
    filtered_responses = [
        r for r in assistant_responses
        if exchange_start_timestamp <= (r.get("timestamp", 0) or 0) <= exchange_end_timestamp
    ]
    
    # Filter reasoning to only those in the exchange
    filtered_reasoning = [
        r for r in reasoning
        if exchange_start_timestamp <= (r.get("timestamp", 0) or 0) <= exchange_end_timestamp
    ]
    
    # Recalculate time range
    all_filtered_timestamps = [
        p.get("timestamp", 0) for p in filtered_prompts
    ] + [
        r.get("timestamp", 0) for r in filtered_responses
    ]
    
    conversation_start = (
        min(all_filtered_timestamps)
        if all_filtered_timestamps
        else exchange_start_timestamp
    )
    
    conversation_end = (
        max(all_filtered_timestamps)
        if all_filtered_timestamps
        else exchange_end_timestamp
    )
    
    # Create filtered metadata
    filtered_metadata = {
        **full_metadata,
        "userPrompts": filtered_prompts,
        "assistantResponses": filtered_responses,
        "reasoning": filtered_reasoning,
        "conversationStart": conversation_start,
        "conversationEnd": conversation_end,
    }
    
    logger.debug(
        f"Extracted current exchange: {len(filtered_prompts)} prompts + "
        f"{len(filtered_responses)} responses "
        f"(from {len(user_prompts)} total prompts, "
        f"{len(assistant_responses)} total responses)"
    )
    
    return filtered_metadata


def filter_to_latest_exchange(
    full_metadata: Dict[str, Any],
    last_exchange_timestamp: int,
) -> Dict[str, Any]:
    """
    Filter metadata to only include messages since lastExchangeTimestamp.
    
    Args:
        full_metadata: Complete metadata from database
        last_exchange_timestamp: Timestamp of last captured exchange
        
    Returns:
        Filtered metadata containing only latest exchange
    """
    if last_exchange_timestamp <= 0:
        # First capture - extract only current exchange, not all
        return extract_current_exchange(full_metadata)
    
    # Filter prompts
    filtered_prompts = [
        p for p in full_metadata.get("userPrompts", [])
        if (p.get("timestamp", 0) or 0) > last_exchange_timestamp
    ]
    
    # Filter responses
    filtered_responses = [
        r for r in full_metadata.get("assistantResponses", [])
        if (r.get("timestamp", 0) or 0) > last_exchange_timestamp
    ]
    
    # Filter reasoning
    filtered_reasoning = [
        r for r in full_metadata.get("reasoning", [])
        if (r.get("timestamp", 0) or 0) > last_exchange_timestamp
    ]
    
    # Recalculate time range
    all_filtered_timestamps = [
        p.get("timestamp", 0) for p in filtered_prompts
    ] + [
        r.get("timestamp", 0) for r in filtered_responses
    ]
    
    conversation_start = (
        min(all_filtered_timestamps)
        if all_filtered_timestamps
        else full_metadata.get("conversationStart", 0)
    )
    
    conversation_end = (
        max(all_filtered_timestamps)
        if all_filtered_timestamps
        else full_metadata.get("conversationEnd", 0)
    )
    
    # Create filtered metadata
    filtered_metadata = {
        **full_metadata,
        "userPrompts": filtered_prompts,
        "assistantResponses": filtered_responses,
        "reasoning": filtered_reasoning,
        "conversationStart": conversation_start,
        "conversationEnd": conversation_end,
    }
    
    logger.debug(
        f"Filtered to latest exchange: {len(filtered_prompts)} prompts + "
        f"{len(filtered_responses)} responses "
        f"(from {len(full_metadata.get('userPrompts', []))} total prompts, "
        f"{len(full_metadata.get('assistantResponses', []))} total responses)"
    )
    
    return filtered_metadata


# ============================================================================
# GIT OPERATIONS
# ============================================================================

def get_untracked_files_in_time_range(
    start_time: int,
    end_time: int,
    workspace_root: Optional[str] = None,
) -> List[str]:
    """
    Get untracked files that were created/modified between start_time and end_time.
    
    Args:
        start_time: Start timestamp (ms)
        end_time: End timestamp (ms)
        workspace_root: Optional workspace root (will detect if not provided)
        
    Returns:
        List of untracked file paths (relative to workspace root)
    """
    try:
        if workspace_root is None:
            workspace_root = str(Path.cwd())
        
        # Get untracked files from git status
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode != 0:
            logger.warning(f"Git status failed: {result.stderr}")
            return []
        
        untracked_files = []
        for line in result.stdout.split("\n"):
            line = line.strip()
            # Git status format: "?? path/to/file" for untracked files
            if line.startswith("??"):
                file_path = line[3:].strip()  # Remove "?? " prefix
                if file_path:
                    # Check file modification time
                    full_path = Path(workspace_root) / file_path
                    if full_path.exists():
                        try:
                            file_mtime = full_path.stat().st_mtime * 1000  # Convert to ms
                            # Include if file was created/modified during exchange
                            if start_time <= file_mtime <= end_time:
                                untracked_files.append(file_path)
                        except Exception:
                            # If we can't get mtime, include it anyway (better to include than miss)
                            untracked_files.append(file_path)
        
        return untracked_files
    except Exception as e:
        logger.error(f"Error getting untracked files: {e}", exc_info=True)
        return []


def get_files_modified_in_time_range(
    start_time: int,
    end_time: int,
    workspace_root: Optional[str] = None,
) -> List[str]:
    """
    Get files modified in git between start_time and end_time.
    
    Args:
        start_time: Start timestamp (ms)
        end_time: End timestamp (ms)
        workspace_root: Optional workspace root (will detect if not provided)
        
    Returns:
        List of modified file paths (relative to workspace root)
    """
    try:
        if workspace_root is None:
            # Try to detect workspace root (current directory or parent)
            workspace_root = str(Path.cwd())
        
        # Add buffer time
        from datetime import datetime
        start_date = datetime.fromtimestamp((start_time - 60000) / 1000).isoformat()
        end_date = datetime.fromtimestamp((end_time + 300000) / 1000).isoformat()
        
        # Get commits with file changes
        git_cmd = [
            "git", "log",
            f"--after={start_date}",
            f"--before={end_date}",
            "--format=%H",
            "--name-only",
            "--all"
        ]
        
        result = subprocess.run(
            git_cmd,
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode != 0:
            logger.warning(f"Git log failed: {result.stderr}")
            return []
        
        # Parse output - extract unique file paths
        # git log --name-only returns file paths, one per line (may have empty lines)
        # Format: commit hash, then file paths (one per line), then empty line, then next commit
        files_set = set()
        for line in result.stdout.split("\n"):
            line = line.strip()
            # Skip empty lines, commit hashes (40-char hex), and format markers
            if (line and 
                not line.startswith("commit") and 
                len(line) != 40 and  # Skip commit hashes (40 chars)
                not line.startswith("|") and
                not line.startswith("Date:") and
                not line.startswith("Author:")):
                # Valid file path (may or may not have "/")
                files_set.add(line)
        
        return list(files_set)
    except Exception as e:
        logger.error(f"Error getting modified files: {e}", exc_info=True)
        return []


def get_file_diff(
    file_path: str,
    workspace_root: str,
    start_commit: Optional[str] = None,
    end_commit: Optional[str] = None,
) -> Optional[str]:
    """
    Get git diff for a specific file.
    
    Args:
        file_path: Relative file path
        workspace_root: Workspace root directory
        start_commit: Optional start commit (for range diff)
        end_commit: Optional end commit (for range diff)
        
    Returns:
        Diff string or None if error
    """
    try:
        # Check if file is tracked
        check_cmd = ["git", "ls-files", "--error-unmatch", file_path]
        check_result = subprocess.run(
            check_cmd,
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False
        )
        
        is_tracked = check_result.returncode == 0
        
        if is_tracked:
            # Tracked file: get diff
            if start_commit and end_commit:
                # Range diff
                diff_cmd = ["git", "diff", f"{start_commit}..{end_commit}", "--", file_path]
            else:
                # HEAD diff
                diff_cmd = ["git", "diff", "HEAD", "--", file_path]
            
            diff_result = subprocess.run(
                diff_cmd,
                cwd=workspace_root,
                capture_output=True,
                text=True,
                check=False
            )
            
            if diff_result.returncode == 0 and diff_result.stdout.strip():
                return diff_result.stdout
            
            # Try staged diff
            staged_cmd = ["git", "diff", "--cached", "--", file_path]
            staged_result = subprocess.run(
                staged_cmd,
                cwd=workspace_root,
                capture_output=True,
                text=True,
                check=False
            )
            
            if staged_result.returncode == 0 and staged_result.stdout.strip():
                return staged_result.stdout
            
            return None
        else:
            # Untracked file: show as new file
            absolute_path = Path(workspace_root) / file_path
            if not absolute_path.exists():
                return None
            
            content = absolute_path.read_text(encoding="utf-8", errors="ignore")
            if not content.strip():
                return None
            
            # Format as "new file" diff
            lines = content.split("\n")
            diff_lines = [
                f"diff --git a/{file_path} b/{file_path}",
                "new file mode 100644",
                "--- /dev/null",
                f"+++ b/{file_path}",
                f"@@ -0,0 +1,{len(lines)} @@",
            ] + [f"+{line}" for line in lines]
            
            return "\n".join(diff_lines)
    except Exception as e:
        logger.error(f"Error getting file diff for {file_path}: {e}")
        return None


def get_file_diffs_for_conversation(
    files: List[Dict[str, Any]],
    conversation_start: int,
    conversation_end: int,
    workspace_root: Optional[str] = None,
) -> Dict[str, str]:
    """
    Get diffs for files mentioned in conversation.
    
    Uses cumulative diff approach: single diff per file from start → end.
    
    Args:
        files: List of {path, mentionedAt} dicts
        conversation_start: Start timestamp (ms)
        conversation_end: End timestamp (ms)
        workspace_root: Optional workspace root
        
    Returns:
        Dict mapping file paths to their diffs
    """
    file_diffs = {}
    
    try:
        if workspace_root is None:
            workspace_root = str(Path.cwd())
        
        # Find commits at conversation boundaries
        from datetime import datetime
        start_date = datetime.fromtimestamp((conversation_start - 60000) / 1000).isoformat()
        end_date = datetime.fromtimestamp((conversation_end + 300000) / 1000).isoformat()
        
        # Get commit at start (or HEAD if no commits before)
        start_commit_cmd = [
            "git", "log",
            f"--before={start_date}",
            "--format=%H",
            "-1"
        ]
        start_result = subprocess.run(
            start_commit_cmd,
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False
        )
        start_commit = start_result.stdout.strip() if start_result.returncode == 0 else None
        
        # Get commit at end (or HEAD)
        end_commit_cmd = [
            "git", "log",
            f"--before={end_date}",
            "--format=%H",
            "-1"
        ]
        end_result = subprocess.run(
            end_commit_cmd,
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False
        )
        end_commit = end_result.stdout.strip() if end_result.returncode == 0 else "HEAD"
        
        # Get diff for each file
        for file_info in files:
            file_path = file_info.get("path", "")
            if not file_path:
                continue
            
            diff = get_file_diff(file_path, workspace_root, start_commit, end_commit)
            if diff:
                file_diffs[file_path] = diff
        
        logger.info(f"Extracted diffs for {len(file_diffs)} files")
        return file_diffs
    except Exception as e:
        logger.error(f"Error getting file diffs: {e}", exc_info=True)
        return file_diffs


def find_related_commits(
    start_time: int,
    end_time: int,
    workspace_root: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Find git commits made during conversation timeframe.
    
    Args:
        start_time: Start timestamp (ms)
        end_time: End timestamp (ms)
        workspace_root: Optional workspace root
        
    Returns:
        List of {sha, message, timestamp} dicts
    """
    commits = []
    
    try:
        if workspace_root is None:
            workspace_root = str(Path.cwd())
        
        from datetime import datetime
        start_date = datetime.fromtimestamp((start_time - 60000) / 1000).isoformat()
        end_date = datetime.fromtimestamp((end_time + 300000) / 1000).isoformat()
        
        git_cmd = [
            "git", "log",
            f"--after={start_date}",
            f"--before={end_date}",
            '--format=%H|%s|%at',
            "--all"
        ]
        
        result = subprocess.run(
            git_cmd,
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode != 0:
            logger.warning(f"Git log failed: {result.stderr}")
            return commits
        
        # Parse output
        for line in result.stdout.split("\n"):
            line = line.strip()
            if not line:
                continue
            
            parts = line.split("|")
            if len(parts) >= 3:
                commits.append({
                    "sha": parts[0][:8],  # Short SHA
                    "message": parts[1],
                    "timestamp": int(parts[2]) * 1000  # Convert to ms
                })
        
        logger.debug(f"Found {len(commits)} related commits")
        return commits
    except Exception as e:
        logger.error(f"Error finding commits: {e}", exc_info=True)
        return commits


# ============================================================================
# BUSINESS LOGIC
# ============================================================================

def build_summary(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build summary object from metadata.
    
    Args:
        metadata: Extracted metadata from Cursor database
        
    Returns:
        Summary dict with all fields
    """
    summary: Dict[str, Any] = {
        "initialIntent": metadata.get("smartIntent") or (
            metadata.get("userPrompts", [{}])[0].get("text", "Unknown")
            if metadata.get("userPrompts") else "Unknown"
        )
    }
    
    # Final intent (last response)
    assistant_responses = metadata.get("assistantResponses", [])
    if assistant_responses:
        last_response = assistant_responses[-1]
        summary["finalIntent"] = last_response.get("text", "")
    
    # Files discussed
    files_discussed = metadata.get("filesDiscussed", [])
    if files_discussed:
        summary["filesDiscussed"] = [f.get("path", "") for f in files_discussed]
    
    # Related commits
    related_commits = metadata.get("relatedCommits", [])
    if related_commits:
        summary["relatedCommits"] = [
            {
                "sha": c.get("sha", ""),
                "message": c.get("message", "")
            }
            for c in related_commits
        ]
    
    # Token usage
    token_usage = metadata.get("tokenUsage", {})
    if token_usage:
        summary["tokenUsage"] = {
            "input": token_usage.get("totalInput", 0),
            "output": token_usage.get("totalOutput", 0),
            "total": token_usage.get("totalInput", 0) + token_usage.get("totalOutput", 0)
        }
    
    # Time range
    conversation_start = metadata.get("conversationStart", 0)
    conversation_end = metadata.get("conversationEnd", 0)
    if conversation_start and conversation_end:
        summary["timeRange"] = {
            "start": conversation_start,
            "end": conversation_end,
            "durationMs": conversation_end - conversation_start
        }
    
    return summary


def merge_llm_enhancement_results(
    existing_summary: Dict[str, Any],
    new_enhancement: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Merge new LLM enhancement with existing summary.
    
    Args:
        existing_summary: Existing summary from previous captures
        new_enhancement: New enhancement from latest exchange
        
    Returns:
        Merged summary
    """
    merged = existing_summary.copy()
    
    # Merge action items (update existing instead of duplicating)
    existing_action_items = existing_summary.get("llmActionItems", [])
    new_action_items = new_enhancement.get("actionItems", [])
    
    # Create a map of existing items by normalized task text
    existing_items_map = {}
    for item in existing_action_items:
        task_text = item.get("task", "").lower().strip()
        if task_text:
            existing_items_map[task_text] = item
    
    # Update or add new items
    for new_item in new_action_items:
        task_text = new_item.get("task", "").lower().strip()
        if task_text:
            if task_text in existing_items_map:
                # Update existing item (prefer new status/priority if provided)
                existing_item = existing_items_map[task_text]
                # Update fields from new item, but preserve existing if new doesn't have it
                existing_item.update({
                    k: v for k, v in new_item.items() 
                    if v is not None and v != "" and k != "task"  # Don't overwrite task text
                })
                # If new item has status, use it (allows marking as done)
                if new_item.get("status"):
                    existing_item["status"] = new_item["status"]
            else:
                # New item, add it
                existing_items_map[task_text] = new_item.copy()
    
    merged["llmActionItems"] = list(existing_items_map.values())
    
    # Merge key decisions (deduplicate)
    existing_decisions = existing_summary.get("llmKeyDecisions", [])
    new_decisions = new_enhancement.get("keyDecisions", [])
    merged["llmKeyDecisions"] = list(set(existing_decisions + new_decisions))
    
    # Update other fields
    merged["llmThreadName"] = new_enhancement.get("threadName") or existing_summary.get("llmThreadName")
    merged["llmSummary"] = new_enhancement.get("summary")  # Use new summary (should include previous context)
    merged["llmCodeChanges"] = new_enhancement.get("codeChangesSummary") or existing_summary.get("llmCodeChanges")
    merged["llmIntentEvolution"] = new_enhancement.get("intentEvolution") or existing_summary.get("llmIntentEvolution")
    
    # NEW: Merge new structured fields
    # For kanbanItems, use new if provided, otherwise keep existing
    if new_enhancement.get("kanbanItems"):
        # Merge kanban items (append new to existing)
        existing_kanban = existing_summary.get("llmKanbanItems", {})
        new_kanban = new_enhancement.get("kanbanItems", {})
        merged["llmKanbanItems"] = {
            "features": (existing_kanban.get("features", []) or []) + (new_kanban.get("features", []) or []),
            "specs": (existing_kanban.get("specs", []) or []) + (new_kanban.get("specs", []) or []),
            "todos": (existing_kanban.get("todos", []) or []) + (new_kanban.get("todos", []) or [])
        }
    else:
        merged["llmKanbanItems"] = existing_summary.get("llmKanbanItems")
    
    # For decisions, merge (deduplicate by decision text)
    existing_decisions = existing_summary.get("llmDecisions", [])
    new_decisions = new_enhancement.get("decisions", [])
    if new_decisions:
        # Merge decisions, deduplicate by decision text
        decision_texts = {d.get("decision", "").lower() for d in existing_decisions if isinstance(d, dict)}
        merged_decisions = list(existing_decisions)
        for new_decision in new_decisions:
            if isinstance(new_decision, dict):
                decision_text = new_decision.get("decision", "").lower()
                if decision_text and decision_text not in decision_texts:
                    merged_decisions.append(new_decision)
                    decision_texts.add(decision_text)
            else:
                merged_decisions.append(new_decision)
        merged["llmDecisions"] = merged_decisions
    else:
        merged["llmDecisions"] = existing_decisions
    
    # For incompleteWork, merge (deduplicate by item text)
    existing_incomplete = existing_summary.get("llmIncompleteWork", [])
    new_incomplete = new_enhancement.get("incompleteWork", [])
    if new_incomplete:
        incomplete_texts = {item.get("item", "").lower() for item in existing_incomplete if isinstance(item, dict)}
        merged_incomplete = list(existing_incomplete)
        for new_item in new_incomplete:
            if isinstance(new_item, dict):
                item_text = new_item.get("item", "").lower()
                if item_text and item_text not in incomplete_texts:
                    merged_incomplete.append(new_item)
                    incomplete_texts.add(item_text)
            else:
                merged_incomplete.append(new_item)
        merged["llmIncompleteWork"] = merged_incomplete
    else:
        merged["llmIncompleteWork"] = existing_incomplete
    
    # For followUps, merge (deduplicate by action text)
    existing_followups = existing_summary.get("llmFollowUps", [])
    new_followups = new_enhancement.get("followUps", [])
    if new_followups:
        followup_texts = {f.get("action", "").lower() for f in existing_followups if isinstance(f, dict)}
        merged_followups = list(existing_followups)
        for new_followup in new_followups:
            if isinstance(new_followup, dict):
                action_text = new_followup.get("action", "").lower()
                if action_text and action_text not in followup_texts:
                    merged_followups.append(new_followup)
                    followup_texts.add(action_text)
            else:
                merged_followups.append(new_followup)
        merged["llmFollowUps"] = merged_followups
    else:
        merged["llmFollowUps"] = existing_followups
    
    # For contextForFuture, merge (combine arrays)
    existing_context = existing_summary.get("llmContextForFuture", {})
    new_context = new_enhancement.get("contextForFuture", {})
    if new_context:
        merged["llmContextForFuture"] = {
            "whatWorked": list(set((existing_context.get("whatWorked", []) or []) + (new_context.get("whatWorked", []) or []))),
            "whatDidntWork": list(set((existing_context.get("whatDidntWork", []) or []) + (new_context.get("whatDidntWork", []) or []))),
            "relatedFiles": list(set((existing_context.get("relatedFiles", []) or []) + (new_context.get("relatedFiles", []) or []))),
            "patternsEstablished": list(set((existing_context.get("patternsEstablished", []) or []) + (new_context.get("patternsEstablished", []) or [])))
        }
    else:
        merged["llmContextForFuture"] = existing_context
    
    return merged


def preserve_llm_fields_when_skipping(
    summary: Dict[str, Any],
    existing_summary: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Preserve existing LLM fields when enhancement is skipped.
    
    Args:
        summary: New summary (without LLM fields)
        existing_summary: Existing summary with LLM fields
        
    Returns:
        Summary with preserved LLM fields
    """
    if not existing_summary:
        return summary
    
    # Preserve all LLM fields
    summary["llmThreadName"] = existing_summary.get("llmThreadName")
    summary["llmSummary"] = existing_summary.get("llmSummary")
    summary["llmKeyDecisions"] = existing_summary.get("llmKeyDecisions")
    summary["llmActionItems"] = existing_summary.get("llmActionItems")
    summary["llmCodeChanges"] = existing_summary.get("llmCodeChanges")
    summary["llmIntentEvolution"] = existing_summary.get("llmIntentEvolution")
    # NEW: Preserve new structured fields
    summary["llmKanbanItems"] = existing_summary.get("llmKanbanItems")
    summary["llmDecisions"] = existing_summary.get("llmDecisions")
    summary["llmIncompleteWork"] = existing_summary.get("llmIncompleteWork")
    summary["llmFollowUps"] = existing_summary.get("llmFollowUps")
    summary["llmContextForFuture"] = existing_summary.get("llmContextForFuture")
    
    return summary


def merge_metadata_cumulative(
    new_metadata: Dict[str, Any],
    existing_metadata: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Merge new metadata with existing metadata cumulatively.
    
    This ensures we don't lose previous messages when updating a plan.
    New messages are appended to existing ones, deduplicated by timestamp+sequence.
    
    Args:
        new_metadata: New filtered metadata (may be partial/empty)
        existing_metadata: Existing stored metadata (may be None)
        
    Returns:
        Merged metadata with all messages
    """
    if not existing_metadata:
        return new_metadata
    
    def dedupe_by_key(items: List[Dict], key_fn) -> List[Dict]:
        """Deduplicate list of dicts by a key function."""
        seen = set()
        result = []
        for item in items:
            key = key_fn(item)
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result
    
    def message_key(msg: Dict) -> str:
        """Create unique key for a message based on timestamp and sequence."""
        ts = msg.get("timestamp", 0)
        seq = msg.get("sequence", 0)
        # Also include first 50 chars of text to handle same-timestamp messages
        text = (msg.get("text", "") or "")[:50]
        return f"{ts}:{seq}:{text}"
    
    # Merge userPrompts
    existing_prompts = existing_metadata.get("userPrompts", []) or []
    new_prompts = new_metadata.get("userPrompts", []) or []
    merged_prompts = dedupe_by_key(existing_prompts + new_prompts, message_key)
    # Sort by timestamp
    merged_prompts.sort(key=lambda x: (x.get("timestamp", 0), x.get("sequence", 0)))
    
    # Merge assistantResponses
    existing_responses = existing_metadata.get("assistantResponses", []) or []
    new_responses = new_metadata.get("assistantResponses", []) or []
    merged_responses = dedupe_by_key(existing_responses + new_responses, message_key)
    merged_responses.sort(key=lambda x: (x.get("timestamp", 0), x.get("sequence", 0)))
    
    # Merge reasoning
    existing_reasoning = existing_metadata.get("reasoning", []) or []
    new_reasoning = new_metadata.get("reasoning", []) or []
    merged_reasoning = dedupe_by_key(existing_reasoning + new_reasoning, message_key)
    merged_reasoning.sort(key=lambda x: (x.get("timestamp", 0), x.get("sequence", 0)))
    
    # Merge timestamps (unique)
    existing_timestamps = set(existing_metadata.get("timestamps", []) or [])
    new_timestamps = set(new_metadata.get("timestamps", []) or [])
    merged_timestamps = sorted(existing_timestamps.union(new_timestamps))
    
    # Merge intentEvolution
    existing_intents = existing_metadata.get("intentEvolution", []) or []
    new_intents = new_metadata.get("intentEvolution", []) or []
    
    def intent_key(intent: Dict) -> str:
        return f"{intent.get('timestamp', 0)}:{intent.get('stage', 0)}"
    
    merged_intents = dedupe_by_key(existing_intents + new_intents, intent_key)
    merged_intents.sort(key=lambda x: (x.get("timestamp", 0), x.get("stage", 0)))
    
    # Merge insightsTimeline
    existing_insights = existing_metadata.get("insightsTimeline", []) or []
    new_insights = new_metadata.get("insightsTimeline", []) or []
    
    def insight_key(insight: Dict) -> str:
        return str(insight.get("exchangeSequence", 0))
    
    merged_insights = dedupe_by_key(existing_insights + new_insights, insight_key)
    # Sort by timestamp descending (most recent first)
    merged_insights.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    
    logger.debug(
        f"Merged metadata: {len(existing_prompts)} + {len(new_prompts)} → {len(merged_prompts)} prompts, "
        f"{len(existing_responses)} + {len(new_responses)} → {len(merged_responses)} responses, "
        f"{len(existing_insights)} + {len(new_insights)} → {len(merged_insights)} insights"
    )
    
    result = {
        "userPrompts": merged_prompts,
        "assistantResponses": merged_responses,
        "reasoning": merged_reasoning,
        "timestamps": merged_timestamps,
        "intentEvolution": merged_intents,
    }
    
    # Only include insightsTimeline if we have insights
    if merged_insights:
        result["insightsTimeline"] = merged_insights
    
    return result


def extract_insight_from_enhancement(
    enhancement: Dict[str, Any],
    metadata: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Extract a per-exchange insight from the full LLM enhancement result.
    
    Args:
        enhancement: Full LLM enhancement result
        metadata: Exchange metadata with userPrompts and assistantResponses
        
    Returns:
        Dict with {timestamp, type, icon, text, exchangeSequence} or None if failed
    """
    try:
        user_prompts = metadata.get("userPrompts", [])
        if not user_prompts:
            return None
        
        # Get the latest exchange for timestamp/sequence
        latest_prompt = user_prompts[-1]
        timestamp = latest_prompt.get("timestamp", int(time.time() * 1000))
        sequence = latest_prompt.get("sequence", 0)
        
        # Extract insight from enhancement result
        # Prefer keyDecisions (most actionable), then summary, then actionItems
        key_decisions = enhancement.get("keyDecisions", [])
        summary = enhancement.get("summary", "")
        action_items = enhancement.get("actionItems", [])
        code_changes = enhancement.get("codeChangesSummary", "")
        
        insight_text = None
        insight_type = "discussion"
        
        if key_decisions and len(key_decisions) > 0:
            # Use the first key decision (most recent/relevant)
            insight_text = key_decisions[0]
            insight_type = "decision"
        elif action_items and len(action_items) > 0:
            # Use first action item
            first_item = action_items[0]
            insight_text = first_item.get("task", "") if isinstance(first_item, dict) else str(first_item)
            insight_type = "decision"
        elif code_changes:
            insight_text = code_changes[:80]
            insight_type = "code"
        elif summary:
            # Use first sentence of summary
            insight_text = summary.split(".")[0].strip()
            if not insight_text:
                insight_text = summary[:80]
            insight_type = "discussion"
        
        if not insight_text:
            return None
        
        # Determine icon based on type
        icon_map = {
            "decision": "🎯",
            "code": "💻",
            "intent": "🔄",
            "discussion": "💬"
        }
        
        return {
            "timestamp": timestamp,
            "type": insight_type,
            "icon": icon_map.get(insight_type, "💬"),
            "text": insight_text[:80],  # Ensure max 80 chars
            "exchangeSequence": sequence
        }
    except Exception as e:
        logger.warning(f"Error extracting insight from enhancement: {e}", exc_info=True)
        return None


async def generate_exchange_insight(
    metadata: Dict[str, Any],
    handle_tool_call_fn: Callable[[str, Dict[str, Any]], Awaitable[Any]]
) -> Optional[Dict[str, Any]]:
    """
    Generate a lightweight one-line insight for the latest exchange.
    Only used as fallback when full LLM enhancement is NOT running.
    
    Args:
        metadata: Exchange metadata with userPrompts and assistantResponses
        handle_tool_call_fn: Function to call MCP tools
        
    Returns:
        Dict with {timestamp, type, icon, text, exchangeSequence} or None if failed
    """
    try:
        user_prompts = metadata.get("userPrompts", [])
        assistant_responses = metadata.get("assistantResponses", [])
        
        if not user_prompts or not assistant_responses:
            return None
        
        # Get the latest exchange
        latest_prompt = user_prompts[-1]
        latest_response = assistant_responses[-1]
        
        prompt_text = latest_prompt.get("text", "").strip()
        response_text = latest_response.get("text", "").strip()
        
        if not prompt_text or not response_text:
            return None
        
        # Simple heuristic-based insight (no LLM call)
        # Extract key action words or decisions from the exchange
        prompt_lower = prompt_text.lower()
        response_lower = response_text.lower()
        
        insight_text = None
        insight_type = "discussion"
        
        # Detect decision-making
        if any(word in prompt_lower for word in ["decide", "choose", "should", "remove", "keep", "add"]):
            insight_text = prompt_text[:80]
            insight_type = "decision"
        # Detect code changes
        elif any(word in response_lower for word in ["modified", "created", "deleted", "updated", "changed"]):
            insight_text = response_text[:80]
            insight_type = "code"
        # Default: use prompt as insight
        else:
            insight_text = prompt_text[:80]
            insight_type = "discussion"
        
        # Determine icon
        icon_map = {
            "decision": "🎯",
            "code": "💻",
            "intent": "🔄",
            "discussion": "💬"
        }
        
        timestamp = latest_prompt.get("timestamp", int(time.time() * 1000))
        sequence = latest_prompt.get("sequence", 0)
        
        return {
            "timestamp": timestamp,
            "type": insight_type,
            "icon": icon_map.get(insight_type, "💬"),
            "text": insight_text[:80],
            "exchangeSequence": sequence
        }
    except Exception as e:
        logger.warning(f"Error generating exchange insight: {e}", exc_info=True)
        return None


async def capture_exchange(
    db: MemoryDatabase,
    composer_id: str,
    exchange_state: Dict[str, Any],
    should_enhance_llm: bool,
    is_update: bool,
    repo: str,
    handle_tool_call_fn: Callable[[str, Dict[str, Any]], Awaitable[Any]],
    session_start_time: Optional[int] = None,
    workspace_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Complete capture flow for an exchange.
    
    This function:
    1. Extracts metadata from Cursor database
    2. Filters to latest exchange only
    3. Checks if should capture (old chat filter, short chat filter)
    4. Extracts file diffs
    5. Builds summary
    6. Enhances with LLM (if needed)
    7. Stores analysis
    8. Updates state
    
    Args:
        db: MemoryDatabase instance
        composer_id: Composer ID to capture
        exchange_state: Exchange state from detection
        should_enhance_llm: Whether to enhance with LLM
        is_update: Whether this is an update to existing chat
        repo: Repository identifier
        handle_tool_call_fn: Function to call MCP tools
        session_start_time: Optional session start timestamp
        workspace_root: Optional workspace root for git operations
        
    Returns:
        Dict with status and details
    """
    try:
        from .cursor_database_reader import extract_metadata, find_cursor_database
        
        # Find database
        db_path = find_cursor_database(composer_id)
        if db_path is None:
            return {"status": "error", "error": "Database not found"}
        
        db_path_str = str(db_path)
        
        # Get cached state
        cached_state = db.get_chat_capture_state(composer_id)
        last_exchange_timestamp = cached_state.get("lastExchangeTimestamp", 0) if cached_state else 0
        
        # Extract full metadata
        full_metadata = extract_metadata(composer_id, db_path_str)
        if not full_metadata or not full_metadata.get("userPrompts"):
            return {"status": "skipped", "reason": "no content"}
        
        # Filter to latest exchange
        metadata = filter_to_latest_exchange(full_metadata, last_exchange_timestamp)
        
        # Check if should capture (old chat filter, short chat filter)
        should_capture, skip_reason = should_capture_chat(
            metadata,
            cached_state,
            session_start_time,
            is_update
        )
        
        if not should_capture:
            # Update state to prevent re-checking
            db.update_chat_capture_state(
                composer_id,
                last_captured_at=int(time.time() * 1000)
            )
            return {"status": "skipped", "reason": skip_reason}
        
        # REMOVED: Short chat filter - no longer skipping small exchanges
        # All exchanges will be captured regardless of size
        
        # Get existing plan (for both first capture and updates)
        # We fetch both summary (for LLM context) and metadata (for cumulative merge)
        existing_summary = None
        existing_metadata = None
        try:
            existing_plans = await handle_tool_call_fn("turingmind_get_chat_analysis_plans", {
                "repo": repo,
                "composer_id": composer_id,
                "limit": 1
            })
            if existing_plans and existing_plans.get("plans"):
                existing_plan = existing_plans["plans"][0]
                existing_summary = existing_plan.get("summary")
                existing_metadata = existing_plan.get("metadata")
                
                # BACKFILL CHECK: If existing metadata is empty but full_metadata has data,
                # use full_metadata instead of filtered. This handles the case where
                # a plan was created but metadata wasn't properly captured.
                existing_prompt_count = len((existing_metadata or {}).get("userPrompts", []))
                full_prompt_count = len(full_metadata.get("userPrompts", []))
                
                if existing_prompt_count == 0 and full_prompt_count > 0:
                    logger.info(
                        f"🔄 BACKFILL: Existing metadata is empty but Cursor has {full_prompt_count} prompts. "
                        f"Using full metadata instead of filtered exchange."
                    )
                    # Use full metadata for this capture to backfill
                    metadata = full_metadata
        except Exception as e:
            logger.warning(f"Could not fetch existing plan: {e}")
        
        # Build summary
        summary = build_summary(metadata)
        
        # Extract file diffs
        file_diffs_list: List[Dict[str, Any]] = []
        newly_processed_files = set()
        
        conversation_start = metadata.get("conversationStart", 0)
        conversation_end = metadata.get("conversationEnd", 0)
        
        if conversation_start and conversation_end:
            try:
                # Get previously processed files
                previously_processed = cached_state.get("processedFiles", set()) if cached_state else set()
                
                # Get files mentioned
                mentioned_files = metadata.get("filesDiscussed", [])
                
                # Get files modified via git (tracked files)
                modified_files = get_files_modified_in_time_range(
                    conversation_start,
                    conversation_end,
                    workspace_root
                )
                
                # Get untracked files created during exchange
                untracked_files = get_untracked_files_in_time_range(
                    conversation_start,
                    conversation_end,
                    workspace_root
                )
                
                # Filter to newly modified files
                newly_modified = [f for f in modified_files if f not in previously_processed]
                newly_untracked = [f for f in untracked_files if f not in previously_processed]
                
                # Combine mentioned, newly modified, and untracked files
                all_files_to_diff = set()
                for file_info in mentioned_files:
                    all_files_to_diff.add(file_info.get("path", ""))
                for file_path in newly_modified:
                    all_files_to_diff.add(file_path)
                for file_path in newly_untracked:
                    all_files_to_diff.add(file_path)
                
                # Limit to MAX_FILES_TO_PROCESS
                files_to_diff = list(all_files_to_diff)[:MAX_FILES_TO_PROCESS]
                
                if files_to_diff:
                    # Get diffs
                    files_with_mentioned_at = [
                        {"path": f, "mentionedAt": conversation_end}
                        for f in files_to_diff
                    ]
                    diffs_map = get_file_diffs_for_conversation(
                        files_with_mentioned_at,
                        conversation_start,
                        conversation_end,
                        workspace_root
                    )
                    
                    # Convert to list format
                    for file_path, diff_content in diffs_map.items():
                        file_diffs_list.append({
                            "path": file_path,
                            "diff": diff_content,
                            "size": len(diff_content)
                        })
                        newly_processed_files.add(file_path)
                    
                    logger.info(
                        f"Extracted {len(file_diffs_list)} file diffs "
                        f"({sum(d['size'] for d in file_diffs_list) / 1024:.1f}KB total)"
                    )
            except Exception as e:
                logger.error(f"Error extracting file diffs: {e}", exc_info=True)
        
        # Enhance with LLM if needed
        llm_enhanced = False
        if should_enhance_llm:
            try:
                # Prepare LLM input
                prompts_to_process = metadata.get("userPrompts", [])
                responses_to_process = metadata.get("assistantResponses", [])
                reasoning_to_process = metadata.get("reasoning", [])
                
                # Log what we're sending to LLM
                logger.info(f"Preparing LLM enhancement: {len(prompts_to_process)} prompts, {len(responses_to_process)} responses, {len(reasoning_to_process)} reasoning blocks")
                
                # Check if we have content to analyze
                if not prompts_to_process and not responses_to_process:
                    logger.warning(f"⚠️ LLM enhancement skipped: No prompts or responses in metadata!")
                    logger.warning(f"   Metadata keys: {list(metadata.keys())}")
                    logger.warning(f"   userPrompts type: {type(metadata.get('userPrompts'))}, length: {len(metadata.get('userPrompts', []))}")
                    logger.warning(f"   assistantResponses type: {type(metadata.get('assistantResponses'))}, length: {len(metadata.get('assistantResponses', []))}")
                
                # Get previous summary for context (always include if available)
                # This provides context about previous exchanges without sending all the raw data
                previous_summary = None
                if existing_summary and (
                    existing_summary.get("llmSummary") or
                    existing_summary.get("llmKeyDecisions") or
                    existing_summary.get("llmActionItems")
                ):
                    previous_summary = {
                        "summary": existing_summary.get("llmSummary"),
                        "keyDecisions": existing_summary.get("llmKeyDecisions"),
                        "actionItems": existing_summary.get("llmActionItems", []),
                        "threadName": existing_summary.get("llmThreadName")
                    }
                
                # NEW: Get rolling context from recent sessions
                rolling_context = []
                try:
                    rolling_context = db.get_rolling_context(
                        repo=repo,
                        current_composer_id=composer_id,
                        window_hours=48,
                        max_sessions=5
                    )
                    if rolling_context:
                        logger.info(f"Including {len(rolling_context)} sessions in rolling context")
                except Exception as e:
                    logger.warning(f"Failed to get rolling context: {e}")
                
                # NEW: Get active tasks for phase tracking
                active_tasks = []
                try:
                    active_tasks = db.get_active_tasks(
                        repo=repo,
                        exclude_phases=["done", "abandoned"],
                        limit=20
                    )
                    if active_tasks:
                        logger.info(f"Including {len(active_tasks)} active tasks for tracking")
                except Exception as e:
                    logger.warning(f"Failed to get active tasks: {e}")
                
                # NEW: Get hierarchical context (repo → folder → file)
                hierarchical_context = {}
                try:
                    files_discussed_paths = [f.get("path", "") for f in metadata.get("filesDiscussed", [])]
                    hierarchical_context = db.get_hierarchical_context(
                        repo=repo,
                        current_files=files_discussed_paths,
                        window_hours=168  # 7 days
                    )
                    ctx_counts = (
                        (1 if hierarchical_context.get("repo") else 0) +
                        len(hierarchical_context.get("folders", [])) +
                        len(hierarchical_context.get("files", []))
                    )
                    if ctx_counts > 0:
                        logger.info(f"Including hierarchical context: {ctx_counts} scope(s)")
                except Exception as e:
                    logger.warning(f"Failed to get hierarchical context: {e}")
                
                # Call LLM enhancement
                enhancement_result = await handle_tool_call_fn("turingmind_enhance_chat_analysis", {
                    "user_prompts": prompts_to_process,
                    "assistant_responses": responses_to_process,
                    "files_discussed": [f.get("path", "") for f in metadata.get("filesDiscussed", [])],
                    "ai_todos": [
                        {"content": t.get("content", ""), "status": t.get("status", "pending")}
                        for t in metadata.get("aiTodos", [])
                    ],
                    "reasoning": reasoning_to_process,
                    "previous_summary": previous_summary,
                    "file_diffs": file_diffs_list,
                    "rolling_context": rolling_context,
                    "active_tasks": active_tasks,
                    "hierarchical_context": hierarchical_context
                })
                
                # Extract enhancement from result
                enhancement = None
                if enhancement_result and enhancement_result.get("status") == "success":
                    enhancement = enhancement_result.get("result")
                elif enhancement_result and not enhancement_result.get("error"):
                    # Some tools return result directly
                    enhancement = enhancement_result
                else:
                    # Log why LLM enhancement failed
                    if enhancement_result:
                        error_msg = enhancement_result.get("error", "Unknown error")
                        logger.warning(f"LLM enhancement returned error: {error_msg}")
                    else:
                        logger.warning(f"LLM enhancement returned None/empty result")
                
                if enhancement:
                    if existing_summary:
                        # Merge with existing
                        summary = merge_llm_enhancement_results(existing_summary, enhancement)
                    else:
                        # First capture - use as-is
                        summary["llmThreadName"] = enhancement.get("threadName")
                        summary["llmSummary"] = enhancement.get("summary")
                        summary["llmKeyDecisions"] = enhancement.get("keyDecisions", [])
                        summary["llmActionItems"] = enhancement.get("actionItems", [])
                        summary["llmCodeChanges"] = enhancement.get("codeChangesSummary")
                        summary["llmIntentEvolution"] = enhancement.get("intentEvolution")
                        # NEW: Save new structured fields
                        summary["llmKanbanItems"] = enhancement.get("kanbanItems")
                        summary["llmDecisions"] = enhancement.get("decisions")
                        summary["llmIncompleteWork"] = enhancement.get("incompleteWork")
                        summary["llmFollowUps"] = enhancement.get("followUps")
                        summary["llmContextForFuture"] = enhancement.get("contextForFuture")
                    
                    llm_enhanced = True
                    thread_name = enhancement.get('threadName', 'N/A')
                    logger.info(f"LLM enhanced: {thread_name}")
                    
                    # Extract per-exchange insight from enhancement result
                    exchange_insight = extract_insight_from_enhancement(
                        enhancement,
                        metadata
                    )
                    if exchange_insight:
                        # Initialize insightsTimeline if needed
                        if "insightsTimeline" not in metadata:
                            metadata["insightsTimeline"] = []
                        
                        # Get existing timeline from existing_metadata if available
                        existing_timeline = []
                        if existing_metadata and "insightsTimeline" in existing_metadata:
                            existing_timeline = existing_metadata["insightsTimeline"]
                        
                        # Merge: add new insight, avoid duplicates by exchangeSequence
                        existing_sequences = {insight.get("exchangeSequence") for insight in existing_timeline}
                        if exchange_insight.get("exchangeSequence") not in existing_sequences:
                            existing_timeline.append(exchange_insight)
                            # Sort by timestamp descending (most recent first)
                            existing_timeline.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
                            metadata["insightsTimeline"] = existing_timeline
                            logger.info(f"Added exchange insight from enhancement: {exchange_insight.get('text', '')[:50]}...")
                    
                    # NEW: Process task lifecycle updates
                    try:
                        # Process task updates (phase transitions)
                        task_updates = enhancement.get("taskUpdates", [])
                        for update in task_updates:
                            task_id = update.get("taskId", "")
                            transition = update.get("transition", {})
                            to_phase = transition.get("to", "")
                            evidence = update.get("evidence", "")
                            confidence = update.get("confidence", 0.5)
                            
                            if task_id and to_phase and confidence >= 0.5:
                                result = db.apply_task_transition(
                                    task_id=task_id,
                                    to_phase=to_phase,
                                    evidence=evidence,
                                    session_id=composer_id
                                )
                                if result.get("status") == "transitioned":
                                    logger.info(f"Task {task_id}: {result.get('from_phase')} → {to_phase}")
                        
                        # Process new tasks - create as PENDING (requires user approval)
                        new_tasks = enhancement.get("newTasks", [])
                        pending_count = 0
                        for task in new_tasks:
                            description = task.get("description", "")
                            confidence = task.get("confidence", 0.5)
                            
                            if description and confidence >= 0.5:
                                # Check for similar existing tasks (deduplication)
                                similar = db.find_similar_tasks(repo, description, threshold=0.7)
                                if similar:
                                    logger.info(f"Skipping new task '{description[:50]}...' - similar to existing: {similar[0].get('id')}")
                                    continue
                                
                                # Create as PENDING task (opt-in)
                                result = db.create_pending_task(
                                    repo=repo,
                                    description=description,
                                    suggested_phase=task.get("initialPhase", "mentioned"),
                                    related_files=task.get("relatedFiles", []),
                                    source_session_id=composer_id,
                                    priority=task.get("priority", "medium"),
                                    confidence=confidence
                                )
                                if result.get("id"):
                                    pending_count += 1
                                    logger.info(f"Created pending task: {result.get('id')} - {description[:50]}...")
                        
                        if pending_count > 0:
                            logger.info(f"Created {pending_count} pending task(s) - awaiting user approval")
                        
                        # Save session summary for rolling context
                        key_decisions = enhancement.get("keyDecisions", [])
                        if isinstance(key_decisions, list) and key_decisions:
                            key_decisions_text = [
                                d.get("decision", str(d)) if isinstance(d, dict) else str(d)
                                for d in key_decisions[:5]
                            ]
                        else:
                            key_decisions_text = []
                        
                        action_items = enhancement.get("actionItems", [])
                        pending_items = [
                            item.get("task", str(item)) if isinstance(item, dict) else str(item)
                            for item in action_items
                            if isinstance(item, dict) and item.get("status") != "done"
                        ][:5]
                        
                        files_discussed_paths = [f.get("path", "") for f in metadata.get("filesDiscussed", [])]
                        
                        # Calculate session duration
                        session_duration_ms = 0
                        if conversation_start > 0 and conversation_end > 0:
                            session_duration_ms = conversation_end - conversation_start
                        
                        db.save_session_summary(
                            repo=repo,
                            composer_id=composer_id,
                            one_line_summary=enhancement.get("summary", "")[:200],
                            key_decisions=key_decisions_text,
                            pending_items=pending_items,
                            files_touched=files_discussed_paths[:10],
                            exchange_count=len(prompts_to_process),
                            session_duration_ms=session_duration_ms
                        )
                        logger.info(f"Saved session summary for rolling context")
                        
                        # Save hierarchical context based on files discussed
                        try:
                            context_for_future = enhancement.get("contextForFuture", {})
                            
                            # Save repo-level context if we have key decisions
                            if key_decisions_text:
                                db.save_hierarchical_context(
                                    repo=repo,
                                    scope_type="repo",
                                    summary=enhancement.get("summary", "")[:200],
                                    key_facts=key_decisions_text[:5],
                                    patterns=context_for_future.get("patterns", [])[:5] if isinstance(context_for_future, dict) else [],
                                    source_session_id=composer_id,
                                    confidence=0.7
                                )
                            
                            # Save folder-level context for each unique folder
                            folders_seen = set()
                            for file_path in files_discussed_paths[:10]:
                                if '/' in file_path:
                                    folder = '/'.join(file_path.split('/')[:-1])
                                    if folder and folder not in folders_seen:
                                        folders_seen.add(folder)
                                        # Extract folder-relevant decisions
                                        folder_facts = [d for d in key_decisions_text if folder.split('/')[-1].lower() in d.lower()][:3]
                                        if folder_facts:
                                            db.save_hierarchical_context(
                                                repo=repo,
                                                scope_type="folder",
                                                scope_path=folder,
                                                summary=f"Work in {folder}: {enhancement.get('summary', '')[:100]}",
                                                key_facts=folder_facts,
                                                source_session_id=composer_id,
                                                confidence=0.6
                                            )
                            
                            # Save file-level context for specifically discussed files
                            file_contexts = context_for_future.get("fileContexts", []) if isinstance(context_for_future, dict) else []
                            for fc in file_contexts[:5]:
                                if isinstance(fc, dict) and fc.get("path") and fc.get("context"):
                                    db.save_hierarchical_context(
                                        repo=repo,
                                        scope_type="file",
                                        scope_path=fc["path"],
                                        summary=fc["context"][:200],
                                        key_facts=fc.get("facts", [])[:5],
                                        source_session_id=composer_id,
                                        confidence=0.8
                                    )
                            
                            if folders_seen:
                                logger.info(f"Saved hierarchical context for {len(folders_seen)} folder(s)")
                        except Exception as ctx_err:
                            logger.warning(f"Failed to save hierarchical context: {ctx_err}")
                        
                    except Exception as e:
                        logger.warning(f"Failed to process task lifecycle: {e}", exc_info=True)
                    
                    # Broadcast LLM completion event (if broadcast function available)
                    # This will be called from bridge_server after storing
            except Exception as e:
                logger.error(f"LLM enhancement failed: {e}", exc_info=True)
        else:
            # Preserve existing LLM fields when skipping
            if existing_summary:
                summary = preserve_llm_fields_when_skipping(summary, existing_summary)
            
            # Generate lightweight insight when full enhancement doesn't run
            try:
                exchange_insight = await generate_exchange_insight(
                    metadata,
                    handle_tool_call_fn
                )
                if exchange_insight:
                    # Initialize insightsTimeline if needed
                    if "insightsTimeline" not in metadata:
                        metadata["insightsTimeline"] = []
                    
                    # Get existing timeline from existing_metadata if available
                    existing_timeline = []
                    if existing_metadata and "insightsTimeline" in existing_metadata:
                        existing_timeline = existing_metadata["insightsTimeline"]
                    
                    # Merge: add new insight, avoid duplicates by exchangeSequence
                    existing_sequences = {insight.get("exchangeSequence") for insight in existing_timeline}
                    if exchange_insight.get("exchangeSequence") not in existing_sequences:
                        existing_timeline.append(exchange_insight)
                        # Sort by timestamp descending (most recent first)
                        existing_timeline.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
                        metadata["insightsTimeline"] = existing_timeline
                        logger.info(f"Added lightweight exchange insight: {exchange_insight.get('text', '')[:50]}...")
            except Exception as e:
                logger.warning(f"Failed to generate lightweight exchange insight: {e}", exc_info=True)
                # Don't fail capture if insight generation fails
        
        # Store analysis
        try:
            # Calculate timeout
            total_messages = len(metadata.get("userPrompts", [])) + len(metadata.get("assistantResponses", []))
            summary_size = len(json.dumps(summary))
            store_timeout_ms = 30000  # Base 30 seconds
            if total_messages > 2000 or summary_size > 1000000:
                store_timeout_ms = 300000  # 5 minutes
            elif total_messages > 1000 or summary_size > 500000:
                store_timeout_ms = 180000  # 3 minutes
            elif total_messages > 500 or summary_size > 200000:
                store_timeout_ms = 120000  # 2 minutes
            
            # Store
            real_created_at = conversation_start if conversation_start > 0 else int(time.time() * 1000)
            
            # Merge new metadata with existing metadata (cumulative storage)
            # This ensures we don't lose previous messages when updating
            new_metadata_to_store = {
                "userPrompts": metadata.get("userPrompts", []),
                "reasoning": metadata.get("reasoning", []),
                "assistantResponses": metadata.get("assistantResponses", []),
                "timestamps": metadata.get("timestamps", []),
                "intentEvolution": metadata.get("intentEvolution", [])
            }
            merged_metadata = merge_metadata_cumulative(new_metadata_to_store, existing_metadata)
            
            logger.info(
                f"Storing merged metadata: {len(merged_metadata.get('userPrompts', []))} prompts, "
                f"{len(merged_metadata.get('assistantResponses', []))} responses "
                f"(new: {len(new_metadata_to_store.get('userPrompts', []))} + "
                f"existing: {len(existing_metadata.get('userPrompts', [])) if existing_metadata else 0})"
            )
            
            store_result = await handle_tool_call_fn("turingmind_store_chat_analysis_plan", {
                "repo": repo,
                "composer_id": composer_id,
                "thread_name": metadata.get("threadName", "Chat Session"),
                "metadata": merged_metadata,
                "summary": summary,
                "created_at": real_created_at
            })
            
            if store_result and store_result.get("status") == "stored":
                # Update state - use merged_metadata for counts since that's what we stored
                message_count = len(merged_metadata.get("userPrompts", [])) + len(merged_metadata.get("assistantResponses", []))
                now = int(time.time() * 1000)
                
                # Merge processed files
                previously_processed = cached_state.get("processedFiles", set()) if cached_state else set()
                all_processed_files = previously_processed.union(newly_processed_files)
                
                db.update_chat_capture_state(
                    composer_id,
                    message_count=message_count,
                    last_captured_at=now,
                    last_llm_enhanced_at=now if llm_enhanced else None,
                    last_llm_processed_prompt_index=len(merged_metadata.get("userPrompts", [])) if llm_enhanced else None,
                    last_llm_processed_response_index=len(merged_metadata.get("assistantResponses", [])) if llm_enhanced else None,
                    last_captured_exchange_count=exchange_state.get("assistantResponseCount", 0),
                    last_exchange_timestamp=exchange_state.get("lastBubbleTimestamp", 0),
                    processed_files=all_processed_files
                )
                
                logger.info(
                    f"✅ Captured: {composer_id[:8]}... "
                    f"({message_count} messages, {len(newly_processed_files)} new files, "
                    f"{'LLM enhanced' if llm_enhanced else 'no LLM'})"
                )
                
                return {
                    "status": "captured",
                    "composerId": composer_id,
                    "messageCount": message_count,
                    "llmEnhanced": llm_enhanced,
                    "filesProcessed": len(newly_processed_files)
                }
            else:
                return {"status": "error", "error": "Storage failed"}
        except Exception as e:
            logger.error(f"Error storing analysis: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}
    
    except Exception as e:
        logger.error(f"Error in capture_exchange: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}
