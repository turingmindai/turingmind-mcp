"""API tests for memory pull and CI observation ingest."""

from __future__ import annotations

import pytest


@pytest.mark.tier_d
def test_sync_pull_without_cloud_config_returns_empty(api_client, tier_repo, monkeypatch):
    client, _db = api_client
    monkeypatch.delenv("TURINGMIND_CLOUD_SYNC", raising=False)
    monkeypatch.delenv("TURINGMIND_API_URL", raising=False)
    monkeypatch.delenv("TURINGMIND_API_KEY", raising=False)

    response = client.post("/api/v2/sync/pull", json={"repo": tier_repo})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pulled"
    assert body["memories_pulled"] == 0
    assert body["tombstones_applied"] == 0
    assert body["via"] == "none"


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
        json={
            "repo": tier_repo,
            "branch": "feature/ci-test",
            "pr_number": 7,
            "head_sha": "c" * 40,
            "conclusion": "failure",
            "check_name": "unit-tests",
        },
        headers={"X-TuringMind-Ingest-Key": "test-ingest-secret"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] == "recorded"
    assert body["confidence"] == 0.65
