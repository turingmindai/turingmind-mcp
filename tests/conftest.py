"""Shared fixtures for memory tier test suites."""

from __future__ import annotations

import pytest

from turingmind_mcp.database import MemoryDatabase
from turingmind_mcp.memory_manager import MemoryManager

TIER_SANDBOX_REPO = "test-org/tier-sandbox"


@pytest.fixture
def tier_repo() -> str:
    return TIER_SANDBOX_REPO


@pytest.fixture
def memory_db(tmp_path):
    db = MemoryDatabase(str(tmp_path / "memory.db"))
    yield db
    db.close()


@pytest.fixture
def api_client(memory_db):
    """FastAPI TestClient backed by an isolated memory database."""
    import turingmind_mcp.api_server as api_mod

    manager = MemoryManager(memory_db)
    previous_db = api_mod._memory_db_instance
    previous_manager = api_mod._memory_manager_instance
    api_mod._memory_db_instance = memory_db
    api_mod._memory_manager_instance = manager
    from fastapi.testclient import TestClient

    client = TestClient(api_mod.app)
    yield client, memory_db
    api_mod._memory_db_instance = previous_db
    api_mod._memory_manager_instance = previous_manager
