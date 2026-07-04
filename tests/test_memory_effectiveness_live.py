"""Gate 2 — live memory effectiveness assessment (requires API on :8477).

Run:
  PYTHONPATH=src pytest tests/test_memory_effectiveness_live.py -v -m gate_2

Or directly:
  PYTHONPATH=src python -m turingmind_mcp.memory_effectiveness_assess --repo turingmindai/turingmind-mcp
"""

from __future__ import annotations

import os

import httpx
import pytest

from turingmind_mcp.memory_effectiveness_assess import (
    default_api_url,
    load_env_file,
    resolve_repo,
    resolve_workspace_dir,
    run_assessment,
)


def _live_api_available() -> bool:
    load_env_file()
    try:
        with httpx.Client(base_url=default_api_url(), timeout=3.0) as client:
            response = client.get("/api/v2/health")
            return response.status_code == 200
    except httpx.HTTPError:
        return False


def _resolved_repo() -> str | None:
    load_env_file()
    return resolve_repo(None, resolve_workspace_dir())


pytestmark = pytest.mark.gate_2

requires_live = pytest.mark.skipif(
    not _live_api_available(),
    reason="V2 API not running on TURINGMIND_LOCAL_API_URL (default :8477)",
)

requires_repo = pytest.mark.skipif(
    _resolved_repo() is None,
    reason="Set --repo, TURINGMIND_DEFAULT_REPO, or git remote on TURINGMIND_WORKSPACE_DIR",
)


@requires_live
@requires_repo
def test_memory_effectiveness_full_assessment():
    """Run all scorecard layers; fail only on hard failures (not warns)."""
    report = run_assessment(repo=_resolved_repo())
    failures = [layer for layer in report.layers if layer.status == "fail"]
    assert not failures, report.summary_table()


@requires_live
@requires_repo
def test_layer_chat_capture():
    report = run_assessment(repo=_resolved_repo())
    layer = next(item for item in report.layers if item.layer == "Chat capture")
    assert layer.status == "pass", layer.notes


@requires_live
@requires_repo
def test_layer_mcp_crud():
    report = run_assessment(repo=_resolved_repo())
    layer = next(item for item in report.layers if item.layer == "MCP list/save/recall")
    assert layer.status == "pass", layer.notes


@requires_live
@requires_repo
@pytest.mark.skipif(
    os.environ.get("TURINGMIND_BRANCH_MEMORY", "").strip().lower() not in ("1", "true", "yes"),
    reason="TURINGMIND_BRANCH_MEMORY=1 required",
)
def test_layer_branch_recall_explicit():
    report = run_assessment(repo=_resolved_repo())
    layer = next(item for item in report.layers if item.layer == "Branch recall (explicit branch)")
    assert layer.status == "pass", layer.notes


@requires_live
@requires_repo
@pytest.mark.skipif(
    os.environ.get("TURINGMIND_BRANCH_MEMORY", "").strip().lower() not in ("1", "true", "yes"),
    reason="TURINGMIND_BRANCH_MEMORY=1 required",
)
def test_layer_branch_recall_inferred():
    report = run_assessment(repo=_resolved_repo())
    layer = next(item for item in report.layers if item.layer == "Branch recall (inferred)")
    assert layer.status == "pass", layer.notes
