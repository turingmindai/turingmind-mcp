"""
Tests for Task Lifecycle and Rolling Context features.

Run with: pytest tests/test_task_lifecycle.py -v
"""

import os
import tempfile
import time
import pytest
import sqlite3

# Add src to path
import sys
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


class TestTaskCreation:
    """Tests for task creation functionality."""

    def test_create_task_basic(self, db):
        """Test basic task creation."""
        result = db.create_task(
            repo="test/repo",
            description="Implement user authentication"
        )
        
        assert result["status"] == "created"
        assert result["id"].startswith("task_")
        assert result["current_phase"] == "mentioned"
        assert result["description"] == "Implement user authentication"

    def test_create_task_with_all_fields(self, db):
        """Test task creation with all optional fields."""
        result = db.create_task(
            repo="test/repo",
            description="Add rate limiting",
            initial_phase="planned",
            related_files=["auth.py", "limiter.py"],
            source_session_id="session_123",
            priority="high",
            confidence=0.9
        )
        
        assert result["status"] == "created"
        assert result["current_phase"] == "planned"
        
        # Verify stored data
        task = db.get_task_by_id(result["id"])
        assert task["priority"] == "high"
        assert task["confidence"] == 0.9
        assert task["related_files"] == ["auth.py", "limiter.py"]

    def test_create_task_creates_initial_transition(self, db):
        """Test that task creation also creates initial transition record."""
        result = db.create_task(
            repo="test/repo",
            description="Test task"
        )
        
        transitions = db.get_task_transitions(result["id"])
        assert len(transitions) == 1
        assert transitions[0]["from_phase"] == "none"
        assert transitions[0]["to_phase"] == "mentioned"
        assert transitions[0]["evidence"] == "Task first identified"


class TestTaskRetrieval:
    """Tests for task retrieval functionality."""

    def test_get_task_by_id(self, db):
        """Test getting a single task by ID."""
        created = db.create_task(
            repo="test/repo",
            description="Test task",
            related_files=["file.py"]
        )
        
        task = db.get_task_by_id(created["id"])
        
        assert task is not None
        assert task["description"] == "Test task"
        assert task["related_files"] == ["file.py"]

    def test_get_task_by_id_not_found(self, db):
        """Test getting a non-existent task."""
        task = db.get_task_by_id("task_nonexistent")
        assert task is None

    def test_get_active_tasks(self, db):
        """Test getting active tasks."""
        # Create tasks in different phases
        db.create_task(repo="test/repo", description="Task 1", initial_phase="mentioned")
        db.create_task(repo="test/repo", description="Task 2", initial_phase="planned")
        
        # Create a done task
        done_task = db.create_task(repo="test/repo", description="Task 3")
        db.apply_task_transition(done_task["id"], "done", "Completed")
        
        tasks = db.get_active_tasks(repo="test/repo")
        
        assert len(tasks) == 2  # Excludes done task
        descriptions = [t["description"] for t in tasks]
        assert "Task 3" not in descriptions

    def test_get_active_tasks_custom_exclude(self, db):
        """Test getting active tasks with custom exclusions."""
        db.create_task(repo="test/repo", description="Task 1", initial_phase="mentioned")
        db.create_task(repo="test/repo", description="Task 2", initial_phase="blocked")
        
        # Exclude blocked as well
        tasks = db.get_active_tasks(
            repo="test/repo",
            exclude_phases=["done", "abandoned", "blocked"]
        )
        
        assert len(tasks) == 1
        assert tasks[0]["description"] == "Task 1"


class TestTaskTransitions:
    """Tests for task phase transitions."""

    def test_apply_transition(self, db):
        """Test applying a phase transition."""
        task = db.create_task(repo="test/repo", description="Test task")
        
        result = db.apply_task_transition(
            task_id=task["id"],
            to_phase="planned",
            evidence="User confirmed"
        )
        
        assert result["status"] == "transitioned"
        assert result["from_phase"] == "mentioned"
        assert result["to_phase"] == "planned"

    def test_apply_transition_updates_task(self, db):
        """Test that transition updates the task's current_phase."""
        task = db.create_task(repo="test/repo", description="Test task")
        db.apply_task_transition(task["id"], "in_progress", "Started work")
        
        updated_task = db.get_task_by_id(task["id"])
        assert updated_task["current_phase"] == "in_progress"

    def test_apply_same_transition_no_change(self, db):
        """Test that applying same phase does nothing."""
        task = db.create_task(repo="test/repo", description="Test task")
        
        result = db.apply_task_transition(task["id"], "mentioned")
        
        assert result["status"] == "no_change"

    def test_transition_history(self, db):
        """Test getting full transition history."""
        task = db.create_task(repo="test/repo", description="Test task")
        db.apply_task_transition(task["id"], "planned", "Will do this")
        db.apply_task_transition(task["id"], "in_progress", "Started")
        db.apply_task_transition(task["id"], "implemented", "Code written")
        
        transitions = db.get_task_transitions(task["id"])
        
        assert len(transitions) == 4  # Initial + 3 transitions
        phases = [(t["from_phase"], t["to_phase"]) for t in transitions]
        assert phases == [
            ("none", "mentioned"),
            ("mentioned", "planned"),
            ("planned", "in_progress"),
            ("in_progress", "implemented")
        ]

    def test_transition_not_found(self, db):
        """Test transition on non-existent task."""
        result = db.apply_task_transition("task_fake", "done")
        assert "error" in result


