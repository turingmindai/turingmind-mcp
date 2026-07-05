"""
Tool Configuration for TuringMind MCP Server

This module defines which tool groups are enabled/disabled.
The architecture has transitioned to the v2 SpecNode Constraint DAG.

Tool Groups:
- login: Authentication flow
- code_intelligence: Index and reason about code
- v2_engine: The strict constraint DAG primitives
"""

import os
from typing import Set

# ============================================================================
# TOOL GROUP DEFINITIONS
# ============================================================================

TOOL_GROUPS = {
    "login": {
        "turingmind_initiate_login",
        "turingmind_poll_login",
    },
    "code_intelligence": {
        "turingmind_index_codebase",
        "turingmind_get_related_code",
        "turingmind_get_project_structure",
        "turingmind_get_edit_reasoning",
        "turingmind_analyze_diff",
        "turingmind_apply_edit",
        "turingmind_get_memory",
        "turingmind_save_memory",
        "turingmind_list_memory",
        "turingmind_delete_memory",
        "turingmind_detect_conflicts",
        "turingmind_resolve_conflict",
        "turingmind_log_reasoning",
        "turingmind_get_audit_trail",
        "turingmind_get_decision_queue",
    },
    "v2_engine": [
        "turingmind_create_spec_node",
        "turingmind_update_spec_node",
        "turingmind_get_spec_status",
        "turingmind_list_spec_nodes",
        "turingmind_get_ready_nodes",
        "turingmind_promote_node",
        "turingmind_generate_verification",
        "turingmind_run_verification",
        "turingmind_record_execution_stage",
        "turingmind_classify_failure",
        "turingmind_apply_fix",
        "turingmind_apply_spec_delta",
        "turingmind_get_impacted_nodes",
        "turingmind_request_approval",
        "turingmind_get_execution_state",
        "turingmind_ingest_runtime_signal",
        "turingmind_bootstrap_codebase",
        "turingmind_sync_codebase",
        "turingmind_sync_cloud",
    ],
}

MEMORY_PROFILE_GROUPS = "login,code_intelligence"
GOVERNED_PROFILE_GROUPS = "login,code_intelligence,v2_engine"

# Default: governed for existing installs; new installs set memory via turingmind-install.sh
DEFAULT_ENABLED_GROUPS = GOVERNED_PROFILE_GROUPS
FULL_ENABLED_GROUPS = GOVERNED_PROFILE_GROUPS


def get_enabled_groups() -> Set[str]:
    """Get the set of enabled tool groups from environment or profile default."""
    env_groups = os.environ.get("TURINGMIND_ENABLED_TOOL_GROUPS", "")
    
    if env_groups.lower() == "all":
        return set(TOOL_GROUPS.keys())
    
    if env_groups.lower() == "minimal":
        env_groups = DEFAULT_ENABLED_GROUPS
    
    if not env_groups:
        try:
            from .profile_config import default_tool_groups_for_profile
            env_groups = default_tool_groups_for_profile()
        except ImportError:
            env_groups = DEFAULT_ENABLED_GROUPS
    
    return {g.strip() for g in env_groups.split(",") if g.strip()}


def get_enabled_tools() -> Set[str]:
    """Get the set of enabled tool names based on enabled groups."""
    enabled_groups = get_enabled_groups()
    enabled_tools: Set[str] = set()
    
    for group_name in enabled_groups:
        if group_name in TOOL_GROUPS:
            enabled_tools.update(TOOL_GROUPS[group_name])
    
    return enabled_tools


def is_tool_enabled(tool_name: str) -> bool:
    """Check if a specific tool is enabled."""
    return tool_name in get_enabled_tools()
