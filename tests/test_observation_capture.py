"""Tests for Tier B observation capture helpers."""

import pytest

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.observation_capture import (
    EVENT_CHAT_EXCHANGE,
    EVENT_GIT_REVERT,
    EVENT_PRE_PUSH_HIGH,
    EVENT_VERIFICATION_SUCCESS,
    build_chat_exchange_content,
    record_chat_exchange_observation,
    record_git_revert_observation,
    record_pre_push_high_observation,
    record_verification_success_observation,
)


@pytest.fixture
def db(tmp_path):
    return MemoryDatabase(str(tmp_path / "obs.db"))


class TestBuildChatExchangeContent:
    def test_builds_from_latest_prompts(self):
        content = build_chat_exchange_content({
            "userPrompts": [{"text": "fix the auth bug"}],
            "assistantResponses": [{"text": "I updated login.py"}],
        })
        assert "user: fix the auth bug" in content
        assert "assistant: I updated login.py" in content

    def test_returns_none_when_empty(self):
        assert build_chat_exchange_content({}) is None


class TestRecordHelpers:
    def test_verification_success(self, db):
        obs_id = record_verification_success_observation(
            db,
            repo="org/repo",
            node_id="node-1",
            node_title="Auth fix",
            confidence=0.9,
            detail="3 passed, 0 failed",
        )
        assert obs_id
        rows = db.list_observations("org/repo", event_type=EVENT_VERIFICATION_SUCCESS)
        assert len(rows) == 1
        assert rows[0]["node_id"] == "node-1"
        assert "Auth fix" in rows[0]["content"]

    def test_git_revert(self, db):
        obs_id = record_git_revert_observation(
            db,
            repo="org/repo",
            commit_sha="abc123def456",
            subject='Revert "bad change"',
            files=["src/a.py", "src/b.py"],
        )
        assert obs_id
        rows = db.list_observations("org/repo", event_type=EVENT_GIT_REVERT)
        assert len(rows) == 1
        assert "abc123de" in rows[0]["content"]

    def test_pre_push_high(self, db):
        obs_id = record_pre_push_high_observation(
            db,
            repo="org/repo",
            summary_lines=["[HIGH] missing tests on node X"],
        )
        assert obs_id
        rows = db.list_observations("org/repo", event_type=EVENT_PRE_PUSH_HIGH)
        assert len(rows) == 1
        assert "HIGH gaps" in rows[0]["content"]

    def test_chat_exchange(self, db):
        obs_id = record_chat_exchange_observation(
            db,
            repo="org/repo",
            composer_id="composer-xyz",
            metadata={
                "userPrompts": [{"text": "why did tests fail?"}],
                "assistantResponses": [{"text": "missing mock"}],
            },
        )
        assert obs_id
        rows = db.list_observations("org/repo", event_type=EVENT_CHAT_EXCHANGE)
        assert len(rows) == 1
        assert rows[0]["source"] == "chat-poller"


class TestObservationCursorIsolation:
    def test_observation_cursor_does_not_touch_capture_fields(self, db):
        composer_id = "composer-iso"
        db.update_chat_capture_state(
            composer_id,
            message_count=5,
            last_captured_at=1000,
        )
        db.update_chat_capture_state(
            composer_id,
            last_observation_bubble_count=3,
            last_observation_at=2000,
            last_observation_exchange_timestamp=1500,
        )
        state = db.get_chat_capture_state(composer_id)
        assert state["messageCount"] == 5
        assert state["lastCapturedAt"] == 1000
        assert state["lastObservationBubbleCount"] == 3
        assert state["lastObservationAt"] == 2000
