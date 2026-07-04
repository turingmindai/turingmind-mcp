import os
import tempfile
import pytest
import json
from unittest.mock import patch, MagicMock

# Ensure turingmind_mcp is importable
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from turingmind_mcp.v2_engine.models import (
    SpecNode, NodeLevel, SurfaceType, ExecutionStage, SpecStatus, FailureClassification
)
from turingmind_mcp.v2_engine.database import init_db, save_spec_node, get_spec_node
from turingmind_mcp.v2_engine.handlers import handle_run_verification
from turingmind_mcp.tools.context import ToolContext

@pytest.fixture
def temp_env(tmp_path):
    # Patch the v2 database path
    v2_db_path = tmp_path / "v2_memory.db"
    
    # Patch the legacy database path
    legacy_db_path = tmp_path / "legacy.db"
    
    with patch("turingmind_mcp.v2_engine.database.DB_PATH", str(v2_db_path)), \
         patch("turingmind_mcp.v2_engine.database.DB_DIR", str(tmp_path)), \
         patch("turingmind_mcp.v2_engine.handlers._memory_db_instance", None):
        
        # Initialize databases
        init_db()
        
        # Mock legacy database to avoid sqlite connection issues
        from turingmind_mcp.database import MemoryDatabase
        legacy_db = MemoryDatabase(str(legacy_db_path))
        
        with patch("turingmind_mcp.v2_engine.handlers._get_memory_db", return_value=legacy_db):
            yield legacy_db
            legacy_db.close()

@pytest.mark.asyncio
async def test_verification_failure_auto_classification(temp_env):
    legacy_db = temp_env
    
    # 1. Create a spec node and save it in v2 database
    node = SpecNode(
        id="node_123",
        repo="test_repo",
        level=NodeLevel.L1_FILE,
        surface_type=SurfaceType.INTERNAL,
        title="Test Node",
        description="A test spec node",
        dependencies=[],
        dependents=[]
    )
    home = os.path.expanduser('~')
    workspace_dir = f"{home}/mock_workspace"
    lib_file = f"{workspace_dir}/lib.py"
    test_lib_file = f"{workspace_dir}/test_lib.py"
    test_dir = f"{workspace_dir}/tests"
    python_bin = f"{workspace_dir}/.venv/bin/python"

    node.implementation.files = [lib_file, test_lib_file]
    node.verification.unit_tests = ["test_lib.py"]
    save_spec_node(node)
    
    # 2. Mock subprocess.run to simulate a failing pytest run
    mock_subprocess_result = MagicMock()
    mock_subprocess_result.returncode = 1
    mock_subprocess_result.stdout = "def test_fail():\n>       assert False\nE       AssertionError: assert False\n\n=========================== 1 failed in 0.05s ==========================="
    mock_subprocess_result.stderr = ""
    
    # 3. Call handle_run_verification with a mocked subprocess.run
    class StubContext:
        logger = MagicMock()
        get_db = lambda self: legacy_db
        
    ctx = StubContext()
    
    with patch("subprocess.run", return_value=mock_subprocess_result), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_file", return_value=True):
        
        result_text_list = await handle_run_verification({
            "node_id": "node_123",
            "test_dir": test_dir,
            "python_bin": python_bin
        }, ctx)
        
        # Parse the JSON response
        assert len(result_text_list) == 1
        print("RESULT TEXT:", result_text_list[0].text)
        response = json.loads(result_text_list[0].text)
        
        assert response["status"] == "failed"
        assert response["node_id"] == "node_123"
        assert response["passed"] == 0
        assert response["failed"] == 1
        
        # Verify it auto-classified to SPEC_GAP (Rule 4: contract empty and trace has assert/invariant/contract)
        assert "classification" in response
        assert response["classification"] == FailureClassification.SPEC_GAP.value
        
        # Verify the database node was updated to FAILED and has the correct classification
        updated_node = get_spec_node("node_123")
        assert updated_node.state.status == SpecStatus.FAILED
        assert updated_node.state.failure_classification == FailureClassification.SPEC_GAP
        assert "AssertionError" in updated_node.state.failure_trace
        
        # Verify a durable memory entry was created in the legacy database
        memories = legacy_db.list_memory_entries(repo="test_repo")
        assert len(memories) == 1
        assert memories[0]["node_id"] == "node_123"
        assert "Verification failure on 'Test Node' classified as spec_gap" in memories[0]["content"]


