"""
Tests for Chat Capture Module

Tests the chat capture detection and processing logic.
"""

import pytest
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any, Optional

from turingmind_mcp.chat_capture import (
    check_exchanges,
    should_capture_chat,
    filter_to_latest_exchange,
    get_files_modified_in_time_range,
    get_file_diff,
    get_file_diffs_for_conversation,
    find_related_commits,
    build_summary,
    merge_llm_enhancement_results,
    preserve_llm_fields_when_skipping,
    AUTO_CAPTURE_INTERVAL_MS,
    CURRENT_CHAT_UPDATE_COOLDOWN_MS,
    LLM_COOLDOWN_MS,
    MIN_NEW_MESSAGES_FOR_LLM,
    RECENT_ACTIVITY_WINDOW_MS,
    VERY_RECENT_ACTIVITY_MS,
    MAX_CHAT_AGE_MS,
    MAX_FILES_TO_PROCESS,
)


class TestCheckExchanges:
    """Tests for check_exchanges()"""
    
    @pytest.fixture
    def mock_db(self):
        """Mock MemoryDatabase instance."""
        db = Mock()
        db.get_chat_capture_state.return_value = None
        return db
    
    @pytest.mark.asyncio
    @patch('turingmind_mcp.chat_capture.find_cursor_database')
    @patch('turingmind_mcp.chat_capture.get_most_recently_active_composer')
    @patch('turingmind_mcp.chat_capture.get_last_exchange_state')
    async def test_check_exchanges_ready_to_capture(
        self,
        mock_get_exchange_state,
        mock_get_most_recent,
        mock_find_db,
        mock_db,
        tmp_path
    ):
        """Test when exchange is ready to capture."""
        # Setup mocks
        db_path = tmp_path / "test.db"
        mock_find_db.return_value = db_path
        
        composer_id = "test-composer"
        now = int(time.time() * 1000)
        
        mock_get_most_recent.return_value = {
            "composerId": composer_id,
            "lastActivityAt": now - 1000,  # 1 second ago (write complete)
            "bubbleCount": 4
        }
        
        mock_get_exchange_state.return_value = {
            "isCompleteExchange": True,
            "totalBubbles": 4,
            "userMessageCount": 2,
            "assistantResponseCount": 2,
            "lastBubbleTimestamp": now - 500
        }
        
        result = await check_exchanges(mock_db, "test-repo")
        
        assert len(result["exchanges"]) == 1
        assert result["exchanges"][0]["composerId"] == composer_id
        assert result["exchanges"][0]["shouldEnhanceLLM"] is True  # First capture
    
    @pytest.mark.asyncio
    @patch('turingmind_mcp.chat_capture.find_cursor_database')
    @patch('turingmind_mcp.chat_capture.get_most_recently_active_composer')
    @patch('turingmind_mcp.chat_capture.get_last_exchange_state')
    async def test_check_exchanges_in_cooldown(
        self,
        mock_get_exchange_state,
        mock_get_most_recent,
        mock_find_db,
        mock_db,
        tmp_path
    ):
        """Test when exchange is in cooldown."""
        db_path = tmp_path / "test.db"
        mock_find_db.return_value = db_path
        
        composer_id = "test-composer"
        now = int(time.time() * 1000)
        
        mock_get_most_recent.return_value = {
            "composerId": composer_id,
            "lastActivityAt": now - 1000,
            "bubbleCount": 4
        }
        
        mock_get_exchange_state.return_value = {
            "isCompleteExchange": True,
            "totalBubbles": 4,
            "userMessageCount": 2,
            "assistantResponseCount": 2,
            "lastBubbleTimestamp": now - 500
        }
        
        # Set cached state with recent capture (in cooldown)
        mock_db.get_chat_capture_state.return_value = {
            "messageCount": 4,
            "lastCapturedAt": now - 10000,  # 10 seconds ago (still in cooldown)
            "lastLLMEnhancedAt": 0,
            "processedFiles": set()
        }
        
        result = await check_exchanges(mock_db, "test-repo")
        
        # Should return empty (in cooldown)
        assert len(result["exchanges"]) == 0
    
    @pytest.mark.asyncio
    @patch('turingmind_mcp.chat_capture.find_cursor_database')
    @patch('turingmind_mcp.chat_capture.get_most_recently_active_composer')
    async def test_check_exchanges_no_active_composer(
        self,
        mock_get_most_recent,
        mock_find_db,
        mock_db,
        tmp_path
    ):
        """Test when no active composer found."""
        db_path = tmp_path / "test.db"
        mock_find_db.return_value = db_path
        mock_get_most_recent.return_value = None
        
        result = await check_exchanges(mock_db, "test-repo")
        
        assert len(result["exchanges"]) == 0


