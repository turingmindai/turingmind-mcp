"""Tool definitions for TuringMind MCP. Exposes get_all_tools() for server.list_tools()."""

from __future__ import annotations

from mcp.types import Tool

ALL_TOOLS: list[Tool] = [
# ─────────────────────────────────────────────────────────────
# LOGIN TOOLS (no auth required)
# ─────────────────────────────────────────────────────────────
Tool(
    name="turingmind_initiate_login",
    description=(
        "Start device code authentication flow for TuringMind. "
        "Returns a verification URL and user code. The user should open the URL "
        "in their browser and enter the code. Then call turingmind_poll_login "
        "with the device_code to complete authentication. "
        "No API key required to call this tool."
    ),
    inputSchema={"type": "object", "properties": {}, "required": []},
),
Tool(
    name="turingmind_poll_login",
    description=(
        "Poll for device code authentication completion. "
        "Call this after turingmind_initiate_login, passing the device_code. "
        "Returns the API key when authentication is complete, or 'pending' status. "
        "On success, automatically saves API key to ~/.turingmind/config. "
        "No API key required to call this tool."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "device_code": {
                "type": "string",
                "description": "Device code from turingmind_initiate_login",
            }
        },
        "required": ["device_code"],
    },
),
# ─────────────────────────────────────────────────────────────
# AUTH TOOLS
# ─────────────────────────────────────────────────────────────
Tool(
    name="turingmind_validate_auth",
    description=(
        "Validate TuringMind API key and get account information. "
        "Returns tier, quota remaining, and user info. "
        "Call this first to verify cloud features are available."
    ),
    inputSchema={"type": "object", "properties": {}, "required": []},
),
Tool(
    name="turingmind_upload_review",
    description=(
        "Upload code review results to TuringMind cloud for analytics and memory. "
        "Stores issues found, files reviewed, and review metadata. "
        "Returns review ID on success. Requires code_review:write permission."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repository identifier (owner/repo format)",
            },
            "branch": {"type": "string", "description": "Git branch name (optional)"},
            "commit": {"type": "string", "description": "Git commit SHA (optional)"},
            "review_type": {
                "type": "string",
                "enum": ["quick", "deep"],
                "default": "quick",
                "description": "Type of review performed",
            },
            "issues": {
                "type": "array",
                "description": "List of issues found during review",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Issue title"},
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "high", "medium", "low"],
                        },
                        "category": {
                            "type": "string",
                            "description": "Category: security, bug, compliance",
                        },
                        "file": {"type": "string", "description": "File path"},
                        "line": {"type": "integer", "description": "Line number"},
                        "description": {"type": "string", "description": "Details"},
                        "cwe": {"type": "string", "description": "CWE ID if applicable"},
                        "confidence": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                        },
                    },
                    "required": ["title", "severity", "file", "line"],
                },
            },
            "raw_content": {
                "type": "string",
                "description": "Full review as markdown (optional)",
            },
            "summary": {
                "type": "object",
                "description": "Summary counts",
                "properties": {
                    "critical": {"type": "integer"},
                    "high": {"type": "integer"},
                    "medium": {"type": "integer"},
                    "low": {"type": "integer"},
                },
            },
            "files_reviewed": {
                "type": "array",
                "description": "Files that were reviewed",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "lines_added": {"type": "integer"},
                        "lines_removed": {"type": "integer"},
                    },
                },
            },
        },
        "required": ["repo"],
    },
),
Tool(
    name="turingmind_get_context",
    description=(
        "Get memory context for a repository from TuringMind cloud. "
        "Returns recent open issues, hotspot files, false positive patterns, "
        "and team conventions. Use this before reviewing to avoid duplicate reports."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repository identifier (owner/repo format)",
            }
        },
        "required": ["repo"],
    },
),
# ─────────────────────────────────────────────────────────────
# FEEDBACK TOOL
# ─────────────────────────────────────────────────────────────
Tool(
    name="turingmind_submit_feedback",
    description=(
        "Submit feedback on a code review issue. Use this when user indicates "
        "an issue was fixed, should be dismissed, or is a false positive. "
        "For false positives, provide pattern and reason to improve future reviews."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "issue_id": {
                "type": "string",
                "description": "Issue ID from the review (e.g., iss_abc123)",
            },
            "action": {
                "type": "string",
                "enum": ["fixed", "dismissed", "false_positive"],
                "description": "Feedback action type",
            },
            "repo": {
                "type": "string",
                "description": "Repository identifier (owner/repo)",
            },
            "file": {
                "type": "string",
                "description": "File path where issue was found (optional)",
            },
            "line": {
                "type": "integer",
                "description": "Line number of the issue (optional)",
            },
            "pattern": {
                "type": "string",
                "description": "For false_positive: code pattern to remember and skip in future",
            },
            "reason": {
                "type": "string",
                "description": "Reason for the feedback (especially important for false_positive)",
            },
        },
        "required": ["issue_id", "action", "repo"],
    },
),
# ─────────────────────────────────────────────────────────────
# CODE ENTITY INDEXING TOOLS
# ─────────────────────────────────────────────────────────────
Tool(
    name="turingmind_index_codebase",
    description=(
        "Index codebase using AST parsing to extract code entities "
        "(functions, classes, files) and relationships. "
        "Enables relationship-aware code review and impact analysis."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Repository identifier (owner/repo)",
            },
            "branch": {
                "type": "string",
                "description": "Git branch (default: main)",
                "default": "main",
            },
            "languages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Languages to parse (js, ts, py)",
                "default": ["javascript", "typescript", "python"],
            },
            "force_reindex": {
                "type": "boolean",
                "description": "Force reindex even if already indexed",
                "default": False,
            },
        },
        "required": ["repo"],
    },
),
Tool(
    name="turingmind_get_related_code",
    description=(
        "Get code entities related to a specific function/class/file. "
        "Uses relationship graph to find callers, callees, and imports for impact analysis."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "file": {"type": "string", "description": "File path"},
            "entity_name": {
                "type": "string",
                "description": "Function/class name (optional)",
            },
            "relationship_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Types: calls, imports (default: both)",
            },
            "direction": {
                "type": "string",
                "enum": ["both", "outgoing", "incoming"],
                "description": "Relationship direction",
                "default": "both",
            },
        },
        "required": ["repo", "file"],
    },
),
Tool(
    name="turingmind_get_project_structure",
    description=(
        "Get comprehensive project structure summary. "
        "Returns language distribution, entity type counts, and basic architecture info."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"}
        },
        "required": ["repo"],
    },
),
# ─────────────────────────────────────────────────────────────
# DEVELOPER INTENT TOOLS
# ─────────────────────────────────────────────────────────────
Tool(
    name="turingmind_get_edit_reasoning",
    description=(
        "Get or capture developer reasoning for file changes. "
        "Extracts intent from commit messages or prompts developer. "
        "Supports per-file reasoning. Helps code review understand intent and reduce false positives."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "reasoning": {"type": "string"},
                        "change_type": {
                            "type": "string",
                            "enum": ["bug_fix", "feature", "refactoring", "security", "other"],
                        },
                        "memory_category": {
                            "type": "string",
                            "enum": ["repo_fact", "learned_pattern", "explicit_rule", "session_context"],
                        },
                        "scope": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
                "description": "List of files with optional per-file reasoning",
            },
            "commit_message": {
                "type": "string",
                "description": "Optional commit message to parse",
            },
            "commit_hash": {
                "type": "string",
                "description": "Optional commit hash for historical lookups",
            },
            "conversation_id": {
                "type": "string",
                "description": "Optional conversation ID for context",
            },
            "interactive": {
                "type": "boolean",
                "description": "Whether to prompt user if reasoning not found",
                "default": False,
            },
        },
        "required": ["repo", "files"],
    },
),
# ─────────────────────────────────────────────────────────────
# MEMORY MANAGEMENT TOOLS
# ─────────────────────────────────────────────────────────────
Tool(
    name="turingmind_list_memory",
    description=(
        "List memory entries with filtering and pagination. "
        "Supports filtering by category, status, scope, and security tags."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "category": {
                "type": "string",
                "enum": ["repo_fact", "learned_pattern", "explicit_rule", "session_context", "all"],
                "description": "Memory category filter",
                "default": "all",
            },
            "status": {
                "type": "string",
                "enum": ["active", "pending", "conflict", "deprecated", "all"],
                "description": "Status filter",
                "default": "all",
            },
            "scope": {"type": "string", "description": "Filter by scope"},
            "branch": {
                "type": "string",
                "description": "Filter by git branch (requires TURINGMIND_BRANCH_MEMORY=1)",
            },
            "include_other_branches": {
                "type": "boolean",
                "description": "Include deprioritized memories from other branches",
                "default": False,
            },
            "security_tag": {
                "type": "string",
                "enum": ["auth", "crypto", "secrets", "compliance"],
                "description": "Filter by security tag",
            },
            "page": {"type": "integer", "description": "Page number", "default": 1},
            "limit": {"type": "integer", "description": "Items per page", "default": 50},
            "search": {"type": "string", "description": "Search content"},
        },
        "required": ["repo"],
    },
),
Tool(
    name="turingmind_get_memory",
    description=(
        "Get detailed information about a specific memory entry including evidence."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "memory_id": {"type": "string", "description": "Memory entry ID"},
        },
        "required": ["repo", "memory_id"],
    },
),
Tool(
    name="turingmind_save_memory",
    description=(
        "Create or update a memory entry. "
        "Supports learned patterns, explicit rules, and session context."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "memory_id": {
                "type": "string",
                "description": "Memory ID (optional for updates)",
            },
            "type": {
                "type": "string",
                "enum": ["learned_pattern", "explicit_rule", "session_context"],
                "description": "Memory type",
            },
            "content": {"type": "string", "description": "Memory content"},
            "scope": {"type": "string", "description": "Scope (repo, file, function)"},
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Confidence score",
            },
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "content": {"type": "string"},
                        "file": {"type": "string"},
                        "line": {"type": "integer"},
                    },
                },
                "description": "Evidence snippets",
            },
            "security_tags": {
                "type": "array",
                "items": {"type": "string", "enum": ["auth", "crypto", "secrets", "compliance"]},
                "description": "Security tags",
            },
            "yaml_definition": {
                "type": "string",
                "description": "YAML representation",
            },
        },
        "required": ["repo", "type", "content", "scope"],
    },
),
Tool(
    name="turingmind_delete_memory",
    description=(
        "Delete or deprecate a memory entry. "
        "Deprecation preserves history, deletion removes completely."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "memory_id": {"type": "string", "description": "Memory entry ID"},
            "action": {
                "type": "string",
                "enum": ["delete", "deprecate"],
                "description": "Action type",
                "default": "deprecate",
            },
        },
        "required": ["repo", "memory_id"],
    },
),
Tool(
    name="turingmind_detect_conflicts",
    description=(
        "Detect conflicts between memory entries. "
        "Identifies contradictions, overlaps, and scope conflicts."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "memory_id": {"type": "string", "description": "New/updated entry ID"},
        },
        "required": ["repo", "memory_id"],
    },
),
Tool(
    name="turingmind_resolve_conflict",
    description=(
        "Resolve conflicts between memory entries. "
        "Supports priority, scope-narrow, time-bound, and merge strategies."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "conflict_id": {"type": "string", "description": "Conflict ID"},
            "strategy": {
                "type": "string",
                "enum": ["priority", "scope_narrow", "time_bound", "merge"],
                "description": "Resolution strategy",
            },
            "resolution": {
                "type": "object",
                "properties": {
                    "keep_memory_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Memory IDs to keep",
                    },
                    "new_content": {"type": "string", "description": "Merged content"},
                    "new_scope": {"type": "string", "description": "New scope"},
                },
            },
        },
        "required": ["repo", "conflict_id", "strategy"],
    },
),
Tool(
    name="turingmind_simulate_impact",
    description=(
        "Simulate how memory entries affect code review. "
        "Shows before/after comparison of review results."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "memory_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Memory IDs to simulate",
            },
            "test_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Test files to review (optional)",
            },
        },
        "required": ["repo"],
    },
),
Tool(
    name="turingmind_explain_decision",
    description=(
        "Explain why AI made a specific decision in code review. "
        "Shows weighted memory contributions and reasoning."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "issue_id": {
                "type": "string",
                "description": "Review issue ID (optional)",
            },
            "file": {"type": "string", "description": "File path (optional)"},
            "line": {"type": "integer", "description": "Line number (optional)"},
        },
        "required": ["repo"],
    },
),
Tool(
    name="turingmind_get_memory_stats",
    description=(
        "Get statistics about memory entries for a repository. "
        "Returns counts by category, status, and other metrics."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"}
        },
        "required": ["repo"],
    },
),
Tool(
    name="turingmind_enable_auto_review",
    description=(
        "Enable automatic code review on git commits. "
        "Monitors repository for new commits and triggers reviews automatically."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "branch": {
                "type": "string",
                "description": "Branch to monitor",
                "default": "main",
            },
            "review_type": {
                "type": "string",
                "enum": ["quick", "deep"],
                "description": "Review type",
                "default": "quick",
            },
            "webhook_url": {
                "type": "string",
                "description": "Optional webhook for notifications",
            },
            "enabled": {
                "type": "boolean",
                "description": "Enable/disable monitoring",
                "default": True,
            },
        },
        "required": ["repo"],
    },
),
# ─────────────────────────────────────────────────────────────
# TDD/SDD WORKFLOW TOOLS
# ─────────────────────────────────────────────────────────────
Tool(
    name="turingmind_create_edit_plan",
    description=(
        "Create an EditPlan - REQUIRED before any code edits. "
        "This is the mandatory reasoning artifact that captures what you're doing, "
        "why you're doing it, and what success looks like. "
        "Returns plan_id that must be used for subsequent operations."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "change_intent": {
                "type": "string",
                "description": "Clear description of what you're trying to accomplish",
            },
            "files_affected": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of files that will be modified",
            },
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of criteria that define success (required)",
            },
            "observed_problem": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Problems or issues observed that led to this change",
            },
            "design_decisions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Key design decisions made",
            },
            "non_goals": {
                "type": "array",
                "items": {"type": "string"},
                "description": "What this change explicitly does NOT do",
            },
            "metadata": {
                "type": "object",
                "description": "Optional metadata (LOE, priority, complexity, estimatedDays, teamSize, dependencies)",
                "properties": {
                    "loe": {"type": "string", "description": "Level of Effort (e.g., '7 days', '2 weeks')"},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"], "description": "Priority level"},
                    "complexity": {"type": "string", "enum": ["simple", "medium", "high", "very-high"], "description": "Complexity level"},
                    "estimatedDays": {"type": "number", "description": "Estimated days to complete"},
                    "teamSize": {"type": "string", "description": "Team size (e.g., '1 developer', '2-3 developers')"},
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of plan_ids this depends on"
                    }
                }
            },
            "feature_id": {"type": "string", "description": "Strategic feature this plan implements (links plan to feature)"},
            "issue_id": {"type": "string", "description": "Issue this plan resolves (links plan to issue)"},
        },
        "required": ["repo", "change_intent", "files_affected", "acceptance_criteria"],
    },
),
Tool(
    name="turingmind_generate_spec",
    description=(
        "Generate specifications from an EditPlan. "
        "Automatically creates Gherkin-style specs from acceptance_criteria. "
        "Returns spec_id for test generation."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "EditPlan ID from create_edit_plan"},
        },
        "required": ["plan_id"],
    },
),
Tool(
    name="turingmind_define_required_tests",
    description=(
        "Define required tests for a specification (RED phase). "
        "Tests must be defined BEFORE any code can be written. "
        "Each test must specify name, type, test_file, and failure_mode."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "spec_id": {"type": "string", "description": "Specification ID"},
            "tests": {
                "type": "array",
                "description": "List of required tests",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Test function name"},
                        "type": {
                            "type": "string",
                            "enum": ["unit", "integration", "e2e"],
                            "description": "Test type",
                        },
                        "test_file": {"type": "string", "description": "Path to test file"},
                        "failure_mode": {
                            "type": "string",
                            "description": "What failing looks like",
                        },
                    },
                    "required": ["name", "type"],
                },
            },
        },
        "required": ["spec_id", "tests"],
    },
),
Tool(
    name="turingmind_validate_tests_written",
    description=(
        "Validate that required tests have been written. "
        "This is the gate between RED and GREEN phases. "
        "Tests must exist before code edits are allowed."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "EditPlan ID"},
        },
        "required": ["plan_id"],
    },
),
Tool(
    name="turingmind_request_code_edit",
    description=(
        "Request permission to edit a file (GREEN phase). "
        "Edits are ONLY allowed if: 1) EditPlan exists, 2) Specs generated, "
        "3) Tests written and validated, 4) Edit addresses acceptance criteria. "
        "Returns edit_token if approved, error if rejected."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "EditPlan ID"},
            "file_path": {"type": "string", "description": "File to edit"},
            "edit_type": {
                "type": "string",
                "enum": ["create", "modify", "delete"],
                "description": "Type of edit",
            },
            "acceptance_criteria_addressed": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Which acceptance criteria this edit addresses",
            },
        },
        "required": ["plan_id", "file_path", "edit_type", "acceptance_criteria_addressed"],
    },
),
Tool(
    name="turingmind_mark_test_written",
    description=(
        "Mark a required test as written after creating the test file. "
        "Verifies the test file exists on disk before marking. "
        "Must be called for each required test before turingmind_validate_tests_written."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "test_id": {"type": "string", "description": "Test ID from define_required_tests"},
            "test_file": {
                "type": "string",
                "description": "Path to the test file (verified to exist)",
            },
            "test_function": {
                "type": "string",
                "description": "Name of the test function in the file",
            },
        },
        "required": ["test_id", "test_file"],
    },
),
Tool(
    name="turingmind_validate_tests_passing",
    description=(
        "Validate that all tests are now passing (GREEN → REFACTOR transition). "
        "Called after code edits to verify the implementation."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "EditPlan ID"},
        },
        "required": ["plan_id"],
    },
),
Tool(
    name="turingmind_complete_tdd_cycle",
    description=(
        "Complete the TDD cycle and generate audit trail. "
        "Captures full EditPlan, specs, tests, code edits, and time metrics."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "EditPlan ID"},
        },
        "required": ["plan_id"],
    },
),
Tool(
    name="turingmind_get_tdd_status",
    description=(
        "Get current TDD workflow status for a repository. "
        "Shows active session, current phase, and what's needed next."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
        },
        "required": ["repo"],
    },
),
Tool(
    name="turingmind_get_audit_trail",
    description=(
        "Get full audit trail for a TDD cycle. "
        "Returns complete reasoning, specs, tests, and edits with traceability."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "EditPlan ID"},
        },
        "required": ["plan_id"],
    },
),
Tool(
    name="turingmind_get_kanban_data",
    description=(
        "Get Kanban board data for TDD workflow visualization. "
        "Returns all edit plans organized by TDD phase (planning, spec, red, green, refactor, done)."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "limit": {
                "type": "integer",
                "description": "Max items per column (default 20)",
                "default": 20,
            },
        },
        "required": ["repo"],
    },
),
# ─────────────────────────────────────────────────────────────
# AUTO-PLAN TOOLS (Continuous SDD)
# ─────────────────────────────────────────────────────────────
Tool(
    name="turingmind_analyze_diff",
    description=(
        "Analyze a git diff and auto-generate an EditPlan. "
        "This enables continuous SDD where plans are inferred from changes "
        "rather than requiring upfront planning. Returns an auto-generated plan "
        "with inferred intent, risk level, and suggested specs."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "file_path": {"type": "string", "description": "Path to the changed file"},
            "diff": {"type": "string", "description": "Git diff content"},
            "context": {
                "type": "string",
                "description": "Surrounding code context (optional)",
            },
            "commit_message": {
                "type": "string",
                "description": "Commit message if available (optional)",
            },
        },
        "required": ["repo", "file_path", "diff"],
    },
),
Tool(
    name="turingmind_store_auto_plan",
    description=(
        "Store an auto-generated plan from diff analysis. "
        "Links the plan to a specific file and diff hash for traceability."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "file_path": {"type": "string", "description": "Path to the changed file"},
            "intent": {"type": "string", "description": "Inferred change intent"},
            "diff_hash": {"type": "string", "description": "Hash of the diff for deduplication"},
            "risk_level": {
                "type": "string",
                "enum": ["low", "medium", "high", "critical"],
                "description": "Inferred risk level",
            },
            "changes_summary": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of changes made",
            },
            "suggested_specs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Suggested specifications for validation",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Confidence score of the inference",
            },
        },
        "required": ["repo", "file_path", "intent", "diff_hash"],
    },
),
Tool(
    name="turingmind_get_auto_plans",
    description=(
        "Get auto-generated plans for a repository or specific files. "
        "Useful for reviewing inferred intents before commit."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "file_path": {"type": "string", "description": "Filter by file path (optional)"},
            "since": {"type": "string", "description": "ISO timestamp to filter from (optional)"},
            "uncommitted_only": {
                "type": "boolean",
                "description": "Only return plans not yet linked to a commit",
                "default": True,
            },
        },
        "required": ["repo"],
    },
),
Tool(
    name="turingmind_bundle_plans_for_commit",
    description=(
        "Bundle all uncommitted auto-plans into a commit record. "
        "Called by git hooks to link plans to commits."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository (owner/repo)"},
            "commit_sha": {"type": "string", "description": "Git commit SHA"},
            "commit_message": {"type": "string", "description": "Commit message"},
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of files in the commit",
            },
        },
        "required": ["repo", "commit_sha", "files"],
    },
),
# ─────────────────────────────────────────────────────────────
# REASONING-CAPTURED EDIT TOOL
# ─────────────────────────────────────────────────────────────
Tool(
    name="turingmind_apply_edit",
    description=(
        "Apply code changes with MANDATORY reasoning capture. "
        "Use this for ALL file edits to ensure intent is documented. "
        "The reasoning becomes part of the permanent audit trail."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "WHY you are making this change (required)",
            },
            "problem_observed": {
                "type": "string",
                "description": "What problem or issue did you identify that led to this change",
            },
            "approach": {
                "type": "string",
                "description": "How you are solving the problem (your strategy)",
            },
            "alternatives_considered": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Other approaches you considered but rejected",
            },
            "file_path": {
                "type": "string",
                "description": "Path to the file to edit",
            },
            "edit_type": {
                "type": "string",
                "enum": ["create", "modify", "delete"],
                "description": "Type of edit operation",
            },
            "old_content": {
                "type": "string",
                "description": "Content to find and replace (for modify)",
            },
            "new_content": {
                "type": "string",
                "description": "New content to insert",
            },
            "full_content": {
                "type": "string",
                "description": "Full file content (for create or full rewrite)",
            },
            "repo": {
                "type": "string",
                "description": "Repository identifier (owner/repo)",
            },
        },
        "required": ["reasoning", "file_path", "edit_type"],
    },
),
Tool(
    name="turingmind_log_reasoning",
    description=(
        "Log your reasoning/thinking process without making changes. "
        "Use this to document your thought process, analysis, and decisions. "
        "Creates a permanent record of AI reasoning for audit trails."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "reasoning_type": {
                "type": "string",
                "enum": ["analysis", "decision", "observation", "plan", "concern"],
                "description": "Type of reasoning being logged",
            },
            "content": {
                "type": "string",
                "description": "The reasoning/thought content",
            },
            "context": {
                "type": "string",
                "description": "What you were looking at or considering",
            },
            "related_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Files related to this reasoning",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "How confident you are in this reasoning",
            },
            "repo": {
                "type": "string",
                "description": "Repository identifier",
            },
            "session_id": {
                "type": "string",
                "description": "Session ID to group reasoning together",
            },
        },
        "required": ["reasoning_type", "content"],
    },
),
# ─────────────────────────────────────────────────────────────
# CHAT ANALYSIS TOOLS
# ─────────────────────────────────────────────────────────────
Tool(
    name="turingmind_store_chat_analysis_plan",
    description=(
        "Store a chat analysis plan extracted from Cursor chat threads. "
        "Stores metadata only (reasoning, user prompts, intents) - NO CODE. "
        "Used for tracking LLM decision-making and intent evolution."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository identifier (owner/repo)"},
            "composer_id": {"type": "string", "description": "Cursor composer ID"},
            "thread_name": {"type": "string", "description": "Thread/session name"},
            "metadata": {
                "type": "object",
                "description": "Extracted metadata (reasoning, prompts, responses - no code)",
                "properties": {
                    "userPrompts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "timestamp": {"type": "number"},
                                "sequence": {"type": "number"}
                            }
                        }
                    },
                    "reasoning": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "bubbleId": {"type": "string"},
                                "reasoning": {"type": "array", "items": {"type": "string"}},
                                "timestamp": {"type": "number"},
                                "sequence": {"type": "number"}
                            }
                        }
                    },
                    "assistantResponses": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "timestamp": {"type": "number"},
                                "hasReasoning": {"type": "boolean"},
                                "sequence": {"type": "number"}
                            }
                        }
                    },
                    "intentEvolution": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "stage": {"type": "number"},
                                "intent": {"type": "string"},
                                "timestamp": {"type": "number"}
                            }
                        }
                    }
                }
            },
            "summary": {
                "type": "object",
                "description": "Extracted summary with intents and file changes",
                "properties": {
                    "initialIntent": {"type": "string"},
                    "finalIntent": {"type": "string"},
                    "fileChanges": {
                        "type": "object",
                        "additionalProperties": {"type": "string"}
                    }
                }
            },
            "created_at": {"type": "number", "description": "Unix timestamp in milliseconds"},
        },
        "required": ["repo", "composer_id"],
    },
),
Tool(
    name="turingmind_get_chat_analysis_plans",
    description=(
        "Get stored chat analysis plans for a repository. "
        "Returns plans with summaries, intents, and file changes for display in the extension UI."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository identifier"},
            "composer_id": {"type": "string", "description": "Filter by specific composer ID (optional)"},
            "limit": {"type": "number", "description": "Max results (default: 50)", "default": 50},
            "offset": {"type": "number", "description": "Pagination offset (default: 0)", "default": 0},
        },
        "required": ["repo"],
    },
),
# ─────────────────────────────────────────────────────────────
# DECISION QUEUE TOOLS (Human-in-the-Loop)
# ─────────────────────────────────────────────────────────────
Tool(
    name="turingmind_create_decision",
    description=(
        "Create a decision request requiring human approval. "
        "Use this when the AI needs human input on plan approval, spec approval, "
        "edit approval, or tradeoff decisions. Returns decision_id for tracking."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository identifier (owner/repo)"},
            "decision_type": {
                "type": "string",
                "enum": ["plan_approval", "spec_approval", "edit_approval", "tradeoff", "custom"],
                "description": "Type of decision",
            },
            "title": {"type": "string", "description": "Short description for UI"},
            "description": {"type": "string", "description": "Detailed context"},
            "plan_id": {"type": "string", "description": "Related EditPlan ID (optional)"},
            "spec_id": {"type": "string", "description": "Related Specification ID (optional)"},
            "linear_issue_id": {"type": "string", "description": "Related Linear issue ID (optional)"},
            "context": {
                "type": "object",
                "description": "Full context for decision (files, criteria, etc.)",
            },
            "options": {
                "type": "array",
                "description": "Available options for tradeoff decisions",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "label": {"type": "string"},
                        "description": {"type": "string"},
                    },
                },
            },
            "priority": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "default": "medium",
                "description": "Priority level",
            },
            "cursor_session_id": {
                "type": "string",
                "description": "Cursor session ID for resume after approval (optional; use when reader unavailable e.g. headless)",
            },
        },
        "required": ["repo", "decision_type", "title"],
    },
),
Tool(
    name="turingmind_get_pending_decisions",
    description=(
        "Get pending decisions requiring human approval. "
        "Returns decisions sorted by priority, with full context for UI display."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository identifier (owner/repo)"},
            "decision_type": {
                "type": "string",
                "enum": ["plan_approval", "spec_approval", "edit_approval", "tradeoff", "custom"],
                "description": "Filter by decision type (optional)",
            },
            "priority": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "Filter by priority (optional)",
            },
            "limit": {"type": "integer", "default": 50, "description": "Max results"},
            "offset": {"type": "integer", "default": 0, "description": "Pagination offset"},
        },
        "required": ["repo"],
    },
),
Tool(
    name="turingmind_resolve_decision",
    description=(
        "Resolve a pending decision. Use this when human has made a choice. "
        "Resolution can be 'approved', 'rejected', 'deferred', or a custom option ID."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "decision_id": {"type": "string", "description": "Decision ID to resolve"},
            "resolution": {
                "type": "string",
                "description": "Resolution value ('approved', 'rejected', 'deferred', or option ID)",
            },
            "resolved_by": {
                "type": "string",
                "default": "user",
                "description": "Who resolved (user ID or 'auto')",
            },
            "resolution_reason": {
                "type": "string",
                "description": "Why this decision was made (optional)",
            },
        },
        "required": ["decision_id", "resolution"],
    },
),
Tool(
    name="turingmind_get_decision_stats",
    description=(
        "Get decision queue statistics for a repository. "
        "Returns counts by status, type, and priority."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository identifier (owner/repo)"},
        },
        "required": ["repo"],
    },
),

