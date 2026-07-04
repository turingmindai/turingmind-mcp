"""API tests for memory pull and CI observation ingest."""

from __future__ import annotations

import os
from unittest import mock

import pytest


@pytest.mark.tier_d
def test_sync_pull_postgres_local(api_client, tier_repo, monkeypatch):
    client, db = api_client
    monkeypatch.setenv("POSTGRES_URI", "postgresql://local/test")
    monkeypatch.delenv("TURINGMIND_CLOUD_SYNC", raising=False)

    with mock.patch(
        "turingmind_mcp.cloud_memory_client.pull_memories_local",
        return_value={
            "memories_pulled": 2,
            "memories_applied": 1,
            "tombstones_applied": 1,
            "memories_pushed": 0,
            "via": "postgres",
        },
    ) as mock_pull:
        response = client.post("/api/v2/sync/pull", json={"repo": tier_repo})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pulled"
    assert body["tombstones_applied"] == 1
    mock_pull.assert_called_once()


def test_ci_observation_requires_ingest_key(api_client, tier_repo, monkeypatch):
    client, _db = api_client
    monkeypatch.setenv("TURINGMIND_INGEST_KEY", "test-ingest-secret")

    missing = client.post(
        "/api/v2/observations/ci",
        json={"repo": tier_repo, "conclusion": "failure", "check_name": "unit-tests"},
    )
    assert missing.status_code == 401

    ok = client.post(
        "/api/v2/observations/ci",
        json={"repo": tier_repo, "conclusion": "failure", "check_name": "unit-tests"},
        headers={"X-TuringMind-Ingest-Key": "test-ingest-secret"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] == "recorded"
    assert body["confidence"] == 0.65