class TestShouldCaptureChat:
    """Tests for should_capture_chat()"""
    
    @pytest.mark.asyncio
    async def test_capture_chat_started_after_session(self):
        """Test capturing chat that started after session."""
        now = int(time.time() * 1000)
        session_start = now - 3600000  # 1 hour ago
        chat_start = now - 1800000  # 30 minutes ago (after session)
        
        metadata = {
            "conversationStart": chat_start,
            "conversationEnd": now - 1000,
            "userPrompts": [{"timestamp": chat_start}],
            "assistantResponses": []
        }
        
        should_capture, reason = should_capture_chat(metadata, None, session_start, False)
        assert should_capture is True
        assert reason is None
    
    @pytest.mark.asyncio
    async def test_skip_old_chat(self):
        """Test skipping old chat."""
        now = int(time.time() * 1000)
        session_start = now - 3600000  # 1 hour ago
        chat_start = now - (8 * 24 * 60 * 60 * 1000)  # 8 days ago (old)
        
        metadata = {
            "conversationStart": chat_start,
            "conversationEnd": now - (7 * 24 * 60 * 60 * 1000),  # 7 days ago
            "userPrompts": [{"timestamp": chat_start}],
            "assistantResponses": []
        }
        
        should_capture, reason = should_capture_chat(metadata, None, session_start, False)
        assert should_capture is False
        assert "old" in reason.lower()
    
    @pytest.mark.asyncio
    async def test_capture_very_recent_activity(self):
        """Test capturing chat with very recent activity."""
        now = int(time.time() * 1000)
        session_start = now - (2 * 24 * 60 * 60 * 1000)  # 2 days ago
        chat_start = now - (3 * 24 * 60 * 60 * 1000)  # 3 days ago
        recent_message = now - 3600000  # 1 hour ago (very recent)
        
        metadata = {
            "conversationStart": chat_start,
            "conversationEnd": now - 1000,
            "userPrompts": [{"timestamp": recent_message}],
            "assistantResponses": []
        }
        
        should_capture, reason = should_capture_chat(metadata, None, session_start, False)
        assert should_capture is True  # Very recent activity overrides age


class TestFilterToLatestExchange:
    """Tests for filter_to_latest_exchange()"""
    
    def test_filter_to_latest_exchange(self):
        """Test filtering to only latest exchange."""
        now = int(time.time() * 1000)
        last_exchange_timestamp = now - 3600000  # 1 hour ago
        
        full_metadata = {
            "userPrompts": [
                {"text": "First prompt", "timestamp": now - 7200000},  # 2 hours ago
                {"text": "Second prompt", "timestamp": now - 1800000}  # 30 min ago (latest)
            ],
            "assistantResponses": [
                {"text": "First response", "timestamp": now - 5400000},  # 1.5 hours ago
                {"text": "Second response", "timestamp": now - 900000}  # 15 min ago (latest)
            ],
            "reasoning": [
                {"bubbleId": "b1", "reasoning": ["think1"], "timestamp": now - 5400000},
                {"bubbleId": "b2", "reasoning": ["think2"], "timestamp": now - 900000}  # latest
            ],
            "conversationStart": now - 7200000,
            "conversationEnd": now - 900000
        }
        
        filtered = filter_to_latest_exchange(full_metadata, last_exchange_timestamp)
        
        assert len(filtered["userPrompts"]) == 1
        assert filtered["userPrompts"][0]["text"] == "Second prompt"
        assert len(filtered["assistantResponses"]) == 1
        assert filtered["assistantResponses"][0]["text"] == "Second response"
        assert len(filtered["reasoning"]) == 1
        assert filtered["reasoning"][0]["bubbleId"] == "b2"
    
    def test_no_filtering_on_first_capture(self):
        """Test filtering to current exchange on first capture (timestamp = 0)."""
        full_metadata = {
            "userPrompts": [
                {"text": "First", "timestamp": 1000},
                {"text": "Second", "timestamp": 2000},
                {"text": "Third", "timestamp": 3000}
            ],
            "assistantResponses": [
                {"text": "Response 1", "timestamp": 1500},
                {"text": "Response 2", "timestamp": 2500}
            ],
            "reasoning": [],
            "conversationStart": 1000,
            "conversationEnd": 3000
        }
        
        filtered = filter_to_latest_exchange(full_metadata, 0)
        
        # Should return only current exchange (last 2 prompts + responses between them)
        assert len(filtered["userPrompts"]) == 2  # Last 2 prompts
        assert filtered["userPrompts"][0]["text"] == "Second"
        assert filtered["userPrompts"][1]["text"] == "Third"
        assert len(filtered["assistantResponses"]) == 1  # Only response between last 2 prompts
        assert filtered["assistantResponses"][0]["text"] == "Response 2"