# ─────────────────────────────────────────────────────────────
# ISSUES TOOLS (Linear-compatible naming)
# ─────────────────────────────────────────────────────────────
Tool(
    name="turingmind_create_issue",
    description=(
        "Create a new issue (task/work item). Uses Linear-compatible naming. "
        "Issues can be linked to decisions and plans for traceability."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository identifier (owner/repo)"},
            "title": {"type": "string", "description": "Issue title"},
            "description": {"type": "string", "description": "Issue description (markdown)"},
            "state": {
                "type": "string",
                "description": "Issue state",
                "enum": ["backlog", "todo", "in_progress", "done", "cancelled"],
                "default": "backlog",
            },
            "priority": {
                "type": "integer",
                "description": "Priority (1=Urgent, 2=High, 3=Normal, 4=Low)",
                "minimum": 1,
                "maximum": 4,
                "default": 3,
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Label strings",
            },
            "assignee": {"type": "string", "description": "User ID or name"},
            "project_id": {"type": "string", "description": "Parent project ID"},
            "parent_id": {"type": "string", "description": "Parent issue ID (for sub-tasks)"},
            "feature_id": {"type": "string", "description": "Linked feature ID (strategic planning)"},
            "decision_id": {"type": "string", "description": "Linked decision ID"},
            "plan_id": {"type": "string", "description": "Linked EditPlan ID"},
            "due_date": {"type": "string", "description": "Due date (ISO format)"},
            "blocked_by": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Issue IDs that block this issue (must be completed first)",
            },
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Issue IDs this issue depends on",
            },
        },
        "required": ["repo", "title"],
    },
),
Tool(
    name="turingmind_get_issue",
    description="Get a specific issue by ID.",
    inputSchema={
        "type": "object",
        "properties": {
            "issue_id": {"type": "string", "description": "Issue ID"},
        },
        "required": ["issue_id"],
    },
),
Tool(
    name="turingmind_list_issues",
    description=(
        "List issues with optional filters. "
        "Returns issues sorted by state (in_progress first) and priority."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository identifier (owner/repo)"},
            "state": {
                "type": "string",
                "description": "Filter by state",
                "enum": ["backlog", "todo", "in_progress", "done", "cancelled"],
            },
            "assignee": {"type": "string", "description": "Filter by assignee"},
            "project_id": {"type": "string", "description": "Filter by project"},
            "priority": {
                "type": "integer",
                "description": "Filter by priority (1-4)",
                "minimum": 1,
                "maximum": 4,
            },
            "limit": {"type": "integer", "description": "Max results (default 50)", "default": 50},
            "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
        },
        "required": ["repo"],
    },
),
Tool(
    name="turingmind_update_issue",
    description=(
        "Update an existing issue. "
        "Only provided fields will be updated."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "issue_id": {"type": "string", "description": "Issue ID"},
            "title": {"type": "string", "description": "New title"},
            "description": {"type": "string", "description": "New description"},
            "state": {
                "type": "string",
                "description": "New state",
                "enum": ["backlog", "todo", "in_progress", "done", "cancelled"],
            },
            "priority": {
                "type": "integer",
                "description": "New priority (1-4)",
                "minimum": 1,
                "maximum": 4,
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "New labels",
            },
            "assignee": {"type": "string", "description": "New assignee (empty to clear)"},
            "project_id": {"type": "string", "description": "New project (empty to clear)"},
            "feature_id": {"type": "string", "description": "Linked feature ID (empty to clear)"},
            "due_date": {"type": "string", "description": "New due date (empty to clear)"},
            "blocked_by": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Issue IDs that block this issue",
            },
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Issue IDs this issue depends on",
            },
        },
        "required": ["issue_id"],
    },
),
Tool(
    name="turingmind_is_issue_blocked",
    description=(
        "Check if an issue is blocked by other issues. "
        "Returns whether the issue is blocked and which issues are blocking it."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "issue_id": {"type": "string", "description": "Issue ID to check"},
        },
        "required": ["issue_id"],
    },
),
Tool(
    name="turingmind_get_next_unblocked_issue",
    description=(
        "Get the next unblocked issue for a feature. "
        "Returns the highest priority issue that is not blocked and not done. "
        "Plugin may send feature_id and/or repo from context; if feature_id omitted, returns null."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "feature_id": {"type": "string", "description": "Feature ID to scope the next issue (plugin passes from context when available)"},
            "repo": {"type": "string", "description": "Repository scope (optional)"},
        },
        "required": [],
    },
),
# ─────────────────────────────────────────────────────────────
# STRATEGIC SPECS TOOLS
# ─────────────────────────────────────────────────────────────
Tool(
    name="turingmind_create_strategic_spec",
    description=(
        "Create a strategic spec (detailed requirements document). "
        "Specs define functional, technical, UX, API, or security requirements for features."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository identifier (owner/repo)"},
            "title": {"type": "string", "description": "Spec title"},
            "content": {"type": "string", "description": "Detailed requirements content (markdown)"},
            "feature_id": {"type": "string", "description": "Feature ID this spec belongs to"},
            "spec_type": {
                "type": "string",
                "enum": ["functional", "technical", "ux", "api", "security"],
                "description": "Type of specification",
                "default": "functional",
            },
            "requirements": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of individual requirements",
            },
            "constraints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of constraints",
            },
            "assumptions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of assumptions",
            },
            "test_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of test/verification criteria",
            },
            "status": {
                "type": "string",
                "enum": ["draft", "review", "approved", "implemented"],
                "default": "draft",
            },
        },
        "required": ["repo", "title"],
    },
),
Tool(
    name="turingmind_get_strategic_spec",
    description="Get a strategic spec by ID.",
    inputSchema={
        "type": "object",
        "properties": {
            "spec_id": {"type": "string", "description": "Spec ID"},
        },
        "required": ["spec_id"],
    },
),
Tool(
    name="turingmind_list_strategic_specs",
    description="List strategic specs with optional filters.",
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository identifier (owner/repo)"},
            "feature_id": {"type": "string", "description": "Filter by feature ID"},
            "status": {
                "type": "string",
                "enum": ["draft", "review", "approved", "implemented"],
                "description": "Filter by status",
            },
            "spec_type": {
                "type": "string",
                "enum": ["functional", "technical", "ux", "api", "security"],
                "description": "Filter by spec type",
            },
        },
        "required": ["repo"],
    },
),
Tool(
    name="turingmind_update_strategic_spec",
    description="Update a strategic spec.",
    inputSchema={
        "type": "object",
        "properties": {
            "spec_id": {"type": "string", "description": "Spec ID"},
            "title": {"type": "string", "description": "Updated title"},
            "content": {"type": "string", "description": "Updated content"},
            "feature_id": {"type": "string", "description": "Updated feature ID"},
            "spec_type": {
                "type": "string",
                "enum": ["functional", "technical", "ux", "api", "security"],
            },
            "requirements": {"type": "array", "items": {"type": "string"}},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "test_criteria": {"type": "array", "items": {"type": "string"}},
            "status": {
                "type": "string",
                "enum": ["draft", "review", "approved", "implemented"],
            },
            "reviewed_by": {"type": "string", "description": "Reviewer ID"},
            "approved_by": {"type": "string", "description": "Approver ID"},
        },
        "required": ["spec_id"],
    },
),
# ─────────────────────────────────────────────────────────────
# STRATEGIC RISKS TOOLS
# ─────────────────────────────────────────────────────────────
Tool(
    name="turingmind_create_strategic_risk",
    description=(
        "Create a strategic risk. Risks can be attached to any entity "
        "(goal, initiative, feature, issue) for risk tracking."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository identifier (owner/repo)"},
            "title": {"type": "string", "description": "Risk title"},
            "description": {"type": "string", "description": "Risk description"},
            "entity_type": {
                "type": "string",
                "enum": ["goal", "initiative", "feature", "issue"],
                "description": "Type of entity this risk is attached to",
            },
            "entity_id": {"type": "string", "description": "ID of the entity"},
            "probability": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "default": "medium",
            },
            "impact": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "default": "medium",
            },
            "mitigation_plan": {"type": "string", "description": "Plan to mitigate the risk"},
            "contingency_plan": {"type": "string", "description": "Plan if risk occurs"},
            "status": {
                "type": "string",
                "enum": ["identified", "mitigating", "mitigated", "occurred", "closed"],
                "default": "identified",
            },
            "owner": {"type": "string", "description": "Risk owner ID"},
        },
        "required": ["repo", "title"],
    },
),
Tool(
    name="turingmind_get_strategic_risk",
    description="Get a strategic risk by ID.",
    inputSchema={
        "type": "object",
        "properties": {
            "risk_id": {"type": "string", "description": "Risk ID"},
        },
        "required": ["risk_id"],
    },
),
Tool(
    name="turingmind_list_strategic_risks",
    description="List strategic risks with optional filters.",
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository identifier (owner/repo)"},
            "entity_type": {
                "type": "string",
                "enum": ["goal", "initiative", "feature", "issue"],
                "description": "Filter by entity type",
            },
            "entity_id": {"type": "string", "description": "Filter by entity ID"},
            "status": {
                "type": "string",
                "enum": ["identified", "mitigating", "mitigated", "occurred", "closed"],
            },
            "min_risk_score": {
                "type": "integer",
                "minimum": 1,
                "maximum": 9,
                "description": "Minimum risk score (1-9)",
            },
        },
        "required": ["repo"],
    },
),
Tool(
    name="turingmind_update_strategic_risk",
    description="Update a strategic risk.",
    inputSchema={
        "type": "object",
        "properties": {
            "risk_id": {"type": "string", "description": "Risk ID"},
            "title": {"type": "string", "description": "Updated title"},
            "description": {"type": "string", "description": "Updated description"},
            "entity_type": {
                "type": "string",
                "enum": ["goal", "initiative", "feature", "issue"],
            },
            "entity_id": {"type": "string", "description": "Updated entity ID"},
            "probability": {"type": "string", "enum": ["low", "medium", "high"]},
            "impact": {"type": "string", "enum": ["low", "medium", "high"]},
            "mitigation_plan": {"type": "string"},
            "contingency_plan": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["identified", "mitigating", "mitigated", "occurred", "closed"],
            },
            "owner": {"type": "string"},
        },
        "required": ["risk_id"],
    },
),
Tool(
    name="turingmind_create_comment",
    description="Create a comment on an issue.",
    inputSchema={
        "type": "object",
        "properties": {
            "issue_id": {"type": "string", "description": "Issue ID"},
            "body": {"type": "string", "description": "Comment body (markdown)"},
            "author": {"type": "string", "description": "Author ID (default: 'ai')", "default": "ai"},
        },
        "required": ["issue_id", "body"],
    },
),
Tool(
    name="turingmind_list_comments",
    description="List comments for an issue.",
    inputSchema={
        "type": "object",
        "properties": {
            "issue_id": {"type": "string", "description": "Issue ID"},
            "limit": {"type": "integer", "description": "Max results (default 50)", "default": 50},
            "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
        },
        "required": ["issue_id"],
    },
),

