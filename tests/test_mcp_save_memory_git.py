"""MCP save_memory must hydrate branch/head_sha from git context."""

from __future__ import annotations

import json
import logging

import pytest

from turingmind_mcp.memory_manager import MemoryManager
from turingmind_mcp.tools.context import ToolContext
from turingmind_mcp.tools.memory import handle_save_memory


@pytest.fixture
def mcp_ctx(memory_db, git_sandbox):
    manager = MemoryManager(memory_db)

    return ToolContext(
        client=None,
        api_url="http://test",
        headers={},
        logger=logging.getLogger("test_mcp_save_git"),
        save_api_key=lambda *_a, **_k: "",
        version="test",
        get_db=lambda: memory_db,
        get_memory_manager=lambda: manager,
        get_repo_path=lambda: str(git_sandbox),
    )


@pytest.mark.asyncio
async def test_save_learned_pattern_attaches_git(mcp_ctx, memory_db, git_sandbox, sample_git_payload):
    listed = await handle_save_memory(
        {
            "repo": "TuringMind-AI/Crediblio-backend",
            "type": "learned_pattern",
            "content": "MCP save must stamp branch and head_sha.",
            "scope": "server/lib/sdr/aiVoice",
            "confidence": 0.8,
        },
        mcp_ctx,
    )
    payload = json.loads(listed[0].text)
    assert payload["status"] == "saved"
    assert payload["branch"] == sample_git_payload["branch"]
    assert payload["head_sha"] == sample_git_payload["head"]
    assert payload["scope_tier"] in ("branch", "working_tree")

    row = memory_db.get_memory_entry(payload["memory_id"])
    assert row is not None
    assert row["branch"] == sample_git_payload["branch"]
    assert row["head_sha"] == sample_git_payload["head"]
    assert row["scope_tier"] == payload["scope_tier"]


@pytest.mark.asyncio
async def test_save_explicit_rule_attaches_git(mcp_ctx, memory_db, sample_git_payload):
    listed = await handle_save_memory(
        {
            "repo": "TuringMind-AI/Crediblio-backend",
            "type": "explicit_rule",
            "content": "Blank disclosure must stay blank.",
            "scope": "server/lib/sdr/aiVoice",
        },
        mcp_ctx,
    )
    payload = json.loads(listed[0].text)
    assert payload["branch"] == sample_git_payload["branch"]
    assert payload["head_sha"] == sample_git_payload["head"]

    row = memory_db.get_memory_entry(payload["memory_id"])
    assert row["branch"] == sample_git_payload["branch"]
    assert row["head_sha"] == sample_git_payload["head"]


@pytest.mark.asyncio
async def test_save_accepts_explicit_git_blob(mcp_ctx, memory_db):
    listed = await handle_save_memory(
        {
            "repo": "TuringMind-AI/Crediblio-backend",
            "type": "learned_pattern",
            "content": "Override git from arguments.",
            "scope": "server/lib/sdr/aiVoice",
            "git": {
                "branch": "feat/ai-voice-conversation-event-os",
                "head": "a" * 40,
                "dirty": False,
            },
        },
        mcp_ctx,
    )
    payload = json.loads(listed[0].text)
    assert payload["branch"] == "feat/ai-voice-conversation-event-os"
    assert payload["head_sha"] == "a" * 40
    assert payload["scope_tier"] == "branch"

    row = memory_db.get_memory_entry(payload["memory_id"])
    assert row["branch"] == "feat/ai-voice-conversation-event-os"
    assert row["head_sha"] == "a" * 40
    assert row["scope_tier"] == "branch"