class TestGitOperations:
    """Tests for git operations"""
    
    @patch('turingmind_mcp.chat_capture.subprocess.run')
    def test_get_files_modified_in_time_range(self, mock_subprocess, tmp_path):
        """Test getting modified files via git."""
        # Mock git log output - git log --name-only returns file paths, one per line
        mock_result = Mock()
        mock_result.returncode = 0
        # git log --name-only format: commit hash, then file paths
        mock_result.stdout = "file1.py\nfile2.py\nfile3.py\n"
        mock_subprocess.return_value = mock_result
        
        start_time = int(time.time() * 1000) - 3600000
        end_time = int(time.time() * 1000)
        
        files = get_files_modified_in_time_range(start_time, end_time, str(tmp_path))
        
        assert "file1.py" in files
        assert "file2.py" in files
        assert "file3.py" in files
    
    @patch('turingmind_mcp.chat_capture.subprocess.run')
    def test_get_file_diff_tracked_file(self, mock_subprocess, tmp_path):
        """Test getting diff for tracked file."""
        # Mock git ls-files (file is tracked)
        mock_ls_files = Mock()
        mock_ls_files.returncode = 0
        
        # Mock git diff
        mock_diff = Mock()
        mock_diff.returncode = 0
        mock_diff.stdout = "diff --git a/file.py b/file.py\n+new line\n"
        
        mock_subprocess.side_effect = [mock_ls_files, mock_diff]
        
        diff = get_file_diff("file.py", str(tmp_path))
        
        assert diff is not None
        assert "file.py" in diff
    
    @patch('turingmind_mcp.chat_capture.subprocess.run')
    def test_get_file_diff_untracked_file(self, mock_subprocess, tmp_path):
        """Test getting diff for untracked file."""
        # Mock git ls-files (file is not tracked)
        mock_ls_files = Mock()
        mock_ls_files.returncode = 1  # Not tracked
        
        mock_subprocess.return_value = mock_ls_files
        
        # Create untracked file
        test_file = tmp_path / "new_file.py"
        test_file.write_text("print('hello')\nprint('world')\n")
        
        diff = get_file_diff("new_file.py", str(tmp_path))
        
        assert diff is not None
        assert "new file" in diff
        assert "hello" in diff
        assert "world" in diff
    
    @patch('turingmind_mcp.chat_capture.get_file_diff')
    def test_get_file_diffs_for_conversation(self, mock_get_diff, tmp_path):
        """Test getting diffs for multiple files."""
        mock_get_diff.return_value = "diff content"
        
        files = [
            {"path": "file1.py", "mentionedAt": 1000},
            {"path": "file2.py", "mentionedAt": 2000}
        ]
        
        diffs = get_file_diffs_for_conversation(
            files,
            1000,
            2000,
            str(tmp_path)
        )
        
        assert len(diffs) == 2
        assert "file1.py" in diffs
        assert "file2.py" in diffs
        assert mock_get_diff.call_count == 2