# ─────────────────────────────────────────────────────────────
# STRATEGIC PLANNING TOOLS (Goals, Initiatives, Features)
# ─────────────────────────────────────────────────────────────

# GOALS
Tool(
    name="turingmind_create_goal",
    description=(
        "Create a new goal (OKR/measurable outcome). Goals are the top of the strategic hierarchy. "
        "They define what success looks like with measurable metrics. "
        "Plugin contract: pass 'intent' (used as title) and optionally 'repo'; repo defaults to context if omitted."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "intent": {"type": "string", "description": "Goal intent/title (plugin sends this as primary)"},
            "repo": {"type": "string", "description": "Repository identifier (owner/repo); default inferred if omitted"},
            "title": {"type": "string", "description": "Goal title (e.g., 'Reduce MTTR by 50%'); overrides intent if set"},
            "description": {"type": "string", "description": "Detailed description"},
            "metric": {"type": "string", "description": "What to measure (e.g., 'mean_time_to_remediate')"},
            "target_value": {"type": "number", "description": "Target value for the metric"},
            "current_value": {"type": "number", "description": "Current value of the metric"},
            "unit": {"type": "string", "description": "Unit of measurement (e.g., 'days', 'percent')"},
            "timeframe": {"type": "string", "description": "Timeframe (e.g., 'Q1 2026', '2026')"},
            "start_date": {"type": "string", "description": "Start date (ISO format)"},
            "target_date": {"type": "string", "description": "Target date (ISO format)"},
            "parent_goal_id": {"type": "string", "description": "Parent goal ID for nested goals"},
            "owner": {"type": "string", "description": "Goal owner"},
        },
        "required": [],
    },
),
Tool(
    name="turingmind_get_goal",
    description="Get a specific goal by ID.",
    inputSchema={
        "type": "object",
        "properties": {
            "goal_id": {"type": "string", "description": "Goal ID"},
        },
        "required": ["goal_id"],
    },
),
Tool(
    name="turingmind_list_goals",
    description="List goals with optional filters.",
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository identifier"},
            "status": {
                "type": "string",
                "description": "Filter by status",
                "enum": ["active", "achieved", "missed", "cancelled"],
            },
            "parent_goal_id": {"type": "string", "description": "Filter by parent goal (empty string for top-level)"},
            "limit": {"type": "integer", "description": "Max results", "default": 50},
            "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
        },
        "required": ["repo"],
    },
),
Tool(
    name="turingmind_update_goal",
    description="Update an existing goal. Use to update progress, status, or confidence.",
    inputSchema={
        "type": "object",
        "properties": {
            "goal_id": {"type": "string", "description": "Goal ID"},
            "title": {"type": "string", "description": "New title"},
            "description": {"type": "string", "description": "New description"},
            "current_value": {"type": "number", "description": "Updated current value"},
            "status": {
                "type": "string",
                "description": "New status",
                "enum": ["active", "achieved", "missed", "cancelled"],
            },
            "confidence": {
                "type": "integer",
                "description": "Confidence of achieving (0-100)",
                "minimum": 0,
                "maximum": 100,
            },
            "owner": {"type": "string", "description": "New owner"},
        },
        "required": ["goal_id"],
    },
),