class TestTaskSimilarity:
    """Tests for task similarity/deduplication."""

    def test_find_similar_tasks(self, db):
        """Test finding similar tasks."""
        db.create_task(repo="test/repo", description="Implement user authentication")
        db.create_task(repo="test/repo", description="Add rate limiting")
        db.create_task(repo="test/repo", description="Implement admin authentication")
        
        similar = db.find_similar_tasks(
            repo="test/repo",
            description="Implement authentication",
            threshold=0.3
        )
        
        assert len(similar) >= 1
        # Should find tasks with "Implement" and "authentication"

    def test_find_similar_tasks_exact_match(self, db):
        """Test finding exact match."""
        db.create_task(repo="test/repo", description="Add unit tests for auth module")
        
        similar = db.find_similar_tasks(
            repo="test/repo",
            description="Add unit tests for auth module",
            threshold=0.9
        )
        
        assert len(similar) == 1
        assert similar[0]["similarity"] >= 0.9

    def test_find_similar_excludes_done(self, db):
        """Test that similarity search excludes done/abandoned tasks."""
        task = db.create_task(repo="test/repo", description="Test task")
        db.apply_task_transition(task["id"], "done", "Completed")
        
        similar = db.find_similar_tasks(
            repo="test/repo",
            description="Test task",
            threshold=0.5
        )
        
        assert len(similar) == 0


class TestStaleTasks:
    """Tests for stale task detection."""

    def test_get_stale_tasks(self, db):
        """Test getting stale tasks."""
        task = db.create_task(repo="test/repo", description="Old task")
        
        # Manually set old timestamp using direct SQL
        with db._get_cursor() as cursor:
            old_time = int((time.time() - 72 * 3600) * 1000)  # 72 hours ago
            cursor.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?",
                (old_time, task["id"])
            )
            db.conn.commit()
        
        stale = db.get_stale_tasks(repo="test/repo", stale_hours=48)
        
        assert len(stale) == 1
        assert stale[0]["id"] == task["id"]
        assert stale[0]["stale_hours"] >= 70

    def test_stale_excludes_done(self, db):
        """Test that stale detection excludes done tasks."""
        task = db.create_task(repo="test/repo", description="Old done task")
        db.apply_task_transition(task["id"], "done", "Completed")
        
        # Make it old
        with db._get_cursor() as cursor:
            old_time = int((time.time() - 72 * 3600) * 1000)
            cursor.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?",
                (old_time, task["id"])
            )
            db.conn.commit()
        
        stale = db.get_stale_tasks(repo="test/repo", stale_hours=48)
        
        assert len(stale) == 0


class TestSessionSummaries:
    """Tests for session summary/rolling context."""

    def test_save_session_summary(self, db):
        """Test saving a session summary."""
        result = db.save_session_summary(
            repo="test/repo",
            composer_id="session_123",
            one_line_summary="Fixed auth bug",
            key_decisions=["Use Redis"],
            pending_items=["Add tests"],
            files_touched=["auth.py"],
            exchange_count=5,
            session_duration_ms=300000
        )
        
        assert result["status"] == "saved"
        assert result["composer_id"] == "session_123"

    def test_save_session_summary_upsert(self, db):
        """Test that save_session_summary updates existing record."""
        db.save_session_summary(
            repo="test/repo",
            composer_id="session_123",
            one_line_summary="First summary"
        )
        
        db.save_session_summary(
            repo="test/repo",
            composer_id="session_123",
            one_line_summary="Updated summary"
        )
        
        summaries = db.get_rolling_context(repo="test/repo")
        
        assert len(summaries) == 1
        assert summaries[0]["one_line_summary"] == "Updated summary"

    def test_get_rolling_context(self, db):
        """Test getting rolling context."""
        db.save_session_summary(
            repo="test/repo",
            composer_id="session_1",
            one_line_summary="Session 1",
            key_decisions=["Decision 1"]
        )
        db.save_session_summary(
            repo="test/repo",
            composer_id="session_2",
            one_line_summary="Session 2"
        )
        
        summaries = db.get_rolling_context(repo="test/repo")
        
        assert len(summaries) == 2
        # Should have parsed JSON fields
        assert summaries[0]["key_decisions"] is not None

    def test_get_rolling_context_excludes_current(self, db):
        """Test that rolling context excludes current session."""
        db.save_session_summary(repo="test/repo", composer_id="session_1", one_line_summary="S1")
        db.save_session_summary(repo="test/repo", composer_id="session_2", one_line_summary="S2")
        
        summaries = db.get_rolling_context(
            repo="test/repo",
            current_composer_id="session_2"
        )
        
        assert len(summaries) == 1
        assert summaries[0]["composer_id"] == "session_1"

    def test_get_rolling_context_respects_limit(self, db):
        """Test that rolling context respects max_sessions."""
        for i in range(10):
            db.save_session_summary(
                repo="test/repo",
                composer_id=f"session_{i}",
                one_line_summary=f"Session {i}"
            )
        
        summaries = db.get_rolling_context(repo="test/repo", max_sessions=3)
        
        assert len(summaries) == 3

    def test_get_rolling_context_truncates_long_summary(self, db):
        """Test that long summaries are truncated on save."""
        long_summary = "A" * 500
        
        db.save_session_summary(
            repo="test/repo",
            composer_id="session_1",
            one_line_summary=long_summary
        )
        
        summaries = db.get_rolling_context(repo="test/repo")
        
        assert len(summaries[0]["one_line_summary"]) <= 200


