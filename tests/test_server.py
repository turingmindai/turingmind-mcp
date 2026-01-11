"""Tests for TuringMind MCP Server."""

import os
import pytest
from unittest.mock import patch, MagicMock

from turingmind_mcp.server import (
    get_config,
    get_api_url,
    save_api_key,
    DEFAULT_API_URL,
)


class TestConfiguration:
    """Tests for configuration functions."""

    def test_default_api_url(self):
        """Default API URL should be production."""
        assert DEFAULT_API_URL == "https://api.turingmind.ai"

    def test_get_api_url_from_env(self):
        """Should read API URL from environment."""
        with patch.dict(os.environ, {"TURINGMIND_API_URL": "http://localhost:3000"}):
            assert get_api_url() == "http://localhost:3000"

    def test_get_api_url_default(self):
        """Should return default when env not set."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("os.path.exists", return_value=False):
                assert get_api_url() == DEFAULT_API_URL

    def test_get_config_from_env(self):
        """Should read config from environment."""
        with patch.dict(
            os.environ,
            {
                "TURINGMIND_API_URL": "http://test.example.com",
                "TURINGMIND_API_KEY": "tmk_test_key",
            },
        ):
            api_url, api_key = get_config()
            assert api_url == "http://test.example.com"
            assert api_key == "tmk_test_key"

    def test_get_config_no_key(self):
        """Should return empty key when not configured."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("os.path.exists", return_value=False):
                api_url, api_key = get_config()
                assert api_url == DEFAULT_API_URL
                assert api_key == ""


class TestSaveApiKey:
    """Tests for save_api_key function."""

    def test_save_api_key_creates_file(self, tmp_path):
        """Should create config file with API key."""
        config_path = tmp_path / ".turingmind" / "config"
        
        with patch("turingmind_mcp.server.CONFIG_DIR", str(tmp_path / ".turingmind")):
            with patch("turingmind_mcp.server.CONFIG_PATH", str(config_path)):
                result = save_api_key("tmk_test_key_12345")
                
                assert config_path.exists()
                content = config_path.read_text()
                assert "TURINGMIND_API_KEY=tmk_test_key_12345" in content
                assert "TURINGMIND_API_URL=" in content


class TestToolSchemas:
    """Tests for tool input schemas."""

    def test_severity_enum_values(self):
        """Severity enum should have correct values."""
        from turingmind_mcp.server import Severity
        
        assert Severity.CRITICAL.value == "critical"
        assert Severity.HIGH.value == "high"
        assert Severity.MEDIUM.value == "medium"
        assert Severity.LOW.value == "low"

    def test_review_type_enum_values(self):
        """ReviewType enum should have correct values."""
        from turingmind_mcp.server import ReviewType
        
        assert ReviewType.QUICK.value == "quick"
        assert ReviewType.DEEP.value == "deep"

    def test_feedback_action_enum_values(self):
        """FeedbackAction enum should have correct values."""
        from turingmind_mcp.server import FeedbackAction
        
        assert FeedbackAction.FIXED.value == "fixed"
        assert FeedbackAction.DISMISSED.value == "dismissed"
        assert FeedbackAction.FALSE_POSITIVE.value == "false_positive"


class TestUploadReviewInput:
    """Tests for UploadReviewInput model."""

    def test_minimal_input(self):
        """Should accept minimal valid input."""
        from turingmind_mcp.server import UploadReviewInput
        
        review = UploadReviewInput(repo="owner/repo")
        assert review.repo == "owner/repo"
        assert review.issues == []
        assert review.review_type.value == "quick"

    def test_full_input(self):
        """Should accept full input."""
        from turingmind_mcp.server import UploadReviewInput, ReviewType
        
        review = UploadReviewInput(
            repo="owner/repo",
            branch="main",
            commit="abc123",
            review_type=ReviewType.DEEP,
            issues=[{"title": "Bug", "severity": "high", "file": "test.py", "line": 1}],
            summary={"critical": 0, "high": 1, "medium": 0, "low": 0},
        )
        assert review.branch == "main"
        assert review.commit == "abc123"
        assert len(review.issues) == 1


class TestSubmitFeedbackInput:
    """Tests for SubmitFeedbackInput model."""

    def test_minimal_input(self):
        """Should accept minimal valid input."""
        from turingmind_mcp.server import SubmitFeedbackInput, FeedbackAction
        
        feedback = SubmitFeedbackInput(
            issue_id="iss_abc123",
            action=FeedbackAction.FIXED,
            repo="owner/repo",
        )
        assert feedback.issue_id == "iss_abc123"
        assert feedback.action == FeedbackAction.FIXED

    def test_false_positive_with_pattern(self):
        """Should accept false positive with pattern."""
        from turingmind_mcp.server import SubmitFeedbackInput, FeedbackAction
        
        feedback = SubmitFeedbackInput(
            issue_id="iss_abc123",
            action=FeedbackAction.FALSE_POSITIVE,
            repo="owner/repo",
            pattern="db.query(sql, params)",
            reason="We use parameterized queries",
        )
        assert feedback.pattern == "db.query(sql, params)"
        assert feedback.reason == "We use parameterized queries"