# INITIATIVES
Tool(
    name="turingmind_create_initiative",
    description=(
        "Create a new initiative (strategic bet). Initiatives are time-boxed efforts that serve a goal. "
        "They have a hypothesis about what will happen if successful. "
        "Plugin contract: pass goal_id (required); repo/title optional (repo inferred from goal if omitted)."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "goal_id": {"type": "string", "description": "Parent goal ID (plugin sends this)"},
            "repo": {"type": "string", "description": "Repository identifier; inferred from goal if omitted"},
            "title": {"type": "string", "description": "Initiative title; default 'Initiative' if omitted"},
            "description": {"type": "string", "description": "Detailed description"},
            "hypothesis": {"type": "string", "description": "If we do X, then Y will happen"},
            "success_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of measurable success criteria",
            },
            "out_of_scope": {"type": "string", "description": "What is explicitly NOT included"},
            "start_date": {"type": "string", "description": "Start date (ISO format)"},
            "target_date": {"type": "string", "description": "Target date (ISO format)"},
            "owner": {"type": "string", "description": "Initiative owner (DRI)"},
            "team_members": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Team members",
            },
        },
        "required": ["goal_id"],
    },
),
Tool(
    name="turingmind_get_initiative",
    description="Get a specific initiative by ID.",
    inputSchema={
        "type": "object",
        "properties": {
            "initiative_id": {"type": "string", "description": "Initiative ID"},
        },
        "required": ["initiative_id"],
    },
),
Tool(
    name="turingmind_list_initiatives",
    description="List initiatives with optional filters.",
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository identifier"},
            "status": {
                "type": "string",
                "description": "Filter by status",
                "enum": ["planned", "active", "completed", "cancelled"],
            },
            "goal_id": {"type": "string", "description": "Filter by parent goal"},
            "health": {
                "type": "string",
                "description": "Filter by health",
                "enum": ["on_track", "at_risk", "off_track"],
            },
            "limit": {"type": "integer", "description": "Max results", "default": 50},
            "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
        },
        "required": ["repo"],
    },
),
Tool(
    name="turingmind_update_initiative",
    description="Update an existing initiative.",
    inputSchema={
        "type": "object",
        "properties": {
            "initiative_id": {"type": "string", "description": "Initiative ID"},
            "title": {"type": "string", "description": "New title"},
            "description": {"type": "string", "description": "New description"},
            "status": {
                "type": "string",
                "description": "New status",
                "enum": ["planned", "active", "completed", "cancelled"],
            },
            "health": {
                "type": "string",
                "description": "New health status",
                "enum": ["on_track", "at_risk", "off_track"],
            },
            "owner": {"type": "string", "description": "New owner"},
        },
        "required": ["initiative_id"],
    },
),

