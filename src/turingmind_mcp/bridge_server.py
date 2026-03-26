#!/usr/bin/env python3
# TURINGMIND_PLAN=130beecb-2fce-47bd-a990-524ae2384a04
"""
TuringMind MCP Bridge Server

WebSocket server that bridges VS Code extension with MCP tools.
Allows the extension to call MCP tools and receive real-time updates.

Run with: python -m turingmind_mcp.bridge_server
Or: python3 src/turingmind_mcp/bridge_server.py
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any, Set

# Handle imports - work both when installed and when run as script
# Add src to path if not already there (for when run as script)
_this_file = Path(__file__)

# Load environment variables from .env file (after _this_file is defined)
try:
    from dotenv import load_dotenv
    _package_dir = _this_file.parent
    _src_dir = _package_dir.parent
    _project_root = _src_dir.parent
    
    # Try multiple locations for .env file
    _env_paths = [
        _project_root / ".env",
        _src_dir / ".env",
        Path.cwd() / ".env",
        Path.home() / ".turingmind" / ".env"
    ]
    
    for env_path in _env_paths:
        if env_path.exists():
            load_dotenv(env_path, override=False)
            logger = logging.getLogger("turingmind-bridge")
            logger.info(f"Loaded .env from {env_path}")
            break
    else:
        # If no .env found, try loading from current directory (for backward compatibility)
        load_dotenv(override=False)
except ImportError:
    # dotenv not installed - continue without it (will use system env vars)
    pass
except Exception as e:
    # Log but don't fail if .env loading has issues
    import logging
    logging.getLogger("turingmind-bridge").warning(f"Could not load .env: {e}")

# Now define package paths (after .env is loaded, using values from dotenv section)
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

# Create a fake turingmind_mcp module in sys.modules to satisfy relative imports
if "turingmind_mcp" not in sys.modules:
    import types
    sys.modules["turingmind_mcp"] = types.ModuleType("turingmind_mcp")
    sys.modules["turingmind_mcp"].__path__ = [str(_package_dir)]

try:
    import websockets
    # Use serve directly from websockets (works in all versions)
    websockets_serve = websockets.serve
except ImportError:
    print("websockets not installed. Run: pip install websockets")
    sys.exit(1)

# Import modules - handle both relative and absolute imports
try:
    from .database import MemoryDatabase
    from .entity_indexer import get_repo_path
except ImportError:
    # Fallback for when run as script
    import importlib.util
    
    # Load database module
    _database_path = _package_dir / "database.py"
    _database_spec = importlib.util.spec_from_file_location("turingmind_mcp.database", _database_path)
    if _database_spec and _database_spec.loader:
        _database_module = importlib.util.module_from_spec(_database_spec)
        sys.modules["turingmind_mcp.database"] = _database_module
        _database_spec.loader.exec_module(_database_module)
        MemoryDatabase = _database_module.MemoryDatabase
    else:
        raise ImportError("Could not load database module")
    
    # Load entity_indexer module
    _entity_indexer_path = _package_dir / "entity_indexer.py"
    _entity_indexer_spec = importlib.util.spec_from_file_location("turingmind_mcp.entity_indexer", _entity_indexer_path)
    if _entity_indexer_spec and _entity_indexer_spec.loader:
        _entity_indexer_module = importlib.util.module_from_spec(_entity_indexer_spec)
        sys.modules["turingmind_mcp.entity_indexer"] = _entity_indexer_module
        _entity_indexer_spec.loader.exec_module(_entity_indexer_module)
        get_repo_path = _entity_indexer_module.get_repo_path
    else:
        raise ImportError("Could not load entity_indexer module")

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.DEBUG if os.environ.get("TURINGMIND_DEBUG") else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("turingmind-bridge")

# ============================================================================
# CONFIGURATION
# ============================================================================

DEFAULT_PORT = 9876
DEFAULT_HOST = "127.0.0.1"

# ============================================================================
# STATE
# ============================================================================

# Connected clients
# Use Any for websocket connections (type varies by websockets version)
clients: Set[Any] = set()

# Database instance
_db: Optional[MemoryDatabase] = None


def get_db() -> MemoryDatabase:
    """Get or create database instance."""
    global _db
    if _db is None:
        _db = MemoryDatabase()
    return _db


# ============================================================================
# MESSAGE HANDLERS
# ============================================================================

async def handle_get_tdd_status(repo: str) -> Dict[str, Any]:
    """Get TDD status for a repository."""
    db = get_db()
    session = db.get_active_tdd_session(repo)
    
    if not session:
        return {
            "status": "no_active_session",
            "repo": repo,
            "message": "No active TDD session"
        }
    
    plan_id = session.get('plan_id')
    plan = db.get_edit_plan(plan_id) if plan_id else None
    
    phase = session.get('current_phase', 'unknown')
    
    # Get test status
    tests = db.get_required_tests_for_plan(plan_id) if plan_id else []
    pending_tests = [t for t in tests if t.get('status') == 'pending']
    written_tests = [t for t in tests if t.get('status') == 'written']
    
    return {
        "status": "active",
        "repo": repo,
        "session_id": session.get('session_id'),
        "plan_id": plan_id,
        "current_phase": phase,
        "change_intent": plan.get('change_intent') if plan else None,
        "files_affected": plan.get('files_affected') if plan else [],
        "acceptance_criteria": plan.get('acceptance_criteria') if plan else [],
        "tests": {
            "total": len(tests),
            "pending": len(pending_tests),
            "written": len(written_tests),
        }
    }


async def handle_validate_edit(
    repo: str,
    file_path: str,
    plan_id: Optional[str] = None
) -> Dict[str, Any]:
    """Validate if a file edit is allowed."""
    db = get_db()
    
    # Get active session
    session = db.get_active_tdd_session(repo)
    
    if not session:
        return {
            "allowed": False,
            "reason": "No active TDD session",
            "action": "Start a TDD cycle with turingmind_create_edit_plan"
        }
    
    # Check phase
    phase = session.get('current_phase', '')
    if phase not in ('green', 'refactor'):
        return {
            "allowed": False,
            "reason": f"Cannot edit in {phase.upper()} phase",
            "current_phase": phase,
            "action": get_phase_action(phase)
        }
    
    # Get plan
    plan_id = plan_id or session.get('plan_id')
    if not plan_id:
        return {
            "allowed": False,
            "reason": "No EditPlan associated with session"
        }
    
    plan = db.get_edit_plan(plan_id)
    if not plan:
        return {
            "allowed": False,
            "reason": "EditPlan not found"
        }
    
    # Check if file is in plan
    files_affected = plan.get('files_affected', [])
    
    # Normalize paths for comparison
    normalized_file = os.path.basename(file_path)
    file_allowed = any(
        normalized_file == os.path.basename(f) or
        file_path.endswith(f) or
        f.endswith(normalized_file)
        for f in files_affected
    )
    
    if not file_allowed:
        return {
            "allowed": False,
            "reason": f"File not in EditPlan",
            "file_path": file_path,
            "allowed_files": files_affected,
            "action": "Add this file to the EditPlan or edit an allowed file"
        }
    
    return {
        "allowed": True,
        "plan_id": plan_id,
        "phase": phase,
        "change_intent": plan.get('change_intent')
    }


async def handle_update_plan_metadata(plan_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Update plan metadata."""
    db = get_db()
    success = db.update_edit_plan_metadata(plan_id, metadata)
    if success:
        return {"status": "success", "plan_id": plan_id, "metadata": metadata}
    else:
        return {"status": "error", "error": "Plan not found or update failed"}


