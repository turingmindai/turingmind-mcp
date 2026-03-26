"""
Integration Tests for Chat Capture with Rolling Context and Task Lifecycle.

Tests the full flow of:
1. Processing chat exchanges
2. Using rolling context from previous sessions
3. Detecting and creating new tasks
4. Updating task phases based on LLM analysis
5. Saving session summaries for future rolling context

Run with: pytest tests/test_chat_capture_integration.py -v
"""

import os
import sys
import tempfile
import time
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from turingmind_mcp.database import MemoryDatabase


@pytest.fixture
def db():
    """Create a temporary database for testing."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test.db")
    database = MemoryDatabase(db_path)
    yield database
    database.close()


class TestRollingContextIntegration:
    """Tests for rolling context integration with LLM enhancement."""

    def test_rolling_context_excludes_current_session(self, db):
        """Ensure rolling context excludes the current session being processed."""
        # Simulate previous sessions
        db.save_session_summary(
            repo="test/repo",
            composer_id="session_001",
            one_line_summary="Set up authentication",
            key_decisions=["Use JWT"],
            pending_items=["Add refresh tokens"]
        )
        db.save_session_summary(
            repo="test/repo",
            composer_id="session_002",
            one_line_summary="Implemented user profile",
            key_decisions=["Use GraphQL"]
        )
        
        # Get context for a new session (should exclude current)
        context = db.get_rolling_context(
            repo="test/repo",
            current_composer_id="session_003"
        )
        
        assert len(context) == 2
        assert all(s["composer_id"] != "session_003" for s in context)

    def test_rolling_context_respects_time_window(self, db):
        """Ensure rolling context only includes recent sessions."""
        now = int(time.time() * 1000)
        
        # Create a session
        db.save_session_summary(
            repo="test/repo",
            composer_id="recent_session",
            one_line_summary="Recent work"
        )
        
        # Manually make one session old
        with db._get_cursor() as cursor:
            old_time = now - (72 * 60 * 60 * 1000)  # 72 hours ago
            cursor.execute(
                "UPDATE session_summaries SET created_at = ? WHERE composer_id = ?",
                (old_time, "recent_session")
            )
            db.conn.commit()
        
        # Query with 24 hour window
        context = db.get_rolling_context(
            repo="test/repo",
            window_hours=24
        )
        
        assert len(context) == 0  # Old session should be excluded


class TestTaskLifecycleIntegration:
    """Tests for task lifecycle integration with LLM enhancement."""

    def test_task_creation_from_llm_response(self, db):
        """Simulate task creation from LLM enhancement response."""
        # Simulate what chat_capture.py does after LLM response
        new_tasks = [
            {
                "description": "Implement rate limiting for API endpoints",
                "initialPhase": "mentioned",
                "relatedFiles": ["api.py", "rate_limiter.py"],
                "priority": "high",
                "confidence": 0.85
            },
            {
                "description": "Add unit tests for authentication",
                "initialPhase": "planned",
                "relatedFiles": ["test_auth.py"],
                "priority": "medium",
                "confidence": 0.75
            }
        ]
        
        created_tasks = []
        for task in new_tasks:
            if task.get("confidence", 0) >= 0.5:
                # Check for duplicates (like chat_capture does)
                similar = db.find_similar_tasks(
                    "test/repo",
                    task["description"],
                    threshold=0.7
                )
                if not similar:
                    result = db.create_task(
                        repo="test/repo",
                        description=task["description"],
                        initial_phase=task.get("initialPhase", "mentioned"),
                        related_files=task.get("relatedFiles", []),
                        priority=task.get("priority", "medium"),
                        confidence=task.get("confidence", 0.5)
                    )
                    created_tasks.append(result)
        
        assert len(created_tasks) == 2
        
        # Verify tasks are retrievable
        active = db.get_active_tasks(repo="test/repo")
        assert len(active) == 2

    def test_task_phase_transition_from_llm(self, db):
        """Simulate task phase updates from LLM analysis."""
        # Create initial task
        task = db.create_task(
            repo="test/repo",
            description="Implement caching layer",
            initial_phase="mentioned"
        )
        task_id = task["id"]
        
        # Simulate LLM detecting work started
        task_updates = [
            {
                "taskId": task_id,
                "transition": {
                    "from": "mentioned",
                    "to": "in_progress"
                },
                "evidence": "User mentioned 'I started working on the cache'",
                "confidence": 0.9
            }
        ]
        
        # Process like chat_capture does
        for update in task_updates:
            tid = update.get("taskId")
            transition = update.get("transition", {})
            to_phase = transition.get("to")
            evidence = update.get("evidence")
            confidence = update.get("confidence", 0.5)
            
            if tid and to_phase and confidence >= 0.5:
                result = db.apply_task_transition(
                    task_id=tid,
                    to_phase=to_phase,
                    evidence=evidence,
                    session_id="session_xyz"
                )
                assert result["status"] == "transitioned"
        
        # Verify task phase updated
        updated_task = db.get_task_by_id(task_id)
        assert updated_task["current_phase"] == "in_progress"
        
        # Verify transition was recorded
        transitions = db.get_task_transitions(task_id)
        assert len(transitions) == 2
        assert transitions[-1]["evidence"] == "User mentioned 'I started working on the cache'"

    def test_task_deduplication_prevents_duplicates(self, db):
        """Ensure duplicate task descriptions are detected and skipped."""
        # Create original task
        db.create_task(
            repo="test/repo",
            description="Add user authentication with OAuth2",
            initial_phase="planned"
        )
        
        # Try to create similar task (like LLM might detect same thing again)
        similar_descriptions = [
            "Add user authentication using OAuth2",
            "Implement user authentication with OAuth2",
            "Add OAuth2 user authentication"
        ]
        
        created_count = 0
        for desc in similar_descriptions:
            similar = db.find_similar_tasks("test/repo", desc, threshold=0.6)
            if not similar:
                db.create_task(repo="test/repo", description=desc)
                created_count += 1
        
        # Should have only the original task
        active = db.get_active_tasks(repo="test/repo")
        assert len(active) == 1

    def test_low_confidence_tasks_ignored(self, db):
        """Ensure low confidence task updates are ignored."""
        task = db.create_task(
            repo="test/repo",
            description="Test task",
            initial_phase="mentioned"
        )
        
        # Try update with low confidence
        low_confidence_update = {
            "taskId": task["id"],
            "transition": {"to": "done"},
            "confidence": 0.3  # Below 0.5 threshold
        }
        
        confidence = low_confidence_update.get("confidence", 0.5)
        if confidence >= 0.5:
            db.apply_task_transition(
                task["id"],
                low_confidence_update["transition"]["to"]
            )
        
        # Task should still be in original phase
        updated = db.get_task_by_id(task["id"])
        assert updated["current_phase"] == "mentioned"


class TestSessionSummaryIntegration:
    """Tests for session summary saving after LLM enhancement."""

    def test_session_summary_saved_after_enhancement(self, db):
        """Simulate saving session summary after LLM enhancement completes."""
        # Simulate LLM enhancement result
        enhancement = {
            "summary": "Implemented rate limiting using token bucket algorithm",
            "keyDecisions": [
                {"decision": "Use Redis for distributed rate limiting"},
                {"decision": "Set limit to 100 req/min per user"}
            ],
            "actionItems": [
                {"task": "Add rate limit headers to response", "status": "pending"},
                {"task": "Write unit tests", "status": "pending"},
                {"task": "Update API docs", "status": "done"}  # Should be excluded
            ]
        }
        
        # Extract like chat_capture does
        key_decisions = enhancement.get("keyDecisions", [])
        key_decisions_text = [
            d.get("decision", str(d)) if isinstance(d, dict) else str(d)
            for d in key_decisions[:5]
        ]
        
        action_items = enhancement.get("actionItems", [])
        pending_items = [
            item.get("task", str(item)) if isinstance(item, dict) else str(item)
            for item in action_items
            if isinstance(item, dict) and item.get("status") != "done"
        ][:5]
        
        # Save summary
        result = db.save_session_summary(
            repo="test/repo",
            composer_id="session_123",
            one_line_summary=enhancement.get("summary", "")[:200],
            key_decisions=key_decisions_text,
            pending_items=pending_items,
            files_touched=["rate_limiter.py", "api.py"],
            exchange_count=5,
            session_duration_ms=300000
        )
        
        assert result["status"] == "saved"
        
        # Verify it's retrievable in rolling context
        context = db.get_rolling_context(repo="test/repo")
        assert len(context) == 1
        assert "rate limiting" in context[0]["one_line_summary"]
        assert len(context[0]["key_decisions"]) == 2
        assert len(context[0]["pending_items"]) == 2  # Excludes "done" item


class TestFullFlowSimulation:
    """End-to-end simulation of chat capture flow."""

    def test_full_chat_capture_flow(self, db):
        """Simulate complete chat capture flow with all features."""
        repo = "user/myproject"
        
        # === Session 1: Initial setup ===
        session1_id = "composer_001"
        
        # No rolling context for first session
        rolling_context = db.get_rolling_context(
            repo=repo,
            current_composer_id=session1_id
        )
        assert len(rolling_context) == 0
        
        # No active tasks yet
        active_tasks = db.get_active_tasks(repo=repo)
        assert len(active_tasks) == 0
        
        # Simulate LLM creating tasks
        db.create_task(
            repo=repo,
            description="Set up project structure",
            initial_phase="in_progress",
            source_session_id=session1_id
        )
        db.create_task(
            repo=repo,
            description="Implement user authentication",
            initial_phase="mentioned",
            source_session_id=session1_id
        )
        
        # Save session summary
        db.save_session_summary(
            repo=repo,
            composer_id=session1_id,
            one_line_summary="Project initialization and planning",
            key_decisions=["Use Python FastAPI", "PostgreSQL for database"],
            pending_items=["Set up CI/CD", "Add tests"]
        )
        
        # === Session 2: Continue work ===
        session2_id = "composer_002"
        
        # Should have rolling context from session 1
        rolling_context = db.get_rolling_context(
            repo=repo,
            current_composer_id=session2_id
        )
        assert len(rolling_context) == 1
        assert "initialization" in rolling_context[0]["one_line_summary"]
        
        # Should have active tasks
        active_tasks = db.get_active_tasks(repo=repo)
        assert len(active_tasks) == 2
        
        # Simulate LLM updating tasks
        setup_task = [t for t in active_tasks if "structure" in t["description"]][0]
        db.apply_task_transition(
            task_id=setup_task["id"],
            to_phase="done",
            evidence="User confirmed project structure is complete",
            session_id=session2_id
        )
        
        auth_task = [t for t in active_tasks if "authentication" in t["description"]][0]
        db.apply_task_transition(
            task_id=auth_task["id"],
            to_phase="in_progress",
            evidence="Started implementing auth routes",
            session_id=session2_id
        )
        
        # Create new task detected
        db.create_task(
            repo=repo,
            description="Add JWT token validation",
            initial_phase="planned",
            source_session_id=session2_id
        )
        
        # Save session 2 summary
        db.save_session_summary(
            repo=repo,
            composer_id=session2_id,
            one_line_summary="Completed setup, started authentication",
            key_decisions=["Use JWT with 15 min expiry"],
            pending_items=["Complete auth middleware"]
        )
        
        # === Verify final state ===
        
        # Active tasks (excludes "done")
        active = db.get_active_tasks(repo=repo)
        assert len(active) == 2  # auth + JWT validation
        
        # Rolling context for future session
        context = db.get_rolling_context(repo=repo)
        assert len(context) == 2
        
        # Task transitions for completed task
        transitions = db.get_task_transitions(setup_task["id"])
        assert transitions[-1]["to_phase"] == "done"
        
        # Auth task should be in_progress
        auth_updated = db.get_task_by_id(auth_task["id"])
        assert auth_updated["current_phase"] == "in_progress"

    def test_stale_task_detection_in_flow(self, db):
        """Test that stale tasks are properly detected."""
        repo = "user/project"
        
        # Create task that will become stale
        task = db.create_task(
            repo=repo,
            description="Review PR #42",
            initial_phase="in_progress"
        )
        
        # Make it old
        with db._get_cursor() as cursor:
            old_time = int((time.time() - 72 * 3600) * 1000)  # 72 hours ago
            cursor.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?",
                (old_time, task["id"])
            )
            db.conn.commit()
        
        # Create recent task
        db.create_task(
            repo=repo,
            description="Fix bug in login",
            initial_phase="in_progress"
        )
        
        # Get stale tasks
        stale = db.get_stale_tasks(repo=repo, stale_hours=48)
        assert len(stale) == 1
        assert stale[0]["description"] == "Review PR #42"
        
        # Regular active tasks should include both
        active = db.get_active_tasks(repo=repo)
        assert len(active) == 2


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_rolling_context_handled(self, db):
        """Ensure empty rolling context doesn't break flow."""
        context = db.get_rolling_context(
            repo="nonexistent/repo",
            window_hours=48
        )
        assert context == []

    def test_transition_to_same_phase_no_op(self, db):
        """Ensure transitioning to same phase is handled gracefully."""
        task = db.create_task(
            repo="test/repo",
            description="Test",
            initial_phase="mentioned"
        )
        
        result = db.apply_task_transition(task["id"], "mentioned")
        assert result["status"] == "no_change"
        
        # Only one transition (initial)
        transitions = db.get_task_transitions(task["id"])
        assert len(transitions) == 1

    def test_very_long_description_handled(self, db):
        """Ensure very long descriptions are handled."""
        long_desc = "A" * 2000
        
        task = db.create_task(
            repo="test/repo",
            description=long_desc
        )
        
        assert task["status"] == "created"
        
        # Retrieve and verify
        retrieved = db.get_task_by_id(task["id"])
        assert len(retrieved["description"]) == 2000

    def test_special_characters_in_description(self, db):
        """Ensure special characters don't break storage."""
        task = db.create_task(
            repo="test/repo",
            description="Fix bug: 'TypeError' in login.py (line 42) - user's input <script>alert('xss')</script>"
        )
        
        assert task["status"] == "created"
        
        # Verify retrieval preserves special chars
        retrieved = db.get_task_by_id(task["id"])
        assert "TypeError" in retrieved["description"]
        assert "<script>" in retrieved["description"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
