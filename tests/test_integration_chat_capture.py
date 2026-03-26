"""
Integration Tests for Chat Capture

End-to-end tests for the complete chat capture flow.
"""

import pytest
import json
import tempfile
import sqlite3
import time
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.chat_capture import (
    check_exchanges,
    capture_exchange,
    MAX_FILES_TO_PROCESS,
)
from turingmind_mcp.cursor_database_reader import (
    find_cursor_database,
    extract_metadata,
    get_most_recently_active_composer,
    get_last_exchange_state,
)


class TestEndToEndCaptureFlow:
    """End-to-end tests for complete capture flow"""
    
    @pytest.fixture
    def test_cursor_db(self, tmp_path):
        """Create test Cursor database with sample conversation."""
        db_path = tmp_path / "state.vscdb"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE cursorDiskKV (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        composer_id = "test-composer-integration"
        now = int(time.time() * 1000)
        
        # ComposerData
        cursor.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (
                f"composerData:{composer_id}",
                json.dumps({"createdAt": now - 3600000})
            )
        )
        
        # User message 1
        cursor.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (
                f"bubbleId:{composer_id}:b1",
                json.dumps({
                    "type": 1,
                    "text": "Implement user authentication",
                    "createdAt": "2025-01-01T10:00:00.000Z"
                })
            )
        )
        
        # Assistant response
        cursor.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (
                f"bubbleId:{composer_id}:b2",
                json.dumps({
                    "type": 2,
                    "text": "I'll help you implement authentication",
                    "createdAt": "2025-01-01T10:01:00.000Z"
                })
            )
        )
        
        # User message 2 (completes exchange)
        cursor.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (
                f"bubbleId:{composer_id}:b3",
                json.dumps({
                    "type": 1,
                    "text": "Add JWT tokens",
                    "createdAt": "2025-01-01T10:02:00.000Z"
                })
            )
        )
        
        conn.commit()
        conn.close()
        return str(db_path), composer_id
    
    @pytest.mark.asyncio
    @patch('turingmind_mcp.chat_capture.find_cursor_database')
    @patch('turingmind_mcp.chat_capture.get_most_recently_active_composer')
    @patch('turingmind_mcp.chat_capture.get_last_exchange_state')
    async def test_complete_capture_flow(
        self,
        mock_get_exchange_state,
        mock_get_most_recent,
        mock_find_db,
        test_cursor_db,
        tmp_path
    ):
        """Test complete capture flow from detection to state update."""
        db_path, composer_id = test_cursor_db
        
        # Setup mocks
        mock_find_db.return_value = Path(db_path)
        mock_get_most_recent.return_value = {
            "composerId": composer_id,
            "lastActivityAt": int(time.time() * 1000) - 1000,
            "bubbleCount": 3
        }
        mock_get_exchange_state.return_value = {
            "isCompleteExchange": True,
            "totalBubbles": 3,
            "userMessageCount": 2,
            "assistantResponseCount": 1,
            "lastBubbleTimestamp": int(time.time() * 1000) - 500
        }
        
        # Create MCP database
        mcp_db = MemoryDatabase(str(tmp_path / "mcp.db"))
        
        # Check exchanges
        result = await check_exchanges(mcp_db, "test-repo")
        
        # Verify exchange detected
        assert len(result["exchanges"]) == 1
        exchange = result["exchanges"][0]
        assert exchange["composerId"] == composer_id
        assert exchange["shouldEnhanceLLM"] is True  # First capture
        
        # Verify state would be updated (simulate)
        mcp_db.update_chat_capture_state(
            composer_id,
            message_count=3,
            last_captured_at=int(time.time() * 1000),
            last_exchange_timestamp=exchange["exchangeState"]["lastBubbleTimestamp"]
        )
        
        # Verify state persisted
        state = mcp_db.get_chat_capture_state(composer_id)
        assert state is not None
        assert state["messageCount"] == 3
        
        mcp_db.close()
    
    @pytest.mark.asyncio
    async def test_multiple_exchanges_sequential(self, test_cursor_db, tmp_path):
        """Test capturing multiple exchanges sequentially."""
        db_path, composer_id = test_cursor_db
        
        # Create MCP database
        mcp_db = MemoryDatabase(str(tmp_path / "mcp.db"))
        
        # First capture
        with patch('turingmind_mcp.chat_capture.find_cursor_database', return_value=Path(db_path)):
            with patch('turingmind_mcp.chat_capture.get_most_recently_active_composer') as mock_get_most:
                with patch('turingmind_mcp.chat_capture.get_last_exchange_state') as mock_get_state:
                    now = int(time.time() * 1000)
                    
                    # First exchange
                    mock_get_most.return_value = {
                        "composerId": composer_id,
                        "lastActivityAt": now - 1000,
                        "bubbleCount": 3
                    }
                    mock_get_state.return_value = {
                        "isCompleteExchange": True,
                        "totalBubbles": 3,
                        "userMessageCount": 2,
                        "assistantResponseCount": 1,
                        "lastBubbleTimestamp": now - 500
                    }
                    
                    result1 = await check_exchanges(mcp_db, "test-repo")
                    assert len(result1["exchanges"]) == 1
                    
                    # Update state after first capture
                    exchange1 = result1["exchanges"][0]
                    # Use a time in the past to avoid cooldown (30 seconds)
                    past_time = now - 40000  # 40 seconds ago (past cooldown)
                    mcp_db.update_chat_capture_state(
                        composer_id,
                        message_count=3,
                        last_captured_at=past_time,
                        last_exchange_timestamp=exchange1["exchangeState"]["lastBubbleTimestamp"]
                    )
                    
                    # Second exchange (should only capture latest)
                    # Update time to simulate new activity
                    new_now = int(time.time() * 1000)
                    mock_get_most.return_value = {
                        "composerId": composer_id,
                        "lastActivityAt": new_now - 1000,  # Recent activity
                        "bubbleCount": 5
                    }
                    mock_get_state.return_value = {
                        "isCompleteExchange": True,
                        "totalBubbles": 5,  # New bubbles added
                        "userMessageCount": 3,
                        "assistantResponseCount": 2,
                        "lastBubbleTimestamp": new_now - 500
                    }
                    
                    result2 = await check_exchanges(mcp_db, "test-repo")
                    # Should detect new exchange
                    assert len(result2["exchanges"]) == 1
        
        mcp_db.close()
    
    @pytest.mark.asyncio
    @patch('turingmind_mcp.cursor_database_reader.find_cursor_database')
    @patch('turingmind_mcp.cursor_database_reader.extract_metadata')
    @patch('turingmind_mcp.chat_capture.get_files_modified_in_time_range')
    @patch('turingmind_mcp.chat_capture.get_file_diffs_for_conversation')
    async def test_end_to_end_skip_flow(
        self,
        mock_get_diffs,
        mock_get_modified,
        mock_extract_metadata,
        mock_find_db,
        test_cursor_db,
        tmp_path
    ):
        """Test complete skip flow (old chat → skip → state update)."""
        db_path, composer_id = test_cursor_db
        mcp_db = MemoryDatabase(str(tmp_path / "mcp.db"))
        
        # Setup mocks for old chat
        mock_find_db.return_value = Path(db_path)
        now = int(time.time() * 1000)
        old_chat_start = now - (8 * 24 * 60 * 60 * 1000)  # 8 days ago (old)
        
        mock_extract_metadata.return_value = {
            "conversationStart": old_chat_start,
            "conversationEnd": now - (7 * 24 * 60 * 60 * 1000),
            "userPrompts": [{"text": "Old chat", "timestamp": old_chat_start}],
            "assistantResponses": []
        }
        
        # Mock tool call handler
        async def mock_handle_tool_call(tool_name, args):
            return {"status": "success"}
        
        # Try to capture (should skip)
        result = await capture_exchange(
            mcp_db,
            composer_id,
            {"isCompleteExchange": True, "totalBubbles": 2},
            should_enhance_llm=False,
            is_update=False,
            repo="test-repo",
            handle_tool_call_fn=mock_handle_tool_call,
            session_start_time=now - 3600000  # 1 hour ago
        )
        
        # Should be skipped
        assert result["status"] == "skipped"
        assert "old" in result.get("reason", "").lower()
        
        # Verify state was updated (to prevent re-checking)
        state = mcp_db.get_chat_capture_state(composer_id)
        assert state is not None
        assert state.get("lastCapturedAt", 0) > 0
        
        mcp_db.close()
    
    @pytest.mark.asyncio
    @patch('turingmind_mcp.cursor_database_reader.find_cursor_database')
    @patch('turingmind_mcp.cursor_database_reader.extract_metadata')
    @patch('turingmind_mcp.chat_capture.get_files_modified_in_time_range')
    @patch('turingmind_mcp.chat_capture.get_file_diffs_for_conversation')
    async def test_file_processing_with_limits(
        self,
        mock_get_diffs,
        mock_get_modified,
        mock_extract_metadata,
        mock_find_db,
        test_cursor_db,
        tmp_path
    ):
        """Test file processing with limits (75 files → limit to 50)."""
        db_path, composer_id = test_cursor_db
        mcp_db = MemoryDatabase(str(tmp_path / "mcp.db"))
        
        # Setup mocks
        mock_find_db.return_value = Path(db_path)
        now = int(time.time() * 1000)
        
        # Create 75 files (more than MAX_FILES_TO_PROCESS = 50)
        all_files = [f"file{i}.py" for i in range(75)]
        mentioned_files = [{"path": f} for f in all_files[:10]]  # 10 mentioned
        
        # Ensure chat is recent (started after session)
        session_start = now - 3600000  # 1 hour ago
        chat_start = now - 1800000  # 30 minutes ago (after session)
        
        mock_extract_metadata.return_value = {
            "conversationStart": chat_start,
            "conversationEnd": now,
            "userPrompts": [{"text": "Implement feature", "timestamp": chat_start}],  # > 10 chars
            "assistantResponses": [{"text": "Response", "timestamp": now - 900000}],
            "filesDiscussed": mentioned_files
        }
        
        # Mock modified files (all 75 files)
        mock_get_modified.return_value = all_files
        
        # Mock diffs (should only be called for 50 files)
        call_count = {"count": 0}
        def mock_diffs(files, start, end, workspace_root):
            call_count["count"] = len(files)
            return {f["path"]: f"diff for {f['path']}" for f in files}
        
        mock_get_diffs.side_effect = mock_diffs
        
        # Mock tool call handler
        async def mock_handle_tool_call(tool_name, args):
            if tool_name == "turingmind_store_chat_analysis_plan":
                return {"status": "stored"}
            return {"status": "success"}
        
        # Capture exchange
        result = await capture_exchange(
            mcp_db,
            composer_id,
            {"isCompleteExchange": True, "totalBubbles": 2},
            should_enhance_llm=False,
            is_update=False,
            repo="test-repo",
            handle_tool_call_fn=mock_handle_tool_call,
            session_start_time=session_start,  # Use same session_start
            workspace_root=str(tmp_path)
        )
        
        # Should succeed
        assert result["status"] == "captured"
        
        # Verify only 50 files were processed (limit enforced)
        assert call_count["count"] <= MAX_FILES_TO_PROCESS
        
        # Verify processed files tracked in state
        state = mcp_db.get_chat_capture_state(composer_id)
        assert state is not None
        processed_files = state.get("processedFiles", set())
        assert len(processed_files) <= MAX_FILES_TO_PROCESS
        
        mcp_db.close()
    
    @pytest.mark.asyncio
    @patch('turingmind_mcp.cursor_database_reader.find_cursor_database')
    @patch('turingmind_mcp.cursor_database_reader.extract_metadata')
    @patch('turingmind_mcp.chat_capture.get_files_modified_in_time_range')
    @patch('turingmind_mcp.chat_capture.get_file_diffs_for_conversation')
    async def test_llm_merging_incremental(
        self,
        mock_get_diffs,
        mock_get_modified,
        mock_extract_metadata,
        mock_find_db,
        test_cursor_db,
        tmp_path
    ):
        """Test LLM merging incremental (first capture → second capture → merge)."""
        db_path, composer_id = test_cursor_db
        mcp_db = MemoryDatabase(str(tmp_path / "mcp.db"))
        
        # Setup mocks
        mock_find_db.return_value = Path(db_path)
        now = int(time.time() * 1000)
        
        # First capture metadata
        first_metadata = {
            "conversationStart": now - 3600000,
            "conversationEnd": now - 1800000,
            "userPrompts": [{"text": "First prompt", "timestamp": now - 3600000}],
            "assistantResponses": [{"text": "First response", "timestamp": now - 2700000}],
            "filesDiscussed": []
        }
        
        mock_extract_metadata.return_value = first_metadata
        mock_get_modified.return_value = []
        mock_get_diffs.return_value = {}
        
        # Mock LLM enhancement for first capture
        first_enhancement = {
            "threadName": "First Thread",
            "summary": "First summary",
            "actionItems": [{"task": "Task 1", "priority": "high"}],
            "keyDecisions": ["Decision 1"]
        }
        
        call_count = {"count": 0}
        async def mock_handle_tool_call(tool_name, args):
            call_count["count"] += 1
            if tool_name == "turingmind_enhance_chat_analysis":
                return {"status": "success", "result": first_enhancement}
            elif tool_name == "turingmind_get_chat_analysis_plans":
                # Return existing plan for second capture
                if call_count["count"] > 1:
                    return {
                        "plans": [{
                            "summary": {
                                "llmThreadName": "First Thread",
                                "llmSummary": "First summary",
                                "llmActionItems": [{"task": "Task 1", "priority": "high"}],
                                "llmKeyDecisions": ["Decision 1"]
                            }
                        }]
                    }
                return {"plans": []}
            elif tool_name == "turingmind_store_chat_analysis_plan":
                return {"status": "stored"}
            return {"status": "success"}
        
        # First capture
        result1 = await capture_exchange(
            mcp_db,
            composer_id,
            {"isCompleteExchange": True, "totalBubbles": 2},
            should_enhance_llm=True,
            is_update=False,
            repo="test-repo",
            handle_tool_call_fn=mock_handle_tool_call,
            workspace_root=str(tmp_path)
        )
        
        assert result1["status"] == "captured"
        
        # Second capture (incremental)
        second_metadata = {
            "conversationStart": now - 3600000,
            "conversationEnd": now,
            "userPrompts": [
                {"text": "First prompt", "timestamp": now - 3600000},
                {"text": "Second prompt", "timestamp": now - 900000}  # New
            ],
            "assistantResponses": [
                {"text": "First response", "timestamp": now - 2700000},
                {"text": "Second response", "timestamp": now - 450000}  # New
            ],
            "filesDiscussed": []
        }
        
        mock_extract_metadata.return_value = second_metadata
        
        # Second enhancement (should merge with first)
        second_enhancement = {
            "threadName": "Updated Thread",
            "summary": "Updated summary",
            "actionItems": [
                {"task": "Task 1", "priority": "high"},  # Duplicate
                {"task": "Task 2", "priority": "medium"}  # New
            ],
            "keyDecisions": ["Decision 1", "Decision 2"]  # Decision 1 duplicate, Decision 2 new
        }
        
        async def mock_handle_tool_call_second(tool_name, args):
            if tool_name == "turingmind_enhance_chat_analysis":
                return {"status": "success", "result": second_enhancement}
            elif tool_name == "turingmind_get_chat_analysis_plans":
                return {
                    "plans": [{
                        "summary": {
                            "llmThreadName": "First Thread",
                            "llmSummary": "First summary",
                            "llmActionItems": [{"task": "Task 1", "priority": "high"}],
                            "llmKeyDecisions": ["Decision 1"]
                        }
                    }]
                }
            elif tool_name == "turingmind_store_chat_analysis_plan":
                # Verify merged summary is stored
                stored_summary = args.get("summary", {})
                # Should have merged action items (Task 1 + Task 2, no duplicate)
                action_items = stored_summary.get("llmActionItems", [])
                assert len(action_items) == 2
                tasks = [item["task"] for item in action_items]
                assert "Task 1" in tasks
                assert "Task 2" in tasks
                # Should have merged decisions (Decision 1 + Decision 2, deduplicated)
                decisions = stored_summary.get("llmKeyDecisions", [])
                assert len(decisions) == 2
                assert "Decision 1" in decisions
                assert "Decision 2" in decisions
                return {"status": "stored"}
            return {"status": "success"}
        
        # Update state to simulate first capture completed
        mcp_db.update_chat_capture_state(
            composer_id,
            message_count=2,
            last_captured_at=now - 35000,  # Past cooldown
            last_exchange_timestamp=now - 1800000
        )
        
        # Second capture (should merge)
        result2 = await capture_exchange(
            mcp_db,
            composer_id,
            {"isCompleteExchange": True, "totalBubbles": 4},
            should_enhance_llm=True,
            is_update=True,
            repo="test-repo",
            handle_tool_call_fn=mock_handle_tool_call_second,
            workspace_root=str(tmp_path)
        )
        
        assert result2["status"] == "captured"
        
        mcp_db.close()


class TestStatePersistence:
    """Tests for state persistence across operations"""
    
    def test_state_persists_across_restarts(self, tmp_path):
        """Test that state persists when database is closed and reopened."""
        db_path = tmp_path / "test.db"
        composer_id = "test-composer"
        
        # Create and update state
        db1 = MemoryDatabase(str(db_path))
        db1.update_chat_capture_state(
            composer_id,
            message_count=10,
            last_captured_at=1000,
            processed_files={"file1.py", "file2.py"}
        )
        db1.close()
        
        # Reopen database
        db2 = MemoryDatabase(str(db_path))
        state = db2.get_chat_capture_state(composer_id)
        
        assert state is not None
        assert state["messageCount"] == 10
        assert "file1.py" in state["processedFiles"]
        assert "file2.py" in state["processedFiles"]
        
        db2.close()