class TestIntegration:
    """Integration tests combining multiple features."""

    def test_full_task_lifecycle(self, db):
        """Test a task going through full lifecycle."""
        # 1. Create task
        task = db.create_task(
            repo="test/repo",
            description="Implement feature X",
            initial_phase="mentioned",
            priority="high"
        )
        
        # 2. Plan it
        db.apply_task_transition(task["id"], "planned", "Added to sprint")
        
        # 3. Start work
        db.apply_task_transition(task["id"], "in_progress", "Picked up ticket")
        
        # 4. Implement
        db.apply_task_transition(task["id"], "implemented", "Code complete")
        
        # 5. Test
        db.apply_task_transition(task["id"], "tested", "All tests passing")
        
        # 6. Complete
        db.apply_task_transition(task["id"], "done", "Merged to main")
        
        # Verify
        final_task = db.get_task_by_id(task["id"])
        assert final_task["current_phase"] == "done"
        
        transitions = db.get_task_transitions(task["id"])
        assert len(transitions) == 6  # Initial + 5 transitions (mentioned->planned->in_progress->implemented->tested->done)
        
        # Should not appear in active tasks
        active = db.get_active_tasks(repo="test/repo")
        assert task["id"] not in [t["id"] for t in active]

    def test_rolling_context_with_tasks(self, db):
        """Test that rolling context and tasks work together."""
        # Create session with tasks
        session_id = "session_abc"
        
        task = db.create_task(
            repo="test/repo",
            description="Task from session",
            source_session_id=session_id
        )
        
        db.save_session_summary(
            repo="test/repo",
            composer_id=session_id,
            one_line_summary="Session with task creation",
            pending_items=["Task from session"]
        )
        
        # Verify task links back to session
        task_data = db.get_task_by_id(task["id"])
        assert task_data["source_session_id"] == session_id
        
        # Verify session summary
        summaries = db.get_rolling_context(repo="test/repo")
        assert any("Task from session" in s.get("pending_items", []) for s in summaries)


class TestPendingTasks:
    """Tests for opt-in task creation via pending tasks."""

    def test_create_pending_task(self, db):
        """Test creating a pending task."""
        result = db.create_pending_task(
            repo="test/repo",
            description="Add user authentication",
            suggested_phase="mentioned",
            related_files=["auth.py"],
            priority="high",
            confidence=0.85
        )
        
        assert result["id"].startswith("pending_")
        assert result["status"] == "pending"
        assert result["confidence"] == 0.85

    def test_get_pending_tasks(self, db):
        """Test retrieving pending tasks."""
        db.create_pending_task(repo="test/repo", description="Task 1")
        db.create_pending_task(repo="test/repo", description="Task 2")
        
        pending = db.get_pending_tasks(repo="test/repo")
        
        assert len(pending) == 2

    def test_approve_pending_task(self, db):
        """Test approving a pending task creates an active task."""
        pending = db.create_pending_task(
            repo="test/repo",
            description="Test task",
            suggested_phase="planned"
        )
        
        result = db.approve_pending_task(pending["id"])
        
        assert result["status"] == "approved"
        assert "task_id" in result
        
        # Verify the task was created
        task = db.get_task_by_id(result["task_id"])
        assert task["description"] == "Test task"
        assert task["current_phase"] == "planned"

    def test_approve_with_override_phase(self, db):
        """Test approving with a different phase."""
        pending = db.create_pending_task(
            repo="test/repo",
            description="Override test",
            suggested_phase="mentioned"
        )
        
        result = db.approve_pending_task(pending["id"], override_phase="in_progress")
        
        task = db.get_task_by_id(result["task_id"])
        assert task["current_phase"] == "in_progress"

    def test_reject_pending_task(self, db):
        """Test rejecting a pending task."""
        pending = db.create_pending_task(repo="test/repo", description="Reject me")
        
        result = db.reject_pending_task(pending["id"], reason="Not relevant")
        
        assert result["status"] == "rejected"
        assert result["reason"] == "Not relevant"
        
        # Verify it's no longer in pending list
        pending_list = db.get_pending_tasks(repo="test/repo")
        assert len(pending_list) == 0

    def test_pending_task_count(self, db):
        """Test getting pending task count."""
        db.create_pending_task(repo="test/repo", description="Task 1")
        db.create_pending_task(repo="test/repo", description="Task 2")
        db.create_pending_task(repo="other/repo", description="Task 3")
        
        count = db.get_pending_task_count(repo="test/repo")
        
        assert count == 2

    def test_cannot_approve_twice(self, db):
        """Test that approved tasks cannot be approved again."""
        pending = db.create_pending_task(repo="test/repo", description="Test")
        db.approve_pending_task(pending["id"])
        
        result = db.approve_pending_task(pending["id"])
        
        assert "error" in result