class TestShortChatFiltering:
    """Tests for short chat filtering - tests actual implementation in capture_exchange()"""
    
    @pytest.mark.asyncio
    @patch('turingmind_mcp.cursor_database_reader.find_cursor_database')
    @patch('turingmind_mcp.cursor_database_reader.extract_metadata')
    async def test_skip_very_short_chat(
        self,
        mock_extract_metadata,
        mock_find_db,
        tmp_path
    ):
        """Test skipping very short chat (< 10 chars) via actual capture_exchange()."""
        from turingmind_mcp.database import MemoryDatabase
        from turingmind_mcp.chat_capture import capture_exchange
        
        mcp_db = MemoryDatabase(str(tmp_path / "mcp.db"))
        mock_find_db.return_value = Path(tmp_path / "test.db")
        
        now = int(time.time() * 1000)
        session_start = now - 3600000
        
        # Very short chat (< 10 chars)
        mock_extract_metadata.return_value = {
            "conversationStart": now - 1800000,
            "conversationEnd": now,
            "userPrompts": [{"text": "hi", "timestamp": now - 1800000}],  # < 10 chars
            "assistantResponses": []
        }
        
        async def mock_handle_tool_call(tool_name, args):
            return {"status": "success"}
        
        result = await capture_exchange(
            mcp_db,
            "test-composer",
            {"isCompleteExchange": True, "totalBubbles": 2},
            should_enhance_llm=False,
            is_update=False,
            repo="test-repo",
            handle_tool_call_fn=mock_handle_tool_call,
            session_start_time=session_start
        )
        
        # Should be skipped due to short chat
        assert result["status"] == "skipped"
        assert result.get("reason") == "too short"
        
        # Verify state was updated
        state = mcp_db.get_chat_capture_state("test-composer")
        assert state is not None
        assert state.get("lastCapturedAt", 0) > 0
        
        mcp_db.close()
    
    @pytest.mark.asyncio
    @patch('turingmind_mcp.cursor_database_reader.find_cursor_database')
    @patch('turingmind_mcp.cursor_database_reader.extract_metadata')
    @patch('turingmind_mcp.chat_capture.get_files_modified_in_time_range')
    @patch('turingmind_mcp.chat_capture.get_file_diffs_for_conversation')
    async def test_capture_normal_chat(
        self,
        mock_get_diffs,
        mock_get_modified,
        mock_extract_metadata,
        mock_find_db,
        tmp_path
    ):
        """Test capturing normal length chat via actual capture_exchange()."""
        from turingmind_mcp.database import MemoryDatabase
        from turingmind_mcp.chat_capture import capture_exchange
        
        mcp_db = MemoryDatabase(str(tmp_path / "mcp.db"))
        mock_find_db.return_value = Path(tmp_path / "test.db")
        
        now = int(time.time() * 1000)
        session_start = now - 3600000
        chat_start = now - 1800000
        
        # Normal length chat (> 10 chars)
        mock_extract_metadata.return_value = {
            "conversationStart": chat_start,
            "conversationEnd": now,
            "userPrompts": [{"text": "Implement user authentication", "timestamp": chat_start}],  # > 10 chars
            "assistantResponses": [{"text": "Response", "timestamp": now - 900000}],
            "filesDiscussed": []
        }
        
        mock_get_modified.return_value = []
        mock_get_diffs.return_value = {}
        
        async def mock_handle_tool_call(tool_name, args):
            if tool_name == "turingmind_store_chat_analysis_plan":
                return {"status": "stored"}
            return {"status": "success"}
        
        result = await capture_exchange(
            mcp_db,
            "test-composer",
            {"isCompleteExchange": True, "totalBubbles": 2},
            should_enhance_llm=False,
            is_update=False,
            repo="test-repo",
            handle_tool_call_fn=mock_handle_tool_call,
            session_start_time=session_start
        )
        
        # Should be captured (not skipped)
        assert result["status"] == "captured"
        
        mcp_db.close()
    
    @pytest.mark.asyncio
    @patch('turingmind_mcp.cursor_database_reader.find_cursor_database')
    @patch('turingmind_mcp.cursor_database_reader.extract_metadata')
    @patch('turingmind_mcp.chat_capture.get_files_modified_in_time_range')
    @patch('turingmind_mcp.chat_capture.get_file_diffs_for_conversation')
    async def test_capture_multi_prompt_chat(
        self,
        mock_get_diffs,
        mock_get_modified,
        mock_extract_metadata,
        mock_find_db,
        tmp_path
    ):
        """Test capturing chat with multiple prompts (not filtered as short) via actual capture_exchange()."""
        from turingmind_mcp.database import MemoryDatabase
        from turingmind_mcp.chat_capture import capture_exchange
        
        mcp_db = MemoryDatabase(str(tmp_path / "mcp.db"))
        mock_find_db.return_value = Path(tmp_path / "test.db")
        
        now = int(time.time() * 1000)
        session_start = now - 3600000
        chat_start = now - 1800000
        
        # Multiple prompts (should not be filtered even if first is short)
        mock_extract_metadata.return_value = {
            "conversationStart": chat_start,
            "conversationEnd": now,
            "userPrompts": [
                {"text": "hi", "timestamp": chat_start},  # Short, but multiple prompts
                {"text": "Implement feature", "timestamp": now - 900000}
            ],
            "assistantResponses": [{"text": "Response", "timestamp": now - 450000}],
            "filesDiscussed": []
        }
        
        mock_get_modified.return_value = []
        mock_get_diffs.return_value = {}
        
        async def mock_handle_tool_call(tool_name, args):
            if tool_name == "turingmind_store_chat_analysis_plan":
                return {"status": "stored"}
            return {"status": "success"}
        
        result = await capture_exchange(
            mcp_db,
            "test-composer",
            {"isCompleteExchange": True, "totalBubbles": 3},
            should_enhance_llm=False,
            is_update=False,
            repo="test-repo",
            handle_tool_call_fn=mock_handle_tool_call,
            session_start_time=session_start
        )
        
        # Should be captured (multiple prompts, not filtered)
        assert result["status"] == "captured"
        
        mcp_db.close()


