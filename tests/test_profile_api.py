"""REST API tests for Memory vs Governed profile behavior."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from turingmind_mcp.v2_engine.database import get_all_spec_nodes


@pytest.fixture
def unified_v2_db(memory_db, monkeypatch):
    from turingmind_mcp.v2_engine import database as v2db

    monkeypatch.setenv("TURINGMIND_DB_PATH", memory_db.db_path)
    monkeypatch.setattr(v2db, "DB_PATH", memory_db.db_path)
    return memory_db


def _graph_gaps_with_orphan() -> list[dict]:
    return [
        {
            "gap_type": "orphan_node",
            "severity": "critical",
            "node_id": "orphan-1",
            "action": "Wire dependency edges",
        }
    ]


def test_decision_queue_scope_memory_filters_graph_gaps(api_client, tier_repo):
    """scope=memory excludes SPDD graph gaps but keeps reconcile findings."""
    client, db = api_client
    db.create_finding(
        repo=tier_repo,
        finding_type="promotion_candidate",
        severity="medium",
        action="Promote repeated edit pattern",
        dedup_key=f"promo-{uuid.uuid4()}",
        memory_id=str(uuid.uuid4()),
    )

    with patch(
        "turingmind_mcp.api_server.detect_graph_gaps",
        return_value=_graph_gaps_with_orphan(),
    ):
        response = client.get(
            "/api/v2/decision-queue",
            params={"repo": tier_repo, "scope": "memory", "limit": 20},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "memory"
    types = {item.get("gap_type") for item in body["queue"]}
    assert "orphan_node" not in types
    assert "promotion_candidate" in types


def test_decision_queue_default_scope_follows_profile(api_client, tier_repo, monkeypatch):
    """Without scope param, effective scope reflects active install profile."""
    client, _ = api_client
    monkeypatch.setenv("TURINGMIND_PROFILE", "governed")

    with patch(
        "turingmind_mcp.api_server.detect_graph_gaps",
        return_value=_graph_gaps_with_orphan(),
    ):
        governed = client.get("/api/v2/decision-queue", params={"repo": tier_repo, "limit": 5})

    assert governed.status_code == 200
    assert governed.json()["scope"] == "governed"
    assert any(item["gap_type"] == "orphan_node" for item in governed.json()["queue"])

    monkeypatch.setenv("TURINGMIND_PROFILE", "memory")
    with patch(
        "turingmind_mcp.api_server.detect_graph_gaps",
        return_value=_graph_gaps_with_orphan(),
    ):
        memory = client.get("/api/v2/decision-queue", params={"repo": tier_repo, "limit": 5})

    assert memory.status_code == 200
    assert memory.json()["scope"] == "memory"
    assert memory.json()["queue"] == []


def test_bootstrap_if_empty_creates_root_node(api_client, unified_v2_db):
    """Governed onboarding endpoint bootstraps only when graph is empty."""
    client, _ = api_client
    repo = f"test-org/bootstrap-api-{uuid.uuid4().hex[:8]}"

    first = client.post(
        "/api/v2/graph/bootstrap-if-empty",
        json={"repo": repo},
    )
    assert first.status_code == 200
    payload = first.json()
    assert payload["status"] == "bootstrapped"
    assert payload["node_count"] == 1

    nodes = get_all_spec_nodes(repo)
    assert len(nodes) == 1
    assert nodes[0].id == f"bootstrap-root-{repo.replace('/', '-')}"

    second = client.post(
        "/api/v2/graph/bootstrap-if-empty",
        json={"repo": repo},
    )
    assert second.status_code == 200
    assert second.json()["status"] == "skipped"
    assert second.json()["reason"] == "graph_not_empty"


def test_bootstrap_if_empty_requires_repo(api_client):
    client, _ = api_client
    response = client.post("/api/v2/graph/bootstrap-if-empty", json={"repo": ""})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_mcp_decision_queue_honors_scope_memory(memory_db, tier_repo, monkeypatch):
    """MCP handler applies the same memory scope filter as REST."""
    from turingmind_mcp.v2_engine.handlers import handle_get_decision_queue, ToolContext

    monkeypatch.setenv("TURINGMIND_DB_PATH", memory_db.db_path)
    from turingmind_mcp.v2_engine import database as v2db

    monkeypatch.setattr(v2db, "DB_PATH", memory_db.db_path)

    with patch(
        "turingmind_mcp.v2_engine.handlers.detect_graph_gaps",
        return_value=_graph_gaps_with_orphan(),
    ):
        result = await handle_get_decision_queue(
            {"repo": tier_repo, "scope": "memory", "limit": 10},
            ToolContext(),
        )

    import json

    payload = json.loads(result[0].text)
    types = {item.get("gap_type") for item in payload["decision_queue"]}
    assert "orphan_node" not in types
