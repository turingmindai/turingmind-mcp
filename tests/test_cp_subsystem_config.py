from __future__ import annotations

import json
from pathlib import Path

import pytest

from turingmind_mcp.subsystem_config import load_subsystem_map, match_subsystem


def test_load_subsystem_map_from_workspace(tmp_path: Path) -> None:
    config_dir = tmp_path / ".turingmind"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "subsystems": {
                    "auth": ["packages/auth/**"],
                    "billing": ["packages/billing/**"],
                }
            }
        ),
        encoding="utf-8",
    )

    loaded = load_subsystem_map(str(tmp_path))
    assert loaded["auth"] == ["packages/auth/**"]
    assert match_subsystem("packages/auth/login.py", loaded) == "auth"
    assert match_subsystem("packages/billing/invoice.py", loaded) == "billing"


def test_cp_subsystem_config_reduces_false_drift(api_client, tier_repo, tmp_path):
    """Configured subsystems distinguish monorepo packages; drift fires across packages."""
    client, _db = api_client
    workspace = tmp_path / "repo"
    turingmind_dir = workspace / ".turingmind"
    turingmind_dir.mkdir(parents=True)
    (turingmind_dir / "config.json").write_text(
        json.dumps(
            {
                "subsystems": {
                    "auth": ["packages/auth/**"],
                    "billing": ["packages/billing/**"],
                }
            }
        ),
        encoding="utf-8",
    )

    composer_id = "composer-subsystem-config"
    base = {
        "repo": tier_repo,
        "composer_id": composer_id,
        "workspace_root": str(workspace),
    }

    r1 = client.post(
        "/api/v2/sync",
        json={**base, "files": ["packages/auth/login.py"]},
    )
    assert r1.status_code == 200

    r2 = client.post(
        "/api/v2/sync",
        json={**base, "files": ["packages/billing/invoice.py"]},
    )
    assert r2.status_code == 200
    assert r2.json()["recall_bundle"]["policy"]["code"] == "TM-DRIFT-002"


def test_cp_subsystem_config_same_package_no_drift(api_client, tier_repo, tmp_path):
    """Repeated edits within one configured subsystem should not emit drift."""
    client, _db = api_client
    workspace = tmp_path / "repo"
    turingmind_dir = workspace / ".turingmind"
    turingmind_dir.mkdir(parents=True)
    (turingmind_dir / "config.json").write_text(
        json.dumps({"subsystems": {"auth": ["packages/auth/**"]}}),
        encoding="utf-8",
    )

    composer_id = "composer-subsystem-stable"
    payload = {
        "repo": tier_repo,
        "composer_id": composer_id,
        "workspace_root": str(workspace),
        "files": ["packages/auth/session.py"],
    }

    client.post("/api/v2/sync", json=payload)
    r2 = client.post("/api/v2/sync", json=payload)
    assert r2.status_code == 200
    assert r2.json()["recall_bundle"]["policy"]["code"] is None