async def handle_get_kanban_data(repo: str, limit: int = 20) -> Dict[str, Any]:
    """Get Kanban board data - strictly filtered by repository."""
    import logging
    logger = logging.getLogger(__name__)
    
    if not repo or not repo.strip():
        logger.warning("handle_get_kanban_data called with empty repo")
        return {
            "repo": repo or "",
            "columns": {
                'planning': [],
                'spec': [],
                'red': [],
                'green': [],
                'refactor': [],
                'done': []
            },
            "phase_counts": {}
        }
    
    db = get_db()
    cursor = db.conn.cursor()
    
    # Normalize repo for exact matching (trim whitespace, ensure consistent format)
    normalized_repo = repo.strip()
    
    logger.info(f"Fetching Kanban data for repo: '{normalized_repo}' (original: '{repo}')")
    
    # Get all sessions with their plans, including metadata
    # STRICT filtering: only exact match, no NULL repos, case-sensitive
    cursor.execute("""
        SELECT 
            s.session_id,
            s.current_phase,
            s.status as session_status,
            s.created_at,
            s.updated_at,
            s.repo as session_repo,
            p.plan_id,
            p.change_intent,
            p.files_affected,
            p.status as plan_status,
            p.metadata_json
        FROM tdd_sessions s
        LEFT JOIN edit_plans p ON s.plan_id = p.plan_id
        WHERE s.repo = ? AND s.repo IS NOT NULL AND s.repo != ''
        ORDER BY s.updated_at DESC
    """, (normalized_repo,))
    
    rows = cursor.fetchall()
    
    # Debug: Log what we found
    logger.info(f"Found {len(rows)} sessions for repo '{normalized_repo}'")
    
    # Double-check: filter out any rows that don't match (defensive programming)
    matching_rows = []
    for row in rows:
        row_dict = dict(row)
        session_repo = row_dict.get('session_repo', '').strip() if row_dict.get('session_repo') else ''
        if session_repo == normalized_repo:
            matching_rows.append(row)
        else:
            logger.warning(f"Filtered out session {row_dict.get('session_id')} with mismatched repo: '{session_repo}' != '{normalized_repo}'")
    
    # Organize by phase
    phases = {
        'planning': [],
        'spec': [],
        'red': [],
        'green': [],
        'refactor': [],
        'done': []
    }
    
    for row in matching_rows:
        item = dict(row)
        phase = item.get('current_phase', 'planning')
        
        # Normalize phase name (handle legacy phase names)
        if phase == 'tests':
            phase = 'red'
        elif phase == 'implementation':
            phase = 'green'
        elif phase == 'validation':
            phase = 'refactor'
        
        # Parse JSON fields
        if item.get('files_affected'):
            try:
                item['files_affected'] = json.loads(item['files_affected'])
            except:
                pass
        
        # Parse metadata_json
        if item.get('metadata_json'):
            try:
                item['metadata'] = json.loads(item['metadata_json'])
            except:
                pass
        
        # Only add if phase is valid and under limit
        if phase in phases and len(phases[phase]) < limit:
            phases[phase].append(item)
    
    total_items = sum(len(items) for items in phases.values())
    logger.info(f"Returning {total_items} Kanban items for repo '{normalized_repo}' across {len([p for p in phases.values() if p])} phases")
    
    return {
        "repo": normalized_repo,
        "columns": phases,
        "phase_counts": {phase: len(items) for phase, items in phases.items()}
    }


async def handle_approve_file(plan_id: str, file_path: str) -> Dict[str, Any]:
    """Record file approval for editing."""
    db = get_db()
    plan = db.get_edit_plan(plan_id)
    
    if not plan:
        return {"success": False, "error": "Plan not found"}
    
    # Create edit record
    edit_id = db.create_code_edit(
        plan_id=plan_id,
        repo=plan['repo'],
        file_path=file_path,
        edit_type='modify',
        acceptance_criteria_met=[],
    )
    
    return {
        "success": True,
        "edit_id": edit_id,
        "file_path": file_path
    }


def get_phase_action(phase: str) -> str:
    """Get action hint for a phase."""
    actions = {
        'planning': 'Call turingmind_generate_spec',
        'spec': 'Call turingmind_define_required_tests',
        'red': 'Write failing tests, then call turingmind_mark_test_written',
        'green': 'Code edits allowed',
        'refactor': 'Refactor code, then call turingmind_complete_tdd_cycle',
        'done': 'TDD cycle complete'
    }
    return actions.get(phase, 'Unknown phase')


# ============================================================================
# TOOL HANDLERS (Direct implementation without MCP dependency)
# ============================================================================

import hashlib
import re
from datetime import datetime