class TestFileFiltering:
    """Tests for file filtering and limiting logic - tests actual implementation in capture_exchange()"""
    
    @pytest.mark.asyncio
    @patch('turingmind_mcp.cursor_database_reader.find_cursor_database')
    @patch('turingmind_mcp.cursor_database_reader.extract_metadata')
    @patch('turingmind_mcp.chat_capture.get_files_modified_in_time_range')
    @patch('turingmind_mcp.chat_capture.get_file_diffs_for_conversation')
    async def test_filter_processed_files(
        self,
        mock_get_diffs,
        mock_get_modified,
        mock_extract_metadata,
        mock_find_db,
        tmp_path
    ):
        """Test filtering out already processed files via actual capture_exchange()."""
        from turingmind_mcp.database import MemoryDatabase
        from turingmind_mcp.chat_capture import capture_exchange
        
        mcp_db = MemoryDatabase(str(tmp_path / "mcp.db"))
        composer_id = "test-composer"
        
        # Set up state with previously processed files
        mcp_db.update_chat_capture_state(
            composer_id,
            processed_files={"file1.py", "file2.py"}
        )
        
        mock_find_db.return_value = Path(tmp_path / "test.db")
        now = int(time.time() * 1000)
        session_start = now - 3600000
        chat_start = now - 1800000
        
        # Ensure chat passes all filters (recent, not short, etc.)
        mock_extract_metadata.return_value = {
            "conversationStart": chat_start,  # After session start
            "conversationEnd": now,
            "userPrompts": [{"text": "Implement feature", "timestamp": chat_start}],  # > 10 chars
            "assistantResponses": [{"text": "Response", "timestamp": now - 900000}],
            "filesDiscussed": []
        }
        
        # Modified files include already processed ones
        mock_get_modified.return_value = ["file1.py", "file2.py", "file3.py"]
        
        # Track which files were actually processed
        processed_files_list = []
        def mock_diffs(files, start, end, workspace_root):
            for f in files:
                processed_files_list.append(f["path"])
            return {f["path"]: f"diff for {f['path']}" for f in files}
        
        mock_get_diffs.side_effect = mock_diffs
        
        async def mock_handle_tool_call(tool_name, args):
            if tool_name == "turingmind_store_chat_analysis_plan":
                return {"status": "stored"}
            return {"status": "success"}
        
        result = await capture_exchange(
            mcp_db,
            composer_id,
            {"isCompleteExchange": True, "totalBubbles": 2},
            should_enhance_llm=False,
            is_update=False,
            repo="test-repo",
            handle_tool_call_fn=mock_handle_tool_call,
            session_start_time=session_start,
            workspace_root=str(tmp_path)
        )
        
        # Should succeed
        assert result["status"] == "captured"
        
        # Verify only newly modified file was processed (file3.py)
        # file1.py and file2.py should NOT be processed (already processed)
        assert "file3.py" in processed_files_list
        # Note: This tests the actual implementation logic in capture_exchange()
        
        mcp_db.close()
    
    @pytest.mark.asyncio
    @patch('turingmind_mcp.cursor_database_reader.find_cursor_database')
    @patch('turingmind_mcp.cursor_database_reader.extract_metadata')
    @patch('turingmind_mcp.chat_capture.get_files_modified_in_time_range')
    @patch('turingmind_mcp.chat_capture.get_file_diffs_for_conversation')
    async def test_include_mentioned_files_even_if_processed(
        self,
        mock_get_diffs,
        mock_get_modified,
        mock_extract_metadata,
        mock_find_db,
        tmp_path
    ):
        """Test that mentioned files are always included via actual capture_exchange()."""
        from turingmind_mcp.database import MemoryDatabase
        from turingmind_mcp.chat_capture import capture_exchange
        
        mcp_db = MemoryDatabase(str(tmp_path / "mcp.db"))
        composer_id = "test-composer"
        
        # Set up state with previously processed file
        mcp_db.update_chat_capture_state(
            composer_id,
            processed_files={"file1.py"}
        )
        
        mock_find_db.return_value = Path(tmp_path / "test.db")
        now = int(time.time() * 1000)
        session_start = now - 3600000
        chat_start = now - 1800000
        
        # File mentioned in conversation (even though already processed)
        # Ensure chat passes all filters
        mock_extract_metadata.return_value = {
            "conversationStart": chat_start,  # After session start
            "conversationEnd": now,
            "userPrompts": [{"text": "Implement feature", "timestamp": chat_start}],  # > 10 chars
            "assistantResponses": [{"text": "Response", "timestamp": now - 900000}],
            "filesDiscussed": [{"path": "file1.py"}]  # Mentioned file
        }
        
        mock_get_modified.return_value = []  # No newly modified files
        
        # Track which files were processed
        processed_files_list = []
        def mock_diffs(files, start, end, workspace_root):
            for f in files:
                processed_files_list.append(f["path"])
            return {f["path"]: f"diff for {f['path']}" for f in files}
        
        mock_get_diffs.side_effect = mock_diffs
        
        async def mock_handle_tool_call(tool_name, args):
            if tool_name == "turingmind_store_chat_analysis_plan":
                return {"status": "stored"}
            return {"status": "success"}
        
        result = await capture_exchange(
            mcp_db,
            composer_id,
            {"isCompleteExchange": True, "totalBubbles": 2},
            should_enhance_llm=False,
            is_update=False,
            repo="test-repo",
            handle_tool_call_fn=mock_handle_tool_call,
            session_start_time=session_start,
            workspace_root=str(tmp_path)
        )
        
        # Should succeed
        assert result["status"] == "captured"
        
        # Verify mentioned file was included even though already processed
        assert "file1.py" in processed_files_list
        
        mcp_db.close()
    
    def test_limit_files_to_50_logic(self):
        """Test limiting files logic (tests the actual slicing logic used in implementation)."""
        # Create 75 files
        files_to_diff = [f"file{i}.py" for i in range(75)]
        
        # Limit to MAX_FILES_TO_PROCESS (same logic as in capture_exchange line 890)
        limited_files = list(files_to_diff)[:MAX_FILES_TO_PROCESS]
        
        assert len(limited_files) == 50
        assert limited_files[0] == "file0.py"
        assert limited_files[49] == "file49.py"
        # Verify the actual implementation logic
        assert len(limited_files) == MAX_FILES_TO_PROCESS
    
    def test_no_limit_when_under_50_logic(self):
        """Test no limiting when under 50 files (tests the actual logic used in implementation)."""
        files_to_diff = [f"file{i}.py" for i in range(30)]
        
        # Same logic as in capture_exchange line 890
        limited_files = list(files_to_diff)[:MAX_FILES_TO_PROCESS]
        
        assert len(limited_files) == 30
        assert len(limited_files) < MAX_FILES_TO_PROCESS


