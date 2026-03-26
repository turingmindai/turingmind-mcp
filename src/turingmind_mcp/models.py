"""Shared Pydantic models and enums for MCP tools. Used by server and tools/*."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """Issue severity levels"""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ReviewType(str, Enum):
    """Code review types"""

    QUICK = "quick"
    DEEP = "deep"


class Issue(BaseModel):
    """A single code review issue"""

    title: str = Field(..., description="Short issue title (max 500 chars)")
    severity: Severity = Field(..., description="Issue severity: critical, high, medium, low")
    category: str = Field("bug", description="Category: security, bug, compliance, performance")
    file: str = Field(..., description="File path where issue was found")
    line: int = Field(..., ge=1, description="Line number (1-indexed)")
    description: Optional[str] = Field(None, description="Detailed description of the issue")
    cwe: Optional[str] = Field(None, description="CWE ID if security issue (e.g., CWE-79)")
    confidence: int = Field(85, ge=0, le=100, description="Confidence score 0-100")


class UploadReviewInput(BaseModel):
    """Input schema for turingmind_upload_review tool"""

    repo: str = Field(..., description="Repository identifier (owner/repo)")
    branch: Optional[str] = Field(None, description="Git branch name")
    commit: Optional[str] = Field(None, description="Git commit SHA (short or full)")
    review_type: ReviewType = Field(ReviewType.QUICK, description="Review type: quick or deep")
    issues: list[dict] = Field(default_factory=list, description="List of issues found")
    raw_content: Optional[str] = Field(None, description="Full review content as markdown")
    summary: Optional[dict] = Field(None, description="Summary with critical/high/medium/low counts")
    files_reviewed: list[dict] = Field(default_factory=list, description="Files that were reviewed")


class GetContextInput(BaseModel):
    """Input schema for turingmind_get_context tool"""

    repo: str = Field(..., description="Repository identifier (owner/repo)")


class FeedbackAction(str, Enum):
    """Actions for issue feedback"""

    FIXED = "fixed"
    DISMISSED = "dismissed"
    FALSE_POSITIVE = "false_positive"


class SubmitFeedbackInput(BaseModel):
    """Input schema for turingmind_submit_feedback tool"""

    issue_id: str = Field(..., description="Issue ID from the review")
    action: FeedbackAction = Field(..., description="Action: fixed, dismissed, or false_positive")
    repo: str = Field(..., description="Repository identifier (owner/repo)")
    file: Optional[str] = Field(None, description="File path where issue was found")
    line: Optional[int] = Field(None, description="Line number of the issue")
    pattern: Optional[str] = Field(None, description="For false_positive: pattern to remember and skip")
    reason: Optional[str] = Field(None, description="Reason for the feedback")
