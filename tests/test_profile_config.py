"""Tests for install profile (Memory vs Governed SKU)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from turingmind_mcp.profile_config import (
    PROFILE_GOVERNED,
    PROFILE_MEMORY,
    GRAPH_GAP_TYPES,
    MEMORY_QUEUE_GAP_TYPES,
    filter_decision_queue_gaps,
    default_tool_groups_for_profile,
    write_profile_env,
    _parse_env_file,
)


def test_default_tool_groups_memory() -> None:
    assert default_tool_groups_for_profile(PROFILE_MEMORY) == "login,code_intelligence"
    assert "v2_engine" not in default_tool_groups_for_profile(PROFILE_MEMORY)


def test_default_tool_groups_governed() -> None:
    groups = default_tool_groups_for_profile(PROFILE_GOVERNED)
    assert "v2_engine" in groups


def test_filter_memory_scope_excludes_graph_gaps() -> None:
    gaps = [
        {"gap_type": "orphan_node", "severity": "critical"},
        {"gap_type": "promotion_candidate", "severity": "medium"},
        {"gap_type": "memory_conflict", "severity": "high"},
    ]
    filtered = filter_decision_queue_gaps(gaps, scope=PROFILE_MEMORY)
    types = {g["gap_type"] for g in filtered}
    assert "orphan_node" not in types
    assert "promotion_candidate" in types
    assert "memory_conflict" in types


def test_filter_governed_scope_returns_all_gaps() -> None:
    gaps = [
        {"gap_type": "orphan_node", "severity": "critical"},
        {"gap_type": "promotion_candidate", "severity": "medium"},
    ]
    filtered = filter_decision_queue_gaps(gaps, scope=PROFILE_GOVERNED)
    assert len(filtered) == 2


def test_filter_memory_includes_unknown_finding_with_memory_id() -> None:
    gaps = [
        {
            "gap_type": "future_reconcile_type",
            "severity": "low",
            "memory_id": "mem-123",
        }
    ]
    filtered = filter_decision_queue_gaps(gaps, scope=PROFILE_MEMORY)
    assert len(filtered) == 1


def test_filter_memory_excludes_unknown_gap_without_memory_id() -> None:
    gaps = [{"gap_type": "security_scan_finding", "severity": "high"}]
    filtered = filter_decision_queue_gaps(gaps, scope=PROFILE_MEMORY)
    assert filtered == []


def test_filter_uses_finding_type_fallback() -> None:
    gaps = [{"finding_type": "promotion_candidate", "severity": "medium"}]
    filtered = filter_decision_queue_gaps(gaps, scope=PROFILE_MEMORY)
    assert len(filtered) == 1


@patch.dict(os.environ, {"TURINGMIND_PROFILE": "memory"}, clear=False)
def test_get_profile_from_env() -> None:
    from turingmind_mcp.profile_config import get_profile

    assert get_profile() == PROFILE_MEMORY


@patch.dict(os.environ, {}, clear=True)
def test_get_profile_defaults_to_governed() -> None:
    from turingmind_mcp import profile_config
    from turingmind_mcp.profile_config import get_profile

    with patch.object(profile_config, "_ENV_FILE", Path("/nonexistent/turingmind/env")):
        assert get_profile() == PROFILE_GOVERNED


def test_parse_env_file_supports_export_prefix(tmp_path: Path) -> None:
    env_path = tmp_path / "env"
    env_path.write_text('export TURINGMIND_PROFILE="memory"\n', encoding="utf-8")
    parsed = _parse_env_file(env_path)
    assert parsed["TURINGMIND_PROFILE"] == "memory"


def test_write_profile_env_merges_keys(tmp_path: Path, monkeypatch) -> None:
    from turingmind_mcp import profile_config

    env_path = tmp_path / "env"
    monkeypatch.setattr(profile_config, "_ENV_FILE", env_path)

    write_profile_env(PROFILE_MEMORY, mcp_python="/tmp/python3")
    first = _parse_env_file(env_path)
    assert first["TURINGMIND_PROFILE"] == PROFILE_MEMORY
    assert first["TURINGMIND_ENABLED_TOOL_GROUPS"] == "login,code_intelligence"
    assert first["TURINGMIND_MCP_PYTHON"] == "/tmp/python3"

    write_profile_env(PROFILE_GOVERNED)
    second = _parse_env_file(env_path)
    assert second["TURINGMIND_PROFILE"] == PROFILE_GOVERNED
    assert "v2_engine" in second["TURINGMIND_ENABLED_TOOL_GROUPS"]


def test_tool_config_respects_memory_profile(monkeypatch) -> None:
    from turingmind_mcp.tool_config import get_enabled_tools, is_tool_enabled

    monkeypatch.delenv("TURINGMIND_ENABLED_TOOL_GROUPS", raising=False)
    monkeypatch.setenv("TURINGMIND_PROFILE", PROFILE_MEMORY)

    enabled = get_enabled_tools()
    assert "turingmind_list_memory" in enabled
    assert "turingmind_create_spec_node" not in enabled
    assert is_tool_enabled("turingmind_get_decision_queue")
    assert not is_tool_enabled("turingmind_bootstrap_codebase")


def test_graph_and_memory_gap_sets_disjoint() -> None:
    overlap = GRAPH_GAP_TYPES & MEMORY_QUEUE_GAP_TYPES
    assert overlap == frozenset()