# FEATURES
Tool(
    name="turingmind_create_feature",
    description=(
        "Create a new feature (user-facing capability). Features describe what users will be able to do. "
        "They belong to initiatives and break down into issues/tasks."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository identifier"},
            "title": {"type": "string", "description": "Feature title"},
            "description": {"type": "string", "description": "Detailed description"},
            "initiative_id": {"type": "string", "description": "Parent initiative ID"},
            "user_story": {"type": "string", "description": "As a [user], I want [goal] so that [benefit]"},
            "value_proposition": {"type": "string", "description": "Why this matters"},
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of acceptance criteria",
            },
            "out_of_scope": {"type": "string", "description": "What is explicitly NOT included"},
            "priority": {
                "type": "integer",
                "description": "Priority (1=Urgent, 2=High, 3=Normal, 4=Low)",
                "minimum": 1,
                "maximum": 4,
                "default": 3,
            },
            "effort_estimate": {
                "type": "string",
                "description": "Effort estimate",
                "enum": ["XS", "S", "M", "L", "XL"],
            },
            "impact_estimate": {
                "type": "string",
                "description": "Impact estimate",
                "enum": ["low", "medium", "high"],
            },
            "owner": {"type": "string", "description": "Feature owner"},
        },
        "required": ["repo", "title"],
    },
),
Tool(
    name="turingmind_get_feature",
    description="Get a specific feature by ID.",
    inputSchema={
        "type": "object",
        "properties": {
            "feature_id": {"type": "string", "description": "Feature ID"},
        },
        "required": ["feature_id"],
    },
),
Tool(
    name="turingmind_list_features",
    description="List features with optional filters.",
    inputSchema={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Repository identifier"},
            "status": {
                "type": "string",
                "description": "Filter by status",
                "enum": ["proposed", "approved", "in_progress", "shipped", "cancelled"],
            },
            "initiative_id": {"type": "string", "description": "Filter by parent initiative"},
            "priority": {
                "type": "integer",
                "description": "Filter by priority (1-4)",
                "minimum": 1,
                "maximum": 4,
            },
            "limit": {"type": "integer", "description": "Max results", "default": 50},
            "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
        },
        "required": ["repo"],
    },
),
Tool(
    name="turingmind_update_feature",
    description="Update an existing feature.",
    inputSchema={
        "type": "object",
        "properties": {
            "feature_id": {"type": "string", "description": "Feature ID"},
            "title": {"type": "string", "description": "New title"},
            "description": {"type": "string", "description": "New description"},
            "status": {
                "type": "string",
                "description": "New status",
                "enum": ["proposed", "approved", "in_progress", "shipped", "cancelled"],
            },
            "progress": {
                "type": "integer",
                "description": "Progress percentage (0-100)",
                "minimum": 0,
                "maximum": 100,
            },
            "priority": {
                "type": "integer",
                "description": "New priority (1-4)",
                "minimum": 1,
                "maximum": 4,
            },
            "owner": {"type": "string", "description": "New owner"},
        },
        "required": ["feature_id"],
    },
),

