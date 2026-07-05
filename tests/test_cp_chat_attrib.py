from __future__ import annotations

import os
import sqlite3
import json
import pytest
from turingmind_mcp.chat_observation_poller import _resolve_repo_from_vscdb, _resolve_default_repo

def test_cp_chat_attrib_from_vscdb(tmp_path):
    """TC-CP-12: Verify that chat observations are attributed to the workspace repo path from history.entries."""
    db_file = tmp_path / "state.vscdb"
    
    # 1. Create a mock state.vscdb with ItemTable
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE ItemTable (key TEXT UNIQUE, value TEXT)")
    
    # We reference the README.md in this project workspace to find a valid git path
    readme_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../README.md"))
    history_value = json.dumps([
        {"editor": {"resource": f"file://{readme_path}"}}
    ])
    
    cursor.execute("INSERT INTO ItemTable (key, value) VALUES ('history.entries', ?)", (history_value,))
    conn.commit()
    conn.close()

    # 2. Test resolution from the mock DB path
    resolved_repo = _resolve_repo_from_vscdb(str(db_file))
    assert resolved_repo is not None
    # Usually it will be turingmindai/Turingmind-App or similar git origin format
    assert "/" in resolved_repo or resolved_repo == "Turingmind-App"

    # 3. Test _resolve_default_repo integration
    default_resolved = _resolve_default_repo(str(db_file))
    assert default_resolved == resolved_repo


def test_cp_chat_isolation_filter(monkeypatch):
    """Verify that chat polling filters out inactive/uncontrolled IDE composer sessions by default."""
    from turingmind_mcp.chat_observation_poller import (
        register_active_composer,
        ACTIVE_AGENT_SESSIONS,
        _check_observation_ready,
    )

    # Enable isolation mode explicitly
    monkeypatch.setenv("TURINGMIND_CHAT_POLL_ISOLATED", "1")

    # Clear active sessions
    ACTIVE_AGENT_SESSIONS.clear()

    # Simulate get_most_recently_active_composer and find_cursor_database
    monkeypatch.setattr(
        "turingmind_mcp.chat_observation_poller.find_cursor_database",
        lambda *a: "/mock/state.vscdb",
    )
    monkeypatch.setattr(
        "turingmind_mcp.chat_observation_poller.get_most_recently_active_composer",
        lambda *a: {"composerId": "composer-user-manual", "bubbleCount": 5, "lastActivityAt": 1000},
    )
    # Mock fallback to check mock works
    monkeypatch.setattr(
        "turingmind_mcp.chat_observation_poller.get_last_exchange_state",
        lambda *a: {"totalBubbles": 5, "isCompleteExchange": True},
    )

    # 1. Unregistered composer should be filtered out (return None)
    res = _check_observation_ready(None)
    assert res is None

    # 2. Registering the composer makes it pass the filter
    register_active_composer("composer-user-manual")
    # It will proceed further and try to query db (which we mock or fails gracefully)
    # We just want to check that it passed the isolation check block. We check that it didn't filter out by composer_id immediately
    # Let's mock get_chat_capture_state to check it gets called (meaning it passed the filter!)
    called_capture = False
    class MockDb:
        def get_chat_capture_state(self, comp_id):
            nonlocal called_capture
            called_capture = True
            return {"lastObservationBubbleCount": 0}

    _check_observation_ready(MockDb())
    assert called_capture is True