@pytest.mark.asyncio
async def test_manual_failure_record_auto_classification(temp_env):
    legacy_db = temp_env
    
    # 1. Create a spec node and save it in v2 database
    node = SpecNode(
        id="node_abc",
        repo="test_repo",
        level=NodeLevel.L1_FILE,
        surface_type=SurfaceType.INTERNAL,
        title="Manual Test Node",
        description="A test spec node for manual failure",
        dependencies=[],
        dependents=[]
    )
    node.state.failure_trace = "invariant violated in assertion"
    node.verification.unit_tests = ["test_manual.py"]
    save_spec_node(node)
    
    # 2. Call handle_record_execution_stage manually with status="failed"
    from turingmind_mcp.v2_engine.handlers import handle_record_execution_stage
    
    class StubContext:
        logger = MagicMock()
        get_db = lambda self: legacy_db
        
    ctx = StubContext()
    
    result_text_list = await handle_record_execution_stage({
        "node_id": "node_abc",
        "stage": "auditing",
        "status": "failed",
        "confidence": 0.3
    }, ctx)
    
    # Parse the JSON response
    assert len(result_text_list) == 1
    response = json.loads(result_text_list[0].text)
    assert response["status"] == "recorded"
    
    # Verify the database node was updated to FAILED and has the correct classification
    updated_node = get_spec_node("node_abc")
    assert updated_node.state.status == SpecStatus.FAILED
    assert updated_node.state.failure_classification == FailureClassification.SPEC_GAP
    
    # Verify a durable memory entry was created in the legacy database
    memories = legacy_db.list_memory_entries(repo="test_repo")
    assert len(memories) == 1
    assert memories[0]["node_id"] == "node_abc"
    assert "Verification failure on 'Manual Test Node' classified as spec_gap" in memories[0]["content"]

    # 3. Call handle_record_execution_stage AGAIN to check de-duplication
    # We will override the classification to test_gap using handle_classify_failure
    from turingmind_mcp.v2_engine.handlers import handle_classify_failure
    classify_result = await handle_classify_failure({
        "node_id": "node_abc",
        "classification": "test_gap",
        "failure_trace": "different failure"
    }, ctx)
    
    # Verify the memory entry was updated, not duplicated
    memories = legacy_db.list_memory_entries(repo="test_repo")
    assert len(memories) == 1
    assert memories[0]["node_id"] == "node_abc"
    assert "classified as test_gap" in memories[0]["content"]


@pytest.mark.asyncio
async def test_classify_failure_idempotent_after_auto_run(temp_env):
    """Manual classify_failure with same class as auto-run must not duplicate memory."""
    legacy_db = temp_env

    node = SpecNode(
        id="node_dup",
        repo="test_repo",
        level=NodeLevel.L1_FILE,
        surface_type=SurfaceType.INTERNAL,
        title="Dup Test Node",
        description="",
        dependencies=[],
        dependents=[],
    )
    home = os.path.expanduser("~")
    workspace_dir = f"{home}/mock_workspace"
    node.implementation.files = [f"{workspace_dir}/lib.py"]
    node.verification.unit_tests = ["test_lib.py"]
    save_spec_node(node)

    class StubContext:
        logger = MagicMock()
        get_db = lambda self: legacy_db

    ctx = StubContext()
    mock_subprocess_result = MagicMock()
    mock_subprocess_result.returncode = 1
    mock_subprocess_result.stdout = "1 failed in 0.05s"
    mock_subprocess_result.stderr = "AssertionError: boom"

    with patch("subprocess.run", return_value=mock_subprocess_result), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_file", return_value=True):
        await handle_run_verification({
            "node_id": "node_dup",
            "test_dir": f"{workspace_dir}/tests",
            "python_bin": f"{workspace_dir}/.venv/bin/python",
        }, ctx)

    from turingmind_mcp.v2_engine.handlers import handle_classify_failure

    classify_result = await handle_classify_failure({
        "node_id": "node_dup",
        "classification": FailureClassification.SPEC_GAP.value,
    }, ctx)
    payload = json.loads(classify_result[0].text)
    assert payload["status"] == "already_classified"

    memories = legacy_db.list_memory_entries(repo="test_repo", memory_type="learned_pattern")
    assert len(memories) == 1
    assert memories[0]["node_id"] == "node_dup"


@pytest.mark.asyncio
async def test_mcp_list_memory_exposes_node_id_after_auto_failure(temp_env):
    """MCP list_memory/get_memory must surface node_id from auto-classified failures."""
    legacy_db = temp_env

    node = SpecNode(
        id="node_mcp",
        repo="test_repo",
        level=NodeLevel.L1_FILE,
        surface_type=SurfaceType.INTERNAL,
        title="MCP Node",
        description="",
        dependencies=[],
        dependents=[],
    )
    home = os.path.expanduser("~")
    workspace_dir = f"{home}/mock_workspace"
    node.implementation.files = [f"{workspace_dir}/lib.py"]
    node.verification.unit_tests = ["test_lib.py"]
    save_spec_node(node)

    class StubContext:
        logger = MagicMock()
        get_db = lambda self: legacy_db

    ctx = StubContext()
    mock_subprocess_result = MagicMock()
    mock_subprocess_result.returncode = 1
    mock_subprocess_result.stdout = "1 failed in 0.05s"
    mock_subprocess_result.stderr = "AssertionError"

    with patch("subprocess.run", return_value=mock_subprocess_result), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_file", return_value=True):
        await handle_run_verification({
            "node_id": "node_mcp",
            "test_dir": f"{workspace_dir}/tests",
            "python_bin": f"{workspace_dir}/.venv/bin/python",
        }, ctx)

    from turingmind_mcp.tools.memory import handle_get_memory, handle_list_memory

    listed = await handle_list_memory({"repo": "test_repo"}, ctx)
    list_payload = json.loads(listed[0].text)
    assert list_payload["total"] == 1
    assert list_payload["entries"][0]["node_id"] == "node_mcp"

    fetched = await handle_get_memory(
        {"repo": "test_repo", "memory_id": list_payload["entries"][0]["memory_id"]},
        ctx,
    )
    detail = json.loads(fetched[0].text)
    assert detail["node_id"] == "node_mcp"