class TestHierarchicalContext:
    """Tests for hierarchical context (repo/folder/file)."""

    def test_save_repo_context(self, db):
        """Test saving repo-level context."""
        result = db.save_hierarchical_context(
            repo="test/repo",
            scope_type="repo",
            summary="FastAPI backend with PostgreSQL",
            key_facts=["REST API", "JWT auth"],
            patterns=["Repository pattern"]
        )
        
        assert result["status"] == "saved"
        assert result["scope_type"] == "repo"
        assert result["scope_path"] is None

    def test_save_folder_context(self, db):
        """Test saving folder-level context."""
        result = db.save_hierarchical_context(
            repo="test/repo",
            scope_type="folder",
            scope_path="src/auth",
            summary="Authentication module",
            key_facts=["JWT tokens", "15 min expiry"]
        )
        
        assert result["status"] == "saved"
        assert result["scope_path"] == "src/auth"

    def test_save_file_context(self, db):
        """Test saving file-level context."""
        result = db.save_hierarchical_context(
            repo="test/repo",
            scope_type="file",
            scope_path="src/auth/login.py",
            summary="Login validates against LDAP first"
        )
        
        assert result["status"] == "saved"
        assert result["scope_path"] == "src/auth/login.py"

    def test_folder_requires_path(self, db):
        """Test that folder scope requires a path."""
        result = db.save_hierarchical_context(
            repo="test/repo",
            scope_type="folder",
            summary="No path provided"
        )
        
        assert "error" in result

    def test_get_hierarchical_context(self, db):
        """Test retrieving layered context."""
        # Set up context at different levels
        db.save_hierarchical_context(
            repo="test/repo", scope_type="repo",
            summary="Project overview"
        )
        db.save_hierarchical_context(
            repo="test/repo", scope_type="folder",
            scope_path="src/auth", summary="Auth module"
        )
        db.save_hierarchical_context(
            repo="test/repo", scope_type="file",
            scope_path="src/auth/login.py", summary="Login file"
        )
        
        # Query with current files
        context = db.get_hierarchical_context(
            repo="test/repo",
            current_files=["src/auth/login.py"]
        )
        
        assert context["repo"] is not None
        assert context["repo"]["summary"] == "Project overview"
        assert len(context["folders"]) == 1
        assert len(context["files"]) == 1

    def test_context_upsert(self, db):
        """Test that saving context updates existing entry."""
        db.save_hierarchical_context(
            repo="test/repo", scope_type="repo",
            summary="Original summary"
        )
        db.save_hierarchical_context(
            repo="test/repo", scope_type="repo",
            summary="Updated summary"
        )
        
        context = db.get_hierarchical_context(repo="test/repo")
        
        assert context["repo"]["summary"] == "Updated summary"

    def test_get_context_for_path(self, db):
        """Test getting context for a specific file path."""
        db.save_hierarchical_context(
            repo="test/repo", scope_type="repo",
            summary="Project"
        )
        db.save_hierarchical_context(
            repo="test/repo", scope_type="folder",
            scope_path="src", summary="Source"
        )
        db.save_hierarchical_context(
            repo="test/repo", scope_type="folder",
            scope_path="src/api", summary="API layer"
        )
        
        context = db.get_context_for_path(
            repo="test/repo",
            file_path="src/api/routes.py"
        )
        
        assert context["repo"] is not None
        # Should find both src and src/api folders
        assert len(context["folders"]) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