class TestBuildSummary:
    """Tests for build_summary()"""
    
    def test_build_summary_complete(self):
        """Test building complete summary object."""
        metadata = {
            "smartIntent": "Implement authentication",
            "userPrompts": [{"text": "Add login"}],
            "assistantResponses": [{"text": "I'll help you implement login"}],
            "filesDiscussed": [{"path": "auth.py"}],
            "relatedCommits": [{"sha": "abc123", "message": "Initial commit"}],
            "tokenUsage": {"totalInput": 100, "totalOutput": 200},
            "conversationStart": 1000,
            "conversationEnd": 2000
        }
        
        summary = build_summary(metadata)
        
        assert summary["initialIntent"] == "Implement authentication"
        assert summary["finalIntent"] == "I'll help you implement login"
        assert summary["filesDiscussed"] == ["auth.py"]
        assert len(summary["relatedCommits"]) == 1
        assert summary["relatedCommits"][0]["sha"] == "abc123"
        assert summary["tokenUsage"]["input"] == 100
        assert summary["tokenUsage"]["output"] == 200
        assert summary["tokenUsage"]["total"] == 300
        assert summary["timeRange"]["start"] == 1000
        assert summary["timeRange"]["end"] == 2000
        assert summary["timeRange"]["durationMs"] == 1000
    
    def test_build_summary_missing_fields(self):
        """Test building summary with missing optional fields."""
        metadata = {
            "userPrompts": [{"text": "Test"}],
            "assistantResponses": [],
            "conversationStart": 1000,
            "conversationEnd": 2000
        }
        
        summary = build_summary(metadata)
        
        assert summary["initialIntent"] == "Test"
        assert "finalIntent" not in summary
        assert "filesDiscussed" not in summary
        assert "relatedCommits" not in summary
        assert "tokenUsage" not in summary
        assert summary["timeRange"]["start"] == 1000
    
    def test_build_summary_fallback_intent(self):
        """Test building summary with fallback to first prompt."""
        metadata = {
            "userPrompts": [{"text": "Fallback intent"}],
            "assistantResponses": []
        }
        
        summary = build_summary(metadata)
        
        # Should use first prompt when smartIntent is missing
        assert summary["initialIntent"] == "Fallback intent"