# ─────────────────────────────────────────────────────────────
# WORKFLOW AUTOMATION
# ─────────────────────────────────────────────────────────────
Tool(
    name="turingmind_break_down_feature",
    description=(
        "Break down a feature into implementation issues. "
        "When 'issues' is omitted or empty, MCP generates a single default issue from the feature. "
        "Otherwise creates the given issues linked to the feature. "
        "Plugin contract: pass feature_id only for generate mode."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "feature_id": {"type": "string", "description": "Feature ID to break down"},
            "issues": {
                "type": "array",
                "description": "Optional list of issues to create; if omitted or empty, one issue is generated from the feature",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Issue title"},
                        "description": {"type": "string", "description": "Issue description with implementation details"},
                        "priority": {
                            "type": "integer",
                            "description": "Priority (1=Urgent, 2=High, 3=Normal, 4=Low)",
                            "minimum": 1,
                            "maximum": 4,
                            "default": 3,
                        },
                        "labels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Labels for categorization",
                        },
                    },
                    "required": ["title"],
                },
            },
        },
        "required": ["feature_id"],
    },
),
Tool(
    name="turingmind_update_feature_progress",
    description=(
        "Recalculate a feature's progress from its linked issues. "
        "Auto-updates progress percentage and status based on issue states. "
        "Call this after completing/closing issues to keep feature progress in sync."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "feature_id": {"type": "string", "description": "Feature ID to update progress for"},
        },
        "required": ["feature_id"],
    },
),
Tool(
    name="turingmind_get_feature_issues",
    description=(
        "Get all issues linked to a feature. Shows the implementation breakdown "
        "and current status of each issue."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "feature_id": {"type": "string", "description": "Feature ID"},
        },
        "required": ["feature_id"],
    },
),
]

def get_all_tools() -> list[Tool]:
    return ALL_TOOLS