async def handle_tool_call(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle MCP tool calls directly without MCP dependency."""
    db = get_db()
    
    # -------------------------------------------------------------------------
    # TDD/SDD Core Tools
    # -------------------------------------------------------------------------
    
    if tool_name == 'turingmind_create_edit_plan':
        repo = args.get('repo', 'local/workspace')
        change_intent = args.get('change_intent', '')
        files_affected = args.get('files_affected', [])
        acceptance_criteria = args.get('acceptance_criteria', [])
        observed_problem = args.get('observed_problem', [])
        design_decisions = args.get('design_decisions', [])
        non_goals = args.get('non_goals', [])
        metadata = args.get('metadata', None)
        
        if not change_intent:
            return {"error": "change_intent is required"}
        
        # First create a TDD session
        session_id = db.create_tdd_session(repo=repo, current_phase='planning')
        
        # Then create the edit plan linked to that session
        plan_id = db.create_edit_plan(
            repo=repo,
            session_id=session_id,
            change_intent=change_intent,
            files_affected=files_affected,
            acceptance_criteria=acceptance_criteria,
            observed_problem=observed_problem,
            design_decisions=design_decisions,
            non_goals=non_goals,
            metadata=metadata,
        )
        
        # Update session with plan_id
        cursor = db.conn.cursor()
        cursor.execute("UPDATE tdd_sessions SET plan_id = ? WHERE session_id = ?", (plan_id, session_id))
        db.conn.commit()
        
        return {
            "status": "success",
            "plan_id": plan_id,
            "session_id": session_id,
            "message": f"EditPlan created. Next: Use turingmind_generate_spec with plan_id={plan_id}",
            "next_step": "turingmind_generate_spec"
        }
    
    elif tool_name == 'turingmind_generate_spec':
        plan_id = args.get('plan_id', '')
        
        if not plan_id:
            return {"error": "plan_id is required"}
        
        plan = db.get_edit_plan(plan_id)
        if not plan:
            return {"error": f"Plan {plan_id} not found"}
        
        repo = plan.get('repo', 'local/workspace')
        
        # Generate Gherkin-style specs from acceptance criteria
        acceptance_criteria = plan.get('acceptance_criteria', [])
        specs = []
        
        for i, criterion in enumerate(acceptance_criteria):
            # Parse criterion into Given/When/Then if possible
            gherkin = generate_gherkin(criterion, plan.get('change_intent', ''))
            
            # Create specification with correct signature
            spec_id = db.create_specification(
                plan_id=plan_id,
                repo=repo,
                title=criterion[:100],  # Use criterion as title
                gherkin_spec=gherkin,
                requirements=[{"criterion": criterion, "index": i+1}],
            )
            specs.append({"spec_id": spec_id, "gherkin": gherkin})
        
        # Update session phase
        session = db.get_active_tdd_session(repo)
        if session:
            db.update_tdd_session_phase(session['session_id'], 'spec')
        
        return {
            "status": "success",
            "plan_id": plan_id,
            "specs_generated": len(specs),
            "specs": specs,
            "message": "Specs generated. Next: Define required tests with turingmind_define_required_tests",
            "next_step": "turingmind_define_required_tests"
        }
    
    elif tool_name == 'turingmind_get_tdd_status':
        repo = args.get('repo', 'local/workspace')
        return await handle_get_tdd_status(repo)
    
    # -------------------------------------------------------------------------
    # Auto-Plan Tools (for continuous tracking)
    # -------------------------------------------------------------------------
    
    elif tool_name == 'turingmind_analyze_diff':
        repo = args.get('repo', 'local/workspace')
        file_path = args.get('file_path', '')
        diff = args.get('diff', '')
        context = args.get('context', '')
        
        if not diff:
            return {"error": "diff is required"}
        
        # Analyze diff to infer intent
        analysis = analyze_diff_content(diff, file_path, context)
        
        return {
            "status": "analyzed",
            "file_path": file_path,
            "intent": analysis['intent'],
            "diff_hash": analysis['diff_hash'],
            "risk_level": analysis['risk_level'],
            "changes_summary": analysis['changes_summary'],
            "suggested_specs": analysis['suggested_specs'],
            "confidence": analysis['confidence']
        }
    
    elif tool_name == 'turingmind_store_auto_plan':
        repo = args.get('repo', 'local/workspace')
        file_path = args.get('file_path', '')
        intent = args.get('intent', '')
        diff_hash = args.get('diff_hash', '')
        risk_level = args.get('risk_level', 'low')
        changes_summary = args.get('changes_summary', '')
        suggested_specs = args.get('suggested_specs', [])
        confidence = args.get('confidence', 0.5)
        
        if not file_path or not intent:
            return {"error": "file_path and intent are required"}
        
        # Generate auto_plan_id
        import uuid
        auto_plan_id = str(uuid.uuid4())
        
        result = db.store_auto_plan(
            auto_plan_id=auto_plan_id,
            repo=repo,
            file_path=file_path,
            diff_hash=diff_hash,
            intent=intent,
            changes_summary=[changes_summary] if changes_summary else None,
            suggested_specs=suggested_specs if isinstance(suggested_specs, list) else [suggested_specs] if suggested_specs else None,
            risk_level=risk_level,
            confidence=confidence,
        )
        
        return {
            "status": "success",
            "auto_plan_id": result.get('auto_plan_id', auto_plan_id),
            "message": f"Auto-plan stored for {file_path}"
        }
    
    elif tool_name == 'turingmind_get_auto_plans':
        repo = args.get('repo', 'local/workspace')
        uncommitted_only = args.get('uncommitted_only', True)
        file_path = args.get('file_path')
        
        plans = db.get_auto_plans(repo=repo, uncommitted_only=uncommitted_only, file_path=file_path)
        
        return {
            "status": "success",
            "count": len(plans),
            "plans": plans
        }
    
    elif tool_name == 'turingmind_bundle_plans_for_commit':
        repo = args.get('repo', 'local/workspace')
        commit_sha = args.get('commit_sha', '')
        files = args.get('files', [])
        
        result = db.bundle_auto_plans_for_commit(repo=repo, commit_sha=commit_sha, files=files)
        
        return result
    
    # -------------------------------------------------------------------------
    # Reasoning Capture Tools
    # -------------------------------------------------------------------------
    
    elif tool_name == 'turingmind_apply_edit':
        repo = args.get('repo', 'local/workspace')
        file_path = args.get('file_path', '')
        edit_type = args.get('edit_type', 'modify')
        reasoning = args.get('reasoning', '')
        problem_observed = args.get('problem_observed', '')
        approach = args.get('approach', '')
        alternatives_considered = args.get('alternatives_considered', [])
        old_content = args.get('old_content', '')
        new_content = args.get('new_content', '')
        
        if not file_path:
            return {"error": "file_path is required"}
        if not reasoning:
            return {"error": "reasoning is required - explain why this change is being made"}
        
        # Generate edit_id
        import uuid
        edit_id = str(uuid.uuid4())
        
        result = db.store_reasoned_edit(
            edit_id=edit_id,
            reasoning=reasoning,
            file_path=file_path,
            edit_type=edit_type,
            repo=repo,
            problem_observed=problem_observed,
            approach=approach,
            alternatives_considered=alternatives_considered if isinstance(alternatives_considered, list) else None,
            old_content=old_content,
            new_content=new_content,
        )
        
        return {
            "status": "success",
            "edit_id": result.get('edit_id', edit_id),
            "message": f"Reasoned edit recorded for {file_path}",
            "reasoning_captured": True,
            "proceed_with_edit": True
        }
    
    elif tool_name == 'turingmind_log_reasoning':
        repo = args.get('repo', 'local/workspace')
        reasoning_type = args.get('reasoning_type', 'observation')
        content = args.get('content', '')
        context = args.get('context', '')
        related_files = args.get('related_files', [])
        confidence = args.get('confidence', 0.8)
        
        if not content:
            return {"error": "content is required"}
        
        # Generate log_id
        import uuid
        log_id = str(uuid.uuid4())
        
        result = db.log_reasoning(
            log_id=log_id,
            reasoning_type=reasoning_type,
            content=content,
            repo=repo,
            context=context,
            related_files=related_files if isinstance(related_files, list) else None,
            confidence=confidence,
        )
        
        return {
            "status": "success",
            "log_id": result.get('log_id', log_id),
            "message": "Reasoning logged successfully"
        }
    
    elif tool_name == 'turingmind_get_reasoning_logs':
        repo = args.get('repo', 'local/workspace')
        limit = args.get('limit', 50)
        session_id = args.get('session_id')
        
        logs = db.get_reasoning_logs(
            repo=repo,
            session_id=session_id,
            limit=limit,
        )
        
        # Filter by reasoning_type if provided (do it in Python since DB doesn't have it)
        reasoning_type = args.get('reasoning_type')
        if reasoning_type:
            logs = [log for log in logs if log.get('reasoning_type') == reasoning_type]
        
        return {
            "status": "success",
            "count": len(logs),
            "logs": logs
        }
    
    # -------------------------------------------------------------------------
    # Chat Analysis Tools
    # -------------------------------------------------------------------------
    
    elif tool_name == 'turingmind_store_chat_analysis_plan':
        repo = args.get('repo', 'local/workspace')
        composer_id = args.get('composer_id', '')
        thread_name = args.get('thread_name')
        metadata = args.get('metadata')  # JSON object with userPrompts, reasoning, etc.
        summary = args.get('summary')
        created_at = args.get('created_at')
        
        if not repo or not composer_id:
            return {"error": "repo and composer_id are required"}
        
        import time
        import hashlib
        
        # Generate plan ID
        plan_id = f"CAP-{int(time.time() * 1000)}-{hashlib.md5(f'{repo}{composer_id}'.encode()).hexdigest()[:8]}"
        
        result = db.store_chat_analysis_plan(
            plan_id=plan_id,
            repo=repo,
            composer_id=composer_id,
            thread_name=thread_name,
            metadata=metadata,
            summary=summary,
            created_at=created_at,
        )
        
        # Add metadata stats to response
        if metadata:
            result["metadata_stats"] = {
                "user_prompts": len(metadata.get("userPrompts", [])),
                "reasoning_blocks": len(metadata.get("reasoning", [])),
                "assistant_responses": len(metadata.get("assistantResponses", []))
            }
        
        return result
    
    elif tool_name == 'turingmind_get_chat_analysis_plans':
        repo = args.get('repo', 'local/workspace')
        composer_id = args.get('composer_id')
        limit = args.get('limit', 50)
        offset = args.get('offset', 0)
        
        if not repo:
            return {"error": "repo is required"}
        
        # Database returns {"status": "success", "plans": [...], "total": N}
        result = db.get_chat_analysis_plans(
            repo=repo,
            composer_id=composer_id,
            limit=limit,
            offset=offset,
        )
        
        # Return the database result directly (it already has correct structure)
        return result
    
    # -------------------------------------------------------------------------
    # Task Lifecycle Management Tools
    # -------------------------------------------------------------------------
    
    elif tool_name == 'turingmind_get_tasks':
        repo = args.get('repo', 'local/workspace')
        exclude_phases = args.get('exclude_phases', ['done', 'abandoned'])
        include_stale = args.get('include_stale', False)
        limit = args.get('limit', 50)
        
        if not repo:
            return {"error": "repo is required"}
        
        tasks = db.get_active_tasks(
            repo=repo,
            exclude_phases=exclude_phases,
            limit=limit
        )
        
        # Optionally include stale tasks
        stale_tasks = []
        if include_stale:
            stale_tasks = db.get_stale_tasks(repo=repo, stale_hours=48, limit=10)
        
        # Group tasks by phase for easier display
        tasks_by_phase = {}
        for task in tasks:
            phase = task.get('current_phase', 'mentioned')
            if phase not in tasks_by_phase:
                tasks_by_phase[phase] = []
            tasks_by_phase[phase].append(task)
        
        return {
            "status": "success",
            "tasks": tasks,
            "tasks_by_phase": tasks_by_phase,
            "stale_tasks": stale_tasks,
            "total": len(tasks)
        }
    
    elif tool_name == 'turingmind_get_task':
        task_id = args.get('task_id')
        include_history = args.get('include_history', True)
        
        if not task_id:
            return {"error": "task_id is required"}
        
        task = db.get_task_by_id(task_id)
        if not task:
            return {"error": f"Task not found: {task_id}"}
        
        result = {"status": "success", "task": task}
        
        if include_history:
            transitions = db.get_task_transitions(task_id)
            result["transitions"] = transitions
        
        return result
    
    elif tool_name == 'turingmind_update_task_phase':
        task_id = args.get('task_id')
        to_phase = args.get('to_phase')
        evidence = args.get('evidence', '')
        session_id = args.get('session_id')
        
        if not task_id or not to_phase:
            return {"error": "task_id and to_phase are required"}
        
        valid_phases = ['mentioned', 'planned', 'in_progress', 'implemented', 'tested', 'done', 'blocked', 'abandoned']
        if to_phase not in valid_phases:
            return {"error": f"Invalid phase: {to_phase}. Must be one of: {', '.join(valid_phases)}"}
        
        result = db.apply_task_transition(
            task_id=task_id,
            to_phase=to_phase,
            evidence=evidence,
            session_id=session_id
        )
        
        return result
    
    elif tool_name == 'turingmind_create_task':
        repo = args.get('repo', 'local/workspace')
        description = args.get('description')
        initial_phase = args.get('initial_phase', 'mentioned')
        related_files = args.get('related_files', [])
        priority = args.get('priority', 'medium')
        source_session_id = args.get('source_session_id')
        
        if not description:
            return {"error": "description is required"}
        
        valid_phases = ['mentioned', 'planned', 'in_progress', 'implemented', 'tested', 'blocked']
        if initial_phase not in valid_phases:
            initial_phase = 'mentioned'
        
        # Check for duplicates
        similar = db.find_similar_tasks(repo, description, threshold=0.7)
        if similar:
            return {
                "status": "duplicate_found",
                "message": f"Similar task already exists: {similar[0].get('id')}",
                "similar_task": similar[0]
            }
        
        result = db.create_task(
            repo=repo,
            description=description,
            initial_phase=initial_phase,
            related_files=related_files,
            source_session_id=source_session_id,
            priority=priority
        )
        
        return result
    
    elif tool_name == 'turingmind_get_rolling_context':
        repo = args.get('repo', 'local/workspace')
        current_composer_id = args.get('current_composer_id')
        window_hours = args.get('window_hours', 48)
        max_sessions = args.get('max_sessions', 5)
        
        if not repo:
            return {"error": "repo is required"}
        
        summaries = db.get_rolling_context(
            repo=repo,
            current_composer_id=current_composer_id,
            window_hours=window_hours,
            max_sessions=max_sessions
        )
        
        return {
            "status": "success",
            "summaries": summaries,
            "total": len(summaries)
        }
    
    # -------------------------------------------------------------------------
    # Pending Tasks (Opt-in Task Creation)
    # -------------------------------------------------------------------------
    
    elif tool_name == 'turingmind_get_pending_tasks':
        repo = args.get('repo', 'local/workspace')
        limit = args.get('limit', 20)
        
        pending = db.get_pending_tasks(repo=repo, limit=limit)
        count = db.get_pending_task_count(repo=repo)
        
        return {
            "status": "success",
            "pending_tasks": pending,
            "total": count
        }
    
    elif tool_name == 'turingmind_approve_task':
        pending_id = args.get('pending_id')
        override_phase = args.get('override_phase')
        
        if not pending_id:
            return {"error": "pending_id is required"}
        
        result = db.approve_pending_task(
            pending_id=pending_id,
            override_phase=override_phase
        )
        
        return result
    
    elif tool_name == 'turingmind_reject_task':
        pending_id = args.get('pending_id')
        reason = args.get('reason')
        
        if not pending_id:
            return {"error": "pending_id is required"}
        
        result = db.reject_pending_task(
            pending_id=pending_id,
            reason=reason
        )
        
        return result
    
    elif tool_name == 'turingmind_bulk_review_tasks':
        actions = args.get('actions', [])
        
        results = []
        for action in actions:
            pending_id = action.get('pending_id')
            decision = action.get('decision')  # 'approve' or 'reject'
            
            if decision == 'approve':
                result = db.approve_pending_task(
                    pending_id=pending_id,
                    override_phase=action.get('override_phase')
                )
            elif decision == 'reject':
                result = db.reject_pending_task(
                    pending_id=pending_id,
                    reason=action.get('reason')
                )
            else:
                result = {"error": f"Invalid decision: {decision}"}
            
            results.append({"pending_id": pending_id, "result": result})
        
        return {
            "status": "success",
            "results": results,
            "approved": sum(1 for r in results if r.get("result", {}).get("status") == "approved"),
            "rejected": sum(1 for r in results if r.get("result", {}).get("status") == "rejected")
        }
    
    # -------------------------------------------------------------------------
    # Hierarchical Context
    # -------------------------------------------------------------------------
    
    elif tool_name == 'turingmind_get_hierarchical_context':
        repo = args.get('repo', 'local/workspace')
        current_files = args.get('current_files', [])
        window_hours = args.get('window_hours', 168)
        
        context = db.get_hierarchical_context(
            repo=repo,
            current_files=current_files,
            window_hours=window_hours
        )
        
        return {
            "status": "success",
            "context": context,
            "has_repo_context": context.get("repo") is not None,
            "folder_count": len(context.get("folders", [])),
            "file_count": len(context.get("files", []))
        }
    
    elif tool_name == 'turingmind_save_hierarchical_context':
        repo = args.get('repo', 'local/workspace')
        scope_type = args.get('scope_type')
        scope_path = args.get('scope_path')
        summary = args.get('summary')
        key_facts = args.get('key_facts', [])
        patterns = args.get('patterns', [])
        
        if not scope_type or not summary:
            return {"error": "scope_type and summary are required"}
        
        result = db.save_hierarchical_context(
            repo=repo,
            scope_type=scope_type,
            scope_path=scope_path,
            summary=summary,
            key_facts=key_facts,
            patterns=patterns
        )
        
        return result
    
    elif tool_name == 'turingmind_check_exchanges':
        repo = args.get('repo', 'local/workspace')
        session_start_time = args.get('session_start_time')
        workspace_root = args.get('workspace_root')
        
        try:
            # Use same import pattern as in handle_message
            try:
                from .chat_capture import check_exchanges
            except ImportError:
                from turingmind_mcp.chat_capture import check_exchanges
            db = get_db()
            result = await check_exchanges(db, repo, session_start_time)
            return result
        except Exception as e:
            logger.exception(f"Error checking exchanges: {e}")
            return {"error": f"Failed to check exchanges: {str(e)}"}
    
    elif tool_name == 'turingmind_capture_exchange':
        composer_id = args.get('composer_id', '')
        exchange_state = args.get('exchange_state', {})
        should_enhance_llm = args.get('should_enhance_llm', True)
        is_update = args.get('is_update', False)
        repo = args.get('repo', 'local/workspace')
        session_start_time = args.get('session_start_time')
        workspace_root = args.get('workspace_root')
        
        if not composer_id:
            return {"error": "composer_id is required"}
        
        try:
            # Use same import pattern as in handle_message
            try:
                from .chat_capture import capture_exchange
            except ImportError:
                from turingmind_mcp.chat_capture import capture_exchange
            db = get_db()
            
            # Pass handle_tool_call function to capture_exchange
            result = await capture_exchange(
                db,
                composer_id,
                exchange_state,
                should_enhance_llm,
                is_update,
                repo,
                handle_tool_call,  # Pass the function
                session_start_time,
                workspace_root
            )
            return result
        except Exception as e:
            logger.exception(f"Error capturing exchange: {e}")
            return {"error": f"Failed to capture exchange: {str(e)}"}
    
    elif tool_name == 'turingmind_get_chat_capture_state':
        composer_id = args.get('composer_id', '')
        
        if not composer_id:
            return {"error": "composer_id is required"}
        
        try:
            db = get_db()
            state = db.get_chat_capture_state(composer_id)
            if state:
                # Convert sets to arrays for JSON serialization
                state['kanbanItemHashes'] = list(state.get('kanbanItemHashes', set()))
                state['processedFiles'] = list(state.get('processedFiles', set()))
                return state
            else:
                return None
        except Exception as e:
            logger.exception(f"Error getting chat capture state: {e}")
            return {"error": f"Failed to get chat capture state: {str(e)}"}
    
    # -------------------------------------------------------------------------
    # Features Board Endpoints
    # -------------------------------------------------------------------------
    
    elif tool_name == 'get_features':
        repo = args.get('repo', 'local/workspace')
        status_filter = args.get('status')  # Optional status filter
        if not repo:
            return {"error": "repo is required"}
        
        features = db.get_features(repo)
        
        # Filter by status if provided
        if status_filter:
            features = [f for f in features if f.get('status') == status_filter]
        
        # Organize by domain (frontend, backend, database, infrastructure, testing, documentation, other)
        columns = {
            'frontend': [],
            'backend': [],
            'database': [],
            'infrastructure': [],
            'testing': [],
            'documentation': [],
            'other': []
        }
        
        for feature in features:
            domain = feature.get('domain', 'other')
            if domain not in columns:
                domain = 'other'
            columns[domain].append(feature)
        
        # Status counts for filters
        status_counts = {
            'backlog': len([f for f in features if f.get('status') == 'backlog']),
            'in_progress': len([f for f in features if f.get('status') == 'in_progress']),
            'blocked': len([f for f in features if f.get('status') == 'blocked']),
            'complete': len([f for f in features if f.get('status') == 'complete'])
        }
        
        return {
            "repo": repo,
            "columns": columns,
            "status_counts": status_counts,
            "domain_counts": {
                domain: len(features_list)
                for domain, features_list in columns.items()
            },
            "features": features
        }
    
    elif tool_name == 'create_feature':
        repo = args.get('repo', 'local/workspace')
        feature_name = args.get('feature_name')
        description = args.get('description')
        priority = args.get('priority')
        target_release = args.get('target_release')
        domain = args.get('domain')  # Optional domain parameter
        
        if not repo or not feature_name:
            return {"error": "repo and feature_name are required"}
        
        try:
            feature_id = db.create_feature(
                repo=repo,
                feature_name=feature_name,
                description=description,
                priority=priority,
                target_release=target_release,
                domain=domain
            )
            
            return {
                "status": "created",
                "feature_id": feature_id
            }
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"Error creating feature: {e}")
            return {"error": f"Failed to create feature: {str(e)}"}
    
    elif tool_name == 'get_feature_details':
        feature_id = args.get('feature_id')
        if not feature_id:
            return {"error": "feature_id is required"}
        
        feature = db.get_feature(feature_id)
        if not feature:
            return {"error": "Feature not found"}
        
        tasks = db.get_tasks_for_feature(feature_id)
        
        return {
            "status": "success",
            "feature": feature,
            "tasks": tasks
        }
    
    elif tool_name == 'link_task_to_feature':
        feature_id = args.get('feature_id')
        plan_id = args.get('plan_id')
        link_type = args.get('link_type', 'implements')
        
        if not feature_id or not plan_id:
            return {"error": "feature_id and plan_id are required"}
        
        link_id = db.create_feature_task_link(feature_id, plan_id, link_type)
        
        # Recalculate feature metrics
        db.recalculate_feature_metrics(feature_id)
        
        return {
            "status": "linked",
            "link_id": link_id
        }
    
    elif tool_name == 'unlink_task_from_feature':
        feature_id = args.get('feature_id')
        plan_id = args.get('plan_id')
        
        if not feature_id or not plan_id:
            return {"error": "feature_id and plan_id are required"}
        
        success = db.delete_feature_task_link(feature_id, plan_id)
        
        if success:
            # Recalculate feature metrics
            db.recalculate_feature_metrics(feature_id)
            return {"status": "unlinked"}
        else:
            return {"error": "Link not found"}
    
    elif tool_name == 'update_feature':
        feature_id = args.get('feature_id')
        updates = args.get('updates', {})
        
        if not feature_id:
            return {"error": "feature_id is required"}
        
        try:
            success = db.update_feature(feature_id, updates)
            
            if success:
                return {"status": "updated", "feature_id": feature_id}
            else:
                return {"error": "Feature not found or update failed"}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"Error updating feature: {e}")
            return {"error": f"Failed to update feature: {str(e)}"}
    
    elif tool_name == 'update_feature_status':
        feature_id = args.get('feature_id')
        status = args.get('status')
        
        if not feature_id or not status:
            return {"error": "feature_id and status are required"}
        
        if status not in ['backlog', 'in_progress', 'blocked', 'complete']:
            return {"error": "Invalid status. Must be: backlog, in_progress, blocked, or complete"}
        
        try:
            success = db.update_feature(feature_id, {'status': status})
            
            if success:
                return {"status": "updated", "feature_id": feature_id}
            else:
                return {"error": "Feature not found or update failed"}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"Error updating feature status: {e}")
            return {"error": f"Failed to update feature status: {str(e)}"}
    
    elif tool_name == 'recalculate_feature_metrics':
        feature_id = args.get('feature_id')
        
        if not feature_id:
            return {"error": "feature_id is required"}
        
        success = db.recalculate_feature_metrics(feature_id)
        
        if success:
            feature = db.get_feature(feature_id)
            return {
                "status": "recalculated",
                "feature_id": feature_id,
                "metrics": {
                    "total_tasks": feature.get('total_tasks', 0),
                    "completed_tasks": feature.get('completed_tasks', 0),
                    "in_progress_tasks": feature.get('in_progress_tasks', 0),
                    "completion_percentage": feature.get('completion_percentage', 0.0),
                    "total_estimated_days": feature.get('total_estimated_days', 0.0)
                }
            }
        else:
            return {"error": "Feature not found"}
    
    # -------------------------------------------------------------------------
    # Chat Analysis Tools
    # -------------------------------------------------------------------------
    
    elif tool_name == 'turingmind_enhance_chat_analysis':
        # Agent-based chat analysis (uses Azure OpenAI, not TuringMind API)
        logger.debug(f"turingmind_enhance_chat_analysis args keys: {list(args.keys())}")
        logger.debug(f"args type: {type(args)}, args sample: {str(args)[:500]}")
        
        user_prompts = args.get("user_prompts", [])
        assistant_responses = args.get("assistant_responses", [])
        files_discussed = args.get("files_discussed", [])
        ai_todos = args.get("ai_todos", [])
        reasoning = args.get("reasoning")
        previous_summary = args.get("previous_summary")
        file_diffs = args.get("file_diffs", [])  # List of {path, diff, size}
        
        # NEW: Rolling context and active tasks for task lifecycle tracking
        rolling_context = args.get("rolling_context", [])
        active_tasks = args.get("active_tasks", [])
        hierarchical_context = args.get("hierarchical_context", {})
        
        # Filter out responses with empty text (Cursor streaming creates many empty entries)
        assistant_responses = [r for r in assistant_responses if r.get("text", "").strip()]
        
        logger.debug(f"Extracted: {len(user_prompts)} user_prompts, {len(assistant_responses)} assistant_responses (after filtering empty)")
        
        # Need at least some content to analyze
        if not user_prompts and not assistant_responses:
            logger.warning(f"No content to analyze! user_prompts={len(user_prompts)}, assistant_responses={len(assistant_responses)}")
            logger.warning(f"Full args sample: {str(args)[:1000]}")
            return {"error": "No content to analyze: both user_prompts and assistant_responses are empty"}
        
        try:
            # Import agent and LLM config (lazy import to avoid circular dependencies)
            # Use fallback import pattern to work when run as script
            try:
                from .agents.chat_analysis_agent import ChatAnalysisAgent
                from .llm.config import get_llm_provider, get_langsmith_client
            except ImportError:
                # Fallback for when run as script - use absolute import path
                # Since we've already added src to sys.path, we can import directly
                try:
                    from turingmind_mcp.agents.chat_analysis_agent import ChatAnalysisAgent
                    from turingmind_mcp.llm.config import get_llm_provider, get_langsmith_client
                except ImportError as e2:
                    logger.error(f"Fallback import also failed: {e2}")
                    raise ImportError(f"Could not import ChatAnalysisAgent: {e2}. Make sure turingmind-mcp is installed or run from the correct directory.")
                
                # Load LLM config module
                _llm_dir = _package_dir / "llm"
                _llm_config_path = _llm_dir / "config.py"
                if _llm_config_path.exists():
                    _llm_spec = importlib.util.spec_from_file_location("turingmind_mcp.llm.config", _llm_config_path)
                    if _llm_spec and _llm_spec.loader:
                        _llm_module = importlib.util.module_from_spec(_llm_spec)
                        sys.modules["turingmind_mcp.llm.config"] = _llm_module
                        _llm_spec.loader.exec_module(_llm_module)
                        get_llm_provider = _llm_module.get_llm_provider
                        get_langsmith_client = _llm_module.get_langsmith_client
                    else:
                        raise ImportError("Could not load llm.config module")
                else:
                    raise ImportError(f"llm/config.py not found at {_llm_config_path}")
            
            # Get LLM provider
            llm_provider = get_llm_provider("azure")
            if not llm_provider:
                return {"error": "LLM provider not configured. Check Azure OpenAI settings (AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT_NAME)."}
            
            # Get LangSmith client (optional)
            langsmith_client = None
            if get_langsmith_client:
                langsmith_client = get_langsmith_client()
                if langsmith_client:
                    logger.info("LangSmith tracing enabled for ChatAnalysisAgent")
            
            # Create agent instance (matching server.py pattern)
            agent = ChatAnalysisAgent(
                llm_provider=llm_provider,
                langsmith_client=langsmith_client,
                use_heavy_task_model=False
            )
            
            # Prepare inputs for agent
            inputs = {
                "user_prompts": user_prompts,
                "assistant_responses": assistant_responses,
                "files_discussed": files_discussed,
                "ai_todos": ai_todos,
                "reasoning": reasoning,
                "previous_summary": previous_summary,
                "file_diffs": file_diffs,
                "rolling_context": rolling_context,
                "active_tasks": active_tasks,
                "hierarchical_context": hierarchical_context
            }
            
            hier_count = (1 if hierarchical_context.get("repo") else 0) + len(hierarchical_context.get("folders", [])) + len(hierarchical_context.get("files", []))
            logger.info(f"Enhancing chat analysis: {len(user_prompts)} prompts, {len(assistant_responses)} responses, "
                       f"{len(file_diffs)} file diffs, {len(rolling_context)} rolling context, {len(active_tasks)} active tasks, "
                       f"{hier_count} hierarchical context scopes")
            
            # Debug: Log sample of what we received
            if user_prompts:
                sample_prompt = user_prompts[0]
                logger.debug(f"Sample user_prompt keys: {list(sample_prompt.keys()) if isinstance(sample_prompt, dict) else type(sample_prompt)}")
                if isinstance(sample_prompt, dict):
                    logger.debug(f"Sample user_prompt text length: {len(sample_prompt.get('text', ''))}")
            
            if assistant_responses:
                sample_response = assistant_responses[0]
                logger.debug(f"Sample assistant_response keys: {list(sample_response.keys()) if isinstance(sample_response, dict) else type(sample_response)}")
                if isinstance(sample_response, dict):
                    text_len = len(sample_response.get('text', ''))
                    logger.debug(f"Sample assistant_response text length: {text_len}")
                    if text_len == 0:
                        # Try other possible keys
                        logger.debug(f"Sample response full content: {str(sample_response)[:500]}")
            
            # Execute agent
            result = await agent.execute(
                inputs=inputs,
                call_type="enhanceChatAnalysis",
                tags=["chat-analysis", "enhancement"],
                extra_metadata={
                    "is_incremental": previous_summary is not None,
                    "prompt_count": len(user_prompts),
                    "response_count": len(assistant_responses),
                    "files_count": len(files_discussed),
                    "todos_count": len(ai_todos),
                    "diffs_count": len(file_diffs),
                    "diffs_total_size": sum(d.get("size", 0) for d in file_diffs),
                    "rolling_context_count": len(rolling_context),
                    "active_tasks_count": len(active_tasks)
                }
            )
            
            # Return result as JSON-serializable dict
            return {"status": "success", "result": result}
            
        except ImportError as e:
            logger.error(f"Failed to import ChatAnalysisAgent: {e}")
            return {"error": f"ChatAnalysisAgent not available: {str(e)}. Check that agents module is installed."}
        except Exception as e:
            logger.exception("Chat analysis enhancement failed")
            return {"error": f"Chat analysis failed: {str(e)}"}
    
    # -------------------------------------------------------------------------
    # Unknown tool
    # -------------------------------------------------------------------------
    
    else:
        return {"error": f"Unknown tool: {tool_name}"}


def generate_gherkin(criterion: str, intent: str) -> str:
    """Generate Gherkin-style spec from acceptance criterion."""
    # Simple template-based generation
    return f"""Feature: {intent}

  Scenario: {criterion}
    Given the system is in a known state
    When the change is applied
    Then {criterion.lower()}
"""


def analyze_diff_content(diff: str, file_path: str, context: str = '') -> Dict[str, Any]:
    """Analyze diff content to infer intent and risk."""
    lines = diff.split('\n')
    
    additions = [l for l in lines if l.startswith('+') and not l.startswith('+++')]
    deletions = [l for l in lines if l.startswith('-') and not l.startswith('---')]
    
    # Infer intent from patterns
    intent_patterns = [
        (r'import|require|from\s+\S+\s+import', 'Adding/modifying imports'),
        (r'async|await|Promise', 'Async operation changes'),
        (r'try|catch|except|finally', 'Error handling changes'),
        (r'def\s+\w+|function\s+\w+|const\s+\w+\s*=', 'Function/method changes'),
        (r'class\s+\w+', 'Class definition changes'),
        (r'if|else|elif|switch|case', 'Control flow changes'),
        (r'return|yield', 'Return value changes'),
        (r'config|settings|env|\.env', 'Configuration changes'),
        (r'test|spec|describe|it\(|expect', 'Test-related changes'),
        (r'password|secret|key|token|auth', 'Security-sensitive changes'),
    ]
    
    intents = []
    risk_level = 'low'
    
    for pattern, description in intent_patterns:
        if re.search(pattern, diff, re.IGNORECASE):
            intents.append(description)
            if 'security' in description.lower():
                risk_level = 'high'
    
    if not intents:
        intents = ['Code modification']
    
    # Risk assessment
    if len(deletions) > 20:
        risk_level = 'medium' if risk_level == 'low' else risk_level
    if any('password' in l.lower() or 'secret' in l.lower() or 'key' in l.lower() for l in additions + deletions):
        risk_level = 'high'
    
    # Generate summary
    summary = f"+{len(additions)} -{len(deletions)} lines"
    
    # Generate diff hash
    diff_hash = hashlib.md5(diff.encode()).hexdigest()[:16]
    
    return {
        'intent': '; '.join(intents[:3]),  # Top 3 intents
        'diff_hash': diff_hash,
        'risk_level': risk_level,
        'changes_summary': summary,
        'suggested_specs': intents,
        'confidence': 0.7 if len(intents) > 1 else 0.5
    }


# ============================================================================
# WEBSOCKET HANDLER
# ============================================================================

async def handle_message(websocket: Any, message: str) -> None:
    """Handle incoming WebSocket message."""
    try:
        data = json.loads(message)
        action = data.get('action', '')
        request_id = data.get('request_id', '')
        
        tool_name_preview = data.get("tool_name", data.get("args", {}).get("tool_name", "unknown"))
        logger.debug(f"Received: {action} (id={request_id}, tool={tool_name_preview})")
        
        response: Dict[str, Any] = {"request_id": request_id, "action": action}
        
        if action == 'get_tdd_status' or action == 'get_sdd_status':
            repo = data.get('repo', '')
            if not repo:
                response['error'] = 'Missing repo parameter'
            else:
                response['data'] = await handle_get_tdd_status(repo)
        
        elif action == 'validate_edit':
            repo = data.get('repo', '')
            file_path = data.get('file_path', '')
            plan_id = data.get('plan_id')
            if not repo or not file_path:
                response['error'] = 'Missing repo or file_path parameter'
            else:
                response['data'] = await handle_validate_edit(repo, file_path, plan_id)
        
        elif action == 'get_kanban_data':
            repo = data.get('repo', '')
            limit = data.get('limit', 20)
            if not repo:
                response['error'] = 'Missing repo parameter'
            else:
                response['data'] = await handle_get_kanban_data(repo, limit)
        
        elif action == 'get_features':
            repo = data.get('repo', '')
            status_filter = data.get('status')  # Optional status filter
            domain_filter = data.get('domain')  # Optional domain filter
            if not repo:
                response['error'] = 'Missing repo parameter'
            else:
                # Call handle_tool_call with get_features tool
                try:
                    tool_args = {'repo': repo}
                    if status_filter:
                        tool_args['status'] = status_filter
                    if domain_filter:
                        tool_args['domain'] = domain_filter
                    result = await handle_tool_call('get_features', tool_args)
                    response['data'] = result
                except Exception as e:
                    logger.exception(f"Error getting features: {e}")
                    response['error'] = f"Failed to get features: {str(e)}"
        
        elif action == 'create_feature':
            repo = data.get('repo', 'local/workspace')
            feature_name = data.get('feature_name')
            description = data.get('description')
            priority = data.get('priority')
            target_release = data.get('target_release')
            domain = data.get('domain')  # Optional domain parameter
            
            if not repo or not feature_name:
                response['error'] = 'Missing repo or feature_name parameter'
            else:
                # Call handle_tool_call with create_feature tool
                try:
                    tool_args = {
                        'repo': repo,
                        'feature_name': feature_name,
                        'description': description,
                        'priority': priority,
                        'target_release': target_release,
                        'domain': domain
                    }
                    result = await handle_tool_call('create_feature', tool_args)
                    response['data'] = result
                except Exception as e:
                    logger.exception(f"Error creating feature: {e}")
                    response['error'] = f"Failed to create feature: {str(e)}"
        
        elif action == 'update_plan_metadata':
            plan_id = data.get('plan_id', '')
            metadata = data.get('metadata', {})
            if not plan_id:
                response['error'] = 'Missing plan_id parameter'
            else:
                response['data'] = await handle_update_plan_metadata(plan_id, metadata)
        
        elif action == 'get_session_by_plan':
            plan_id = data.get('plan_id', '')
            if not plan_id:
                response['error'] = 'Missing plan_id parameter'
            else:
                db = get_db()
                session = db.get_tdd_session_by_plan(plan_id)
                if session:
                    response['data'] = {'session_id': session.get('session_id')}
                else:
                    response['error'] = 'Session not found for plan'
        
        elif action == 'update_session_phase':
            session_id = data.get('session_id', '')
            new_phase = data.get('new_phase', '')
            if not session_id or not new_phase:
                response['error'] = 'Missing session_id or new_phase parameter'
            else:
                db = get_db()
                success = db.update_tdd_session_phase(session_id, new_phase)
                if success:
                    response['data'] = {'status': 'success', 'session_id': session_id, 'new_phase': new_phase}
                else:
                    response['error'] = 'Failed to update session phase'
        
        elif action == 'approve_file':
            plan_id = data.get('plan_id', '')
            file_path = data.get('file_path', '')
            if not plan_id or not file_path:
                response['error'] = 'Missing plan_id or file_path parameter'
            else:
                response['data'] = await handle_approve_file(plan_id, file_path)
        
        elif action == 'call_mcp_tool':
            tool_name = data.get('tool_name', '')
            tool_args = data.get('arguments', {})
            
            if not tool_name:
                response['error'] = 'Missing tool_name parameter'
            else:
                try:
                    # Handle tools directly without MCP dependency
                    result = await handle_tool_call(tool_name, tool_args)
                    response['data'] = result
                except Exception as e:
                    logger.exception(f"Error calling tool {tool_name}: {e}")
                    response['error'] = f"Failed to call tool: {str(e)}"
                    response['error_details'] = str(e)
        
        elif action == 'check_exchanges' or action == 'turingmind_check_exchanges':
            repo = data.get('repo', 'local/workspace')
            session_start_time = data.get('session_start_time')
            
            try:
                from .chat_capture import check_exchanges
                db = get_db()
                result = await check_exchanges(db, repo, session_start_time)
                response['data'] = result
            except Exception as e:
                logger.exception(f"Error checking exchanges: {e}")
                response['error'] = f"Failed to check exchanges: {str(e)}"
        
        elif action == 'capture_exchange' or action == 'turingmind_capture_exchange':
            composer_id = data.get('composer_id', '')
            exchange_state = data.get('exchange_state', {})
            should_enhance_llm = data.get('should_enhance_llm', True)
            is_update = data.get('is_update', False)
            repo = data.get('repo', 'local/workspace')
            session_start_time = data.get('session_start_time')
            workspace_root = data.get('workspace_root')
            
            if not composer_id:
                response['error'] = 'Missing composer_id parameter'
            else:
                try:
                    from .chat_capture import capture_exchange
                    db = get_db()
                    
                    # Pass handle_tool_call function to capture_exchange
                    result = await capture_exchange(
                        db,
                        composer_id,
                        exchange_state,
                        should_enhance_llm,
                        is_update,
                        repo,
                        handle_tool_call,  # Pass the function
                        session_start_time,
                        workspace_root
                    )
                    response['data'] = result
                    
                    # If LLM enhancement completed, broadcast event
                    if result.get('llmEnhanced') or result.get('llm_enhanced'):
                        # Get the stored plan to retrieve thread name
                        try:
                            stored_plans = db.get_chat_analysis_plans(repo=repo, composer_id=composer_id, limit=1)
                            if stored_plans.get('plans') and len(stored_plans['plans']) > 0:
                                plan = stored_plans['plans'][0]
                                summary = plan.get('summary', {})
                                thread_name = summary.get('llmThreadName') or plan.get('thread_name', 'Chat Session')
                            else:
                                thread_name = 'Chat Session'
                            
                            await broadcast_llm_enhancement_complete(repo, composer_id, thread_name)
                            logger.info(f"Broadcasted LLM completion for {composer_id[:8]}...: {thread_name}")
                        except Exception as e:
                            logger.warning(f"Failed to broadcast LLM completion: {e}")
                except Exception as e:
                    logger.exception(f"Error capturing exchange: {e}")
                    response['error'] = f"Failed to capture exchange: {str(e)}"
        
        elif action == 'get_chat_capture_state' or action == 'turingmind_get_chat_capture_state':
            composer_id = data.get('composer_id', '')
            
            if not composer_id:
                response['error'] = 'Missing composer_id parameter'
            else:
                try:
                    db = get_db()
                    state = db.get_chat_capture_state(composer_id)
                    if state:
                        # Convert sets to arrays for JSON serialization
                        state['kanbanItemHashes'] = list(state.get('kanbanItemHashes', set()))
                        state['processedFiles'] = list(state.get('processedFiles', set()))
                        response['data'] = state
                    else:
                        response['data'] = None
                except Exception as e:
                    logger.exception(f"Error getting chat capture state: {e}")
                    response['error'] = f"Failed to get chat capture state: {str(e)}"
        
        elif action == 'ping':
            response['data'] = {'pong': True}
        
        else:
            response['error'] = f'Unknown action: {action}'
        
        await websocket.send(json.dumps(response))
        
    except json.JSONDecodeError as e:
        await websocket.send(json.dumps({
            'error': f'Invalid JSON: {e}'
        }))
    except Exception as e:
        logger.exception(f"Error handling message: {e}")
        await websocket.send(json.dumps({
            'error': f'Internal error: {type(e).__name__}: {e}'
        }))


async def handler(websocket: Any) -> None:
    """Handle WebSocket connection."""
    clients.add(websocket)
    logger.info(f"Client connected. Total clients: {len(clients)}")
    
    try:
        async for message in websocket:
            await handle_message(websocket, message)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        clients.remove(websocket)
        logger.info(f"Client disconnected. Total clients: {len(clients)}")


# ============================================================================
# BROADCAST
# ============================================================================

async def broadcast(message: Dict[str, Any]) -> None:
    """Broadcast message to all connected clients."""
    if not clients:
        return
    
    message_str = json.dumps(message)
    await asyncio.gather(
        *[client.send(message_str) for client in clients],
        return_exceptions=True
    )


async def broadcast_phase_change(repo: str, session_id: str, new_phase: str) -> None:
    """Broadcast phase change to all clients."""
    await broadcast({
        'event': 'phase_change',
        'repo': repo,
        'session_id': session_id,
        'phase': new_phase
    })


async def broadcast_llm_enhancement_complete(repo: str, composer_id: str, thread_name: str) -> None:
    """Broadcast LLM enhancement completion to all clients."""
    import time
    await broadcast({
        'event': 'llm_enhancement_complete',
        'repo': repo,
        'composer_id': composer_id,
        'thread_name': thread_name,
        'timestamp': int(time.time() * 1000)
    })


# ============================================================================
# MAIN
# ============================================================================

async def main(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Run the bridge server."""
    logger.info(f"Starting TuringMind Bridge Server on {host}:{port}")
    
    async with websockets_serve(handler, host, port):
        logger.info(f"Bridge server running at ws://{host}:{port}")
        await asyncio.Future()  # Run forever


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Entry point for running the server."""
    try:
        asyncio.run(main(host, port))
    except KeyboardInterrupt:
        logger.info("Server stopped")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="TuringMind MCP Bridge Server")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host to bind to")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to bind to")
    
    args = parser.parse_args()
    run_server(args.host, args.port)
