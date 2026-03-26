"""
Tests for Chat Capture State Management in Database

Tests the database methods for managing chat capture state.
"""

import pytest
import json
import tempfile
from pathlib import Path

from turingmind_mcp.database import MemoryDatabase


class TestChatCaptureState:
    """Tests for chat capture state management"""
    
    @pytest.fixture
    def db(self, tmp_path):
        """Create test database."""
        db_path = tmp_path / "test.db"
        return MemoryDatabase(str(db_path))
    
    def test_get_chat_capture_state_not_exists(self, db):
        """Test getting state for non-existent composer."""
        state = db.get_chat_capture_state("nonexistent-composer")
        assert state is None
    
    def test_update_chat_capture_state_create_new(self, db):
        """Test creating new chat capture state."""
        composer_id = "test-composer"
        
        success = db.update_chat_capture_state(
            composer_id,
            message_count=10,
            last_captured_at=1000,
            processed_files={"file1.py", "file2.py"}
        )
        
        assert success is True
        
        state = db.get_chat_capture_state(composer_id)
        assert state is not None
        assert state["messageCount"] == 10
        assert state["lastCapturedAt"] == 1000
        assert "file1.py" in state["processedFiles"]
        assert "file2.py" in state["processedFiles"]
    
    def test_update_chat_capture_state_update_existing(self, db):
        """Test updating existing state."""
        composer_id = "test-composer"
        
        # Create initial state
        db.update_chat_capture_state(
            composer_id,
            message_count=10,
            last_captured_at=1000,
            processed_files={"file1.py"}
        )
        
        # Update state
        db.update_chat_capture_state(
            composer_id,
            message_count=15,
            last_captured_at=2000,
            processed_files={"file2.py"}  # Should merge with existing
        )
        
        state = db.get_chat_capture_state(composer_id)
        assert state["messageCount"] == 15
        assert state["lastCapturedAt"] == 2000
        assert "file1.py" in state["processedFiles"]  # Preserved
        assert "file2.py" in state["processedFiles"]  # Added
    
    def test_update_chat_capture_state_llm_tracking(self, db):
        """Test LLM enhancement tracking."""
        composer_id = "test-composer"
        
        db.update_chat_capture_state(
            composer_id,
            message_count=10,
            last_llm_enhanced_at=1000,
            last_llm_processed_prompt_index=5,
            last_llm_processed_response_index=5
        )
        
        state = db.get_chat_capture_state(composer_id)
        assert state["lastLLMEnhancedAt"] == 1000
        assert state["lastLLMProcessedPromptIndex"] == 5
        assert state["lastLLMProcessedResponseIndex"] == 5
    
    def test_update_chat_capture_state_kanban_hashes(self, db):
        """Test Kanban item hash tracking."""
        composer_id = "test-composer"
        
        db.update_chat_capture_state(
            composer_id,
            message_count=10,
            kanban_item_hashes={"hash1", "hash2"}
        )
        
        state = db.get_chat_capture_state(composer_id)
        assert "hash1" in state["kanbanItemHashes"]
        assert "hash2" in state["kanbanItemHashes"]
        
        # Update with new hash (should merge)
        db.update_chat_capture_state(
            composer_id,
            kanban_item_hashes={"hash3"}
        )
        
        state = db.get_chat_capture_state(composer_id)
        assert "hash1" in state["kanbanItemHashes"]  # Preserved
        assert "hash2" in state["kanbanItemHashes"]  # Preserved
        assert "hash3" in state["kanbanItemHashes"]  # Added
    
    def test_chat_capture_state_persists(self, db, tmp_path):
        """Test that state persists across database connections."""
        composer_id = "test-composer"
        
        # Create state
        db.update_chat_capture_state(
            composer_id,
            message_count=10,
            last_captured_at=1000,
            processed_files={"file1.py"}
        )
        
        # Close and reopen database
        db_path = db.db_path
        db.close()
        
        new_db = MemoryDatabase(db_path)
        state = new_db.get_chat_capture_state(composer_id)
        
        assert state is not None
        assert state["messageCount"] == 10
        assert "file1.py" in state["processedFiles"]
        
        new_db.close()
    
    def test_chat_capture_state_multiple_composers(self, db):
        """Test managing state for multiple composers."""
        composer1 = "composer-1"
        composer2 = "composer-2"
        
        db.update_chat_capture_state(
            composer1,
            message_count=10,
            processed_files={"file1.py"}
        )
        
        db.update_chat_capture_state(
            composer2,
            message_count=20,
            processed_files={"file2.py"}
        )
        
        state1 = db.get_chat_capture_state(composer1)
        state2 = db.get_chat_capture_state(composer2)
        
        assert state1["messageCount"] == 10
        assert "file1.py" in state1["processedFiles"]
        
        assert state2["messageCount"] == 20
        assert "file2.py" in state2["processedFiles"]
        
        # States should be independent
        assert state1["processedFiles"] != state2["processedFiles"]
