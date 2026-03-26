"""
Tests for Cursor Database Reader

Tests the functions that read from Cursor's SQLite database.
"""

import json
import pytest
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from turingmind_mcp.cursor_database_reader import (
    find_cursor_database,
    composer_exists_in_database,
    execute_sqlite_query,
    get_most_recently_active_composer,
    get_last_exchange_state,
    extract_metadata,
    extract_timestamp,
    extract_smart_intent,
    generate_thread_name,
)


class TestFindCursorDatabase:
    """Tests for find_cursor_database()"""
    
    @patch('turingmind_mcp.cursor_database_reader._get_cursor_global_storage')
    @patch('turingmind_mcp.cursor_database_reader._get_cursor_workspace_storage')
    def test_find_database_not_found(self, mock_workspace, mock_global, tmp_path):
        """Test when database doesn't exist."""
        # Mock storage paths to point to tmp_path (which has no database)
        mock_global.return_value = tmp_path / "Library/Application Support/Cursor/User/globalStorage"
        mock_workspace.return_value = tmp_path / "Library/Application Support/Cursor/User/workspaceStorage"
        
        result = find_cursor_database()
        assert result is None
    
    @patch('turingmind_mcp.cursor_database_reader.composer_exists_in_database')
    @patch('turingmind_mcp.cursor_database_reader._get_cursor_global_storage')
    @patch('turingmind_mcp.cursor_database_reader._get_cursor_workspace_storage')
    def test_find_database_in_global_storage(self, mock_workspace, mock_global, mock_exists, tmp_path):
        """Test finding database in globalStorage."""
        # Setup
        global_storage = tmp_path / "Library/Application Support/Cursor/User/globalStorage"
        global_storage.mkdir(parents=True)
        db_path = global_storage / "state.vscdb"
        db_path.write_bytes(b"fake db")
        
        mock_global.return_value = global_storage
        mock_workspace.return_value = tmp_path / "Library/Application Support/Cursor/User/workspaceStorage"
        mock_exists.return_value = True
        
        result = find_cursor_database("test-composer-id")
        assert result == db_path