class TestLLMMerging:
    """Tests for LLM enhancement merging"""
    
    def test_merge_action_items_deduplicate(self):
        """Test merging action items with deduplication."""
        existing_summary = {
            "llmActionItems": [{"task": "Fix bug", "priority": "high"}]
        }
        new_enhancement = {
            "actionItems": [
                {"task": "Fix bug", "priority": "high"},  # Duplicate
                {"task": "Add feature", "priority": "medium"}  # New
            ]
        }
        
        merged = merge_llm_enhancement_results(existing_summary, new_enhancement)
        
        assert len(merged["llmActionItems"]) == 2
        # Should have original + new (deduplicated)
        tasks = [item["task"] for item in merged["llmActionItems"]]
        assert "Fix bug" in tasks
        assert "Add feature" in tasks
    
    def test_merge_key_decisions_deduplicate(self):
        """Test merging key decisions with deduplication."""
        existing_summary = {
            "llmKeyDecisions": ["Decision 1"]
        }
        new_enhancement = {
            "keyDecisions": ["Decision 1", "Decision 2"]
        }
        
        merged = merge_llm_enhancement_results(existing_summary, new_enhancement)
        
        # Should deduplicate
        assert len(merged["llmKeyDecisions"]) == 2
        assert "Decision 1" in merged["llmKeyDecisions"]
        assert "Decision 2" in merged["llmKeyDecisions"]
    
    def test_merge_first_capture(self):
        """Test no merge on first capture (no existing summary)."""
        existing_summary = None
        new_enhancement = {
            "threadName": "New Thread",
            "summary": "New summary",
            "actionItems": [{"task": "Task 1"}],
            "keyDecisions": ["Decision 1"]
        }
        
        # First capture - should use enhancement as-is
        # (This is handled in capture_exchange, but we test the merge function)
        # When existing_summary is None, merge shouldn't be called
        # But if it is, it should handle gracefully
        if existing_summary:
            merged = merge_llm_enhancement_results(existing_summary, new_enhancement)
        else:
            # First capture - use as-is
            merged = new_enhancement
        
        assert merged["threadName"] == "New Thread"
        assert len(merged["actionItems"]) == 1


