from __future__ import annotations

import pytest
import uuid
from turingmind_mcp.server import call_tool, get_db
from mcp.types import TextContent

@pytest.mark.asyncio
async def test_cp_gatekeeper_policy_interceptor(memory_db, tier_repo, monkeypatch):
    """TC-CP-31: Verify MCP tool execution prepends warnings if session drift or active findings exist."""
    
    # Override singleton db inside server module
    import turingmind_mcp.server as mcp_server
    monkeypatch.setattr(mcp_server, "_db_instance", memory_db)
    
    composer_id = f"composer-gk-{uuid.uuid4()}"
    session_id = str(uuid.uuid4())
    
    # 1. Create a clean hydrated session
    memory_db.create_coding_session(
        session_id=session_id,
        composer_id=composer_id,
        repo=tier_repo,
        branch="main",
        expires_at="2030-01-01T00:00:00",
    )
    
    # Execute a local tool (e.g. turingmind_get_spec_status)
    # The tool turingmind_get_spec_status returns tool info or a message when not found
    args = {"repo": tier_repo, "node_id": "non-existent"}
    res = await call_tool("turingmind_get_spec_status", args)
    
    # Verify no warning is prepended
    assert len(res) == 1
    assert "TM-DRIFT-002" not in res[0].text
    assert "TM-QUEUE-003" not in res[0].text
    
    # 2. Update session state to drift
    memory_db.update_coding_session(
        session_id=session_id,
        loaded_scopes=["reconcile"],
        touched_files=["src/reconcile.py"],
        touched_subsystems=["reconcile"],
        recall_history=[],
        policy_state="drift",
        expires_at="2030-01-01T00:00:00",
    )
    
    # Call tool again
    res2 = await call_tool("turingmind_get_spec_status", args)
    
    # Verify TM-DRIFT-002 warning is prepended as first TextContent
    assert len(res2) > 1
    assert "TM-DRIFT-002" in res2[0].text
    
    # 3. Create a critical unresolved finding
    memory_db.create_finding(
        repo=tier_repo,
        finding_type="security_regression",
        severity="critical",
        action="Unresolved critical SQL injection risk in query constructor.",
        dedup_key="sec-sql-inj-key",
    )
    
    # Call tool again
    res3 = await mcp_server.call_tool("turingmind_get_spec_status", args)
    
    # Verify both drift and queue warning warnings are present
    assert len(res3) > 1
    assert "TM-QUEUE-003" in res3[0].text