class TestComposerExistsInDatabase:
    """Tests for composer_exists_in_database()"""
    
    def test_composer_exists(self, tmp_path):
        """Test checking if composer exists."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # Create table and insert test data
        cursor.execute("""
            CREATE TABLE cursorDiskKV (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cursor.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            ("bubbleId:test-composer-id:123", json.dumps({"type": 1, "text": "test"}))
        )
        conn.commit()
        conn.close()
        
        result = composer_exists_in_database(str(db_path), "test-composer-id")
        assert result is True
    
    def test_composer_not_exists(self, tmp_path):
        """Test when composer doesn't exist."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE cursorDiskKV (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()
        conn.close()
        
        result = composer_exists_in_database(str(db_path), "nonexistent")
        assert result is False


class TestGetMostRecentlyActiveComposer:
    """Tests for get_most_recently_active_composer()"""
    
    def test_get_most_recent_composer(self, tmp_path):
        """Test getting most recently active composer."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE cursorDiskKV (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        # Insert bubbles with timestamps
        composer1 = "composer-1"
        composer2 = "composer-2"
        
        # Composer 1: older bubble
        cursor.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (
                f"bubbleId:{composer1}:b1",
                json.dumps({"type": 1, "createdAt": "2025-01-01T10:00:00.000Z"})
            )
        )
        
        # Composer 2: newer bubble (should be returned)
        cursor.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (
                f"bubbleId:{composer2}:b1",
                json.dumps({"type": 1, "createdAt": "2025-01-01T12:00:00.000Z"})
            )
        )
        
        conn.commit()
        conn.close()
        
        result = get_most_recently_active_composer(str(db_path))
        assert result is not None
        assert result["composerId"] == composer2
        assert result["bubbleCount"] == 1
    
    def test_no_bubbles_found(self, tmp_path):
        """Test when no bubbles exist."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE cursorDiskKV (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()
        conn.close()
        
        result = get_most_recently_active_composer(str(db_path))
        assert result is None


class TestGetLastExchangeState:
    """Tests for get_last_exchange_state()"""
    
    def test_complete_exchange(self, tmp_path):
        """Test detecting complete exchange (user → AI → user)."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE cursorDiskKV (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        composer_id = "test-composer"
        
        # User message 1
        cursor.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (
                f"bubbleId:{composer_id}:b1",
                json.dumps({"type": 1, "createdAt": "2025-01-01T10:00:00.000Z"})
            )
        )
        
        # Assistant response
        cursor.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (
                f"bubbleId:{composer_id}:b2",
                json.dumps({"type": 2, "createdAt": "2025-01-01T10:01:00.000Z"})
            )
        )
        
        # User message 2 (completes exchange)
        cursor.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (
                f"bubbleId:{composer_id}:b3",
                json.dumps({"type": 1, "createdAt": "2025-01-01T10:02:00.000Z"})
            )
        )
        
        conn.commit()
        conn.close()
        
        result = get_last_exchange_state(str(db_path), composer_id)
        assert result is not None
        assert result["isCompleteExchange"] is True
        assert result["userMessageCount"] == 2
        assert result["assistantResponseCount"] == 1
    
    def test_incomplete_exchange(self, tmp_path):
        """Test detecting incomplete exchange (waiting for user)."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE cursorDiskKV (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        composer_id = "test-composer"
        
        # User message
        cursor.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (
                f"bubbleId:{composer_id}:b1",
                json.dumps({"type": 1, "createdAt": "2025-01-01T10:00:00.000Z"})
            )
        )
        
        # Assistant response (last bubble - exchange not complete)
        cursor.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (
                f"bubbleId:{composer_id}:b2",
                json.dumps({"type": 2, "createdAt": "2025-01-01T10:01:00.000Z"})
            )
        )
        
        conn.commit()
        conn.close()
        
        result = get_last_exchange_state(str(db_path), composer_id)
        assert result is not None
        assert result["isCompleteExchange"] is False


class TestExtractTimestamp:
    """Tests for extract_timestamp()"""
    
    def test_extract_from_bubble_created_at(self):
        """Test extracting timestamp from bubble createdAt."""
        bubble = {"createdAt": "2025-01-01T10:00:00.000Z"}
        timestamp = extract_timestamp(bubble)
        assert timestamp > 0
        assert isinstance(timestamp, int)
    
    def test_fallback_to_composer_created_at(self):
        """Test fallback to composer createdAt."""
        bubble = {}
        composer_created_at = 1704110400000  # 2025-01-01 10:00:00
        timestamp = extract_timestamp(bubble, composer_created_at)
        assert timestamp == composer_created_at
    
    def test_fallback_to_zero(self):
        """Test fallback to zero when no timestamp available."""
        bubble = {}
        timestamp = extract_timestamp(bubble)
        assert timestamp == 0


class TestExtractSmartIntent:
    """Tests for extract_smart_intent()"""
    
    def test_extract_substantive_intent(self):
        """Test extracting substantive intent."""
        prompts = [
            {"text": "yes", "timestamp": 1000},
            {"text": "ok", "timestamp": 2000},
            {"text": "Implement user authentication system with JWT tokens", "timestamp": 3000}
        ]
        
        intent = extract_smart_intent(prompts)
        assert "authentication" in intent.lower()
        assert intent != "yes"
        assert intent != "ok"
    
    def test_skip_filler_messages(self):
        """Test skipping filler messages."""
        prompts = [
            {"text": "yes", "timestamp": 1000},
            {"text": "ok", "timestamp": 2000},
            {"text": "thanks", "timestamp": 3000}
        ]
        
        intent = extract_smart_intent(prompts)
        # Should fallback to first prompt
        assert intent == "yes"
    
    def test_skip_short_messages(self):
        """Test skipping very short messages."""
        prompts = [
            {"text": "hi", "timestamp": 1000},
            {"text": "Implement a comprehensive user management system", "timestamp": 2000}
        ]
        
        intent = extract_smart_intent(prompts)
        assert "user management" in intent.lower()
        assert intent != "hi"
    
    def test_fallback_to_first_prompt(self):
        """Test fallback when no substantive message found."""
        prompts = [
            {"text": "test", "timestamp": 1000}
        ]
        
        intent = extract_smart_intent(prompts)
        assert intent == "test"


class TestGenerateThreadName:
    """Tests for generate_thread_name()"""
    
    def test_generate_from_intent(self):
        """Test generating thread name from intent."""
        intent = "Implement user authentication system"
        name = generate_thread_name(intent)
        assert "user authentication" in name.lower()
        assert len(name) <= 60
    
    def test_clean_intent_prefixes(self):
        """Test cleaning common prefixes."""
        intent = "can you implement authentication"
        name = generate_thread_name(intent)
        assert "can you" not in name.lower()
    
    def test_truncate_long_intent(self):
        """Test truncating very long intents."""
        intent = "A" * 100
        name = generate_thread_name(intent)
        assert len(name) <= 60
        assert name.endswith("...")
    
    def test_fallback_for_empty_intent(self):
        """Test fallback for empty intent."""
        name = generate_thread_name("")
        assert name == "Chat session"


class TestExtractMetadata:
    """Tests for extract_metadata()"""
    
    @pytest.fixture
    def test_db(self, tmp_path):
        """Create test database with sample conversation."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE cursorDiskKV (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        composer_id = "test-composer"
        created_at = 1704110400000  # 2025-01-01 10:00:00
        
        # ComposerData
        cursor.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (
                f"composerData:{composer_id}",
                json.dumps({"createdAt": created_at})
            )
        )
        
        # User prompt
        cursor.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (
                f"bubbleId:{composer_id}:b1",
                json.dumps({
                    "type": 1,
                    "text": "Implement user authentication",
                    "createdAt": "2025-01-01T10:00:00.000Z",
                    "attachedFileCodeChunksMetadataOnly": [
                        {"relativeWorkspacePath": "src/auth.py"}
                    ]
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
                    "createdAt": "2025-01-01T10:01:00.000Z",
                    "allThinkingBlocks": ["Let me think about this..."],
                    "tokenCount": {"inputTokens": 100, "outputTokens": 200}
                })
            )
        )
        
        conn.commit()
        conn.close()
        return str(db_path), composer_id
    
    def test_extract_complete_metadata(self, test_db):
        """Test extracting complete metadata."""
        db_path, composer_id = test_db
        
        metadata = extract_metadata(composer_id, db_path)
        
        assert metadata is not None
        assert len(metadata["userPrompts"]) == 1
        assert len(metadata["assistantResponses"]) == 1
        assert len(metadata["reasoning"]) == 1
        assert len(metadata["filesDiscussed"]) == 1
        assert metadata["filesDiscussed"][0]["path"] == "src/auth.py"
        assert metadata["tokenUsage"]["totalInput"] == 100
        assert metadata["tokenUsage"]["totalOutput"] == 200
        assert metadata["conversationStart"] > 0
        assert metadata["conversationEnd"] > 0
    
    def test_extract_empty_conversation(self, tmp_path):
        """Test extracting from empty conversation."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE cursorDiskKV (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()
        conn.close()
        
        metadata = extract_metadata("nonexistent", str(db_path))
        # Should return empty metadata, not None (since we create the structure)
        assert metadata is not None
        assert len(metadata["userPrompts"]) == 0
    
    def test_extract_with_todos(self, test_db):
        """Test extracting AI todos."""
        db_path, composer_id = test_db
        
        # Add bubble with todos
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            (
                f"bubbleId:{composer_id}:b3",
                json.dumps({
                    "type": 2,
                    "text": "Here are the tasks",
                    "createdAt": "2025-01-01T10:02:00.000Z",
                    "todos": [
                        json.dumps({"id": "todo-1", "content": "Create auth module", "status": "pending"})
                    ]
                })
            )
        )
        conn.commit()
        conn.close()
        
        metadata = extract_metadata(composer_id, db_path)
        assert len(metadata["aiTodos"]) == 1
        assert metadata["aiTodos"][0]["content"] == "Create auth module"