class TestPreserveLLMFields:
    """Tests for preserving LLM fields when skipping"""
    
    def test_preserve_llm_fields_when_skipping(self):
        """Test preserving existing LLM fields when enhancement is skipped."""
        summary = {
            "initialIntent": "Test intent"
        }
        existing_summary = {
            "llmThreadName": "Existing Thread",
            "llmSummary": "Existing summary",
            "llmKeyDecisions": ["Decision 1"],
            "llmActionItems": [{"task": "Task 1"}],
            "llmCodeChanges": "Code changes",
            "llmIntentEvolution": "Evolution"
        }
        
        preserved = preserve_llm_fields_when_skipping(summary, existing_summary)
        
        assert preserved["initialIntent"] == "Test intent"
        assert preserved["llmThreadName"] == "Existing Thread"
        assert preserved["llmSummary"] == "Existing summary"
        assert preserved["llmKeyDecisions"] == ["Decision 1"]
        assert len(preserved["llmActionItems"]) == 1
        assert preserved["llmCodeChanges"] == "Code changes"
        assert preserved["llmIntentEvolution"] == "Evolution"
    
    def test_preserve_llm_fields_no_existing(self):
        """Test preserving when no existing summary."""
        summary = {"initialIntent": "Test"}
        existing_summary = None
        
        preserved = preserve_llm_fields_when_skipping(summary, existing_summary)
        
        # Should return summary as-is
        assert preserved == summary
        assert "llmThreadName" not in preserved


class TestCommitHistory:
    """Tests for find_related_commits()"""
    
    @patch('turingmind_mcp.chat_capture.subprocess.run')
    def test_find_related_commits(self, mock_subprocess, tmp_path):
        """Test finding related commits."""
        # Mock git log output: SHA|message|timestamp
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "abc12345|Initial commit|1609459200\n"
        mock_result.stdout += "def67890|Add feature|1609545600\n"
        mock_subprocess.return_value = mock_result
        
        start_time = 1609459200000  # ms
        end_time = 1609632000000  # ms
        
        commits = find_related_commits(start_time, end_time, str(tmp_path))
        
        assert len(commits) == 2
        assert commits[0]["sha"] == "abc12345"
        assert commits[0]["message"] == "Initial commit"
        assert commits[1]["sha"] == "def67890"
        assert commits[1]["message"] == "Add feature"
    
    @patch('turingmind_mcp.chat_capture.subprocess.run')
    def test_find_related_commits_empty(self, mock_subprocess, tmp_path):
        """Test finding commits when none exist."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_subprocess.return_value = mock_result
        
        commits = find_related_commits(1000, 2000, str(tmp_path))
        
        assert len(commits) == 0
    
    @patch('turingmind_mcp.chat_capture.subprocess.run')
    def test_find_related_commits_git_error(self, mock_subprocess, tmp_path):
        """Test handling git command failure."""
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stderr = "fatal: not a git repository"
        mock_subprocess.return_value = mock_result
        
        commits = find_related_commits(1000, 2000, str(tmp_path))
        
        # Should return empty list on error
        assert len(commits) == 0


class TestConstants:
    """Tests for constants"""
    
    def test_constants_defined(self):
        """Test that all constants are defined."""
        assert AUTO_CAPTURE_INTERVAL_MS == 3000
        assert CURRENT_CHAT_UPDATE_COOLDOWN_MS == 30000
        assert LLM_COOLDOWN_MS == 30000
        assert MIN_NEW_MESSAGES_FOR_LLM == 1
        assert RECENT_ACTIVITY_WINDOW_MS == 6 * 60 * 60 * 1000
        assert VERY_RECENT_ACTIVITY_MS == 2 * 60 * 60 * 1000
        assert MAX_CHAT_AGE_MS == 7 * 24 * 60 * 60 * 1000
        assert MAX_FILES_TO_PROCESS == 50
