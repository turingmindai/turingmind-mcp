"""Tests for local V2 CLI helpers and pre-push evaluation."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from turingmind_mcp.v2_local_cli import (
    evaluate_pre_push,
    fetch_decision_queue,
    format_queue_markdown,
    format_queue_pop_markdown,
    queue_has_severity,
)


def test_format_queue_markdown_includes_severity_tags() -> None:
    text = format_queue_markdown({
        "total": 1,
        "queue": [{"gap_type": "orphan_node", "severity": "critical", "action": "Wire edges"}],
    })
    assert "[CRITICAL]" in text
    assert "orphan_node" in text


def test_format_queue_pop_markdown_top_item() -> None:
    text = format_queue_pop_markdown({
        "queue": [
            {"gap_type": "promotion_candidate", "severity": "medium", "node_id": "n1", "action": "Promote"},
            {"gap_type": "orphan_node", "severity": "critical", "node_id": "n2"},
        ],
    })
    assert "promotion_candidate" in text
    assert "MEDIUM" in text.upper() or "medium" in text.lower()


def test_evaluate_pre_push_governed_blocks_on_critical() -> None:
    data = {"queue": [{"gap_type": "orphan_node", "severity": "critical"}]}
    code, output = evaluate_pre_push(data, "governed")
    assert code == 1
    assert "[CRITICAL]" in output


def test_evaluate_pre_push_memory_never_blocks() -> None:
    data = {"queue": [{"gap_type": "orphan_node", "severity": "critical"}]}
    code, _ = evaluate_pre_push(data, "memory")
    assert code == 0


def test_queue_has_severity() -> None:
    data = {"queue": [{"severity": "high"}, {"severity": "low"}]}
    assert queue_has_severity(data, "high")
    assert not queue_has_severity(data, "critical")


@patch.dict(os.environ, {"TURINGMIND_PROFILE": "memory"}, clear=False)
def test_fetch_decision_queue_adds_memory_scope(monkeypatch) -> None:
    captured: dict = {}

    def fake_api_get(path: str, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"queue": [], "total": 0, "scope": "memory"}

    monkeypatch.setattr("turingmind_mcp.v2_local_cli.api_get", fake_api_get)
    fetch_decision_queue("org/repo")
    assert captured["params"]["scope"] == "memory"
