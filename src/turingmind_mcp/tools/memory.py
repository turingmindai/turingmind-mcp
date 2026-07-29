"""Memory tools: list_memory, get_memory, save_memory, delete_memory, detect_conflicts, resolve_conflict, simulate_impact, explain_decision, get_memory_stats."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from mcp.types import TextContent

from .context import ToolContext


def _git_fields_for_mcp_save(
    ctx: ToolContext,
    arguments: Optional[dict] = None,
) -> Dict[str, Any]:
    """Attach current git branch/HEAD to MCP saves (parity with HTTP /api/v2/memory).

    Prefer optional ``arguments.git`` blob; else collect from workspace via
    ``ctx.get_repo_path`` / ``TURINGMIND_WORKSPACE_DIR`` / cwd.
    """
    from turingmind_mcp.git_context import (
        collect_git_context,
        git_context_from_payload,
        git_fields_for_storage,
        normalize_scope_tier_write,
    )

    args = arguments or {}
    raw_git = args.get("git")
    if isinstance(raw_git, dict) and raw_git:
        try:
            git_ctx = git_context_from_payload(
                {
                    "branch": raw_git.get("branch"),
                    "head": raw_git.get("head") or raw_git.get("head_sha"),
                    "dirty": bool(raw_git.get("dirty", False)),
                    "default_branch": raw_git.get("default_branch"),
                }
            )
            fields = git_fields_for_storage(git_ctx)
            fields["scope_tier"] = normalize_scope_tier_write(
                fields["branch"],
                bool(fields["git_dirty"]),
                raw_git.get("scope_tier"),
            )
            return fields
        except ValueError as exc:
            ctx.logger.warning("MCP save_memory ignored invalid git blob: %s", exc)

    workspace: Optional[Path] = None
    if ctx.get_repo_path:
        try:
            repo_path = ctx.get_repo_path()
            if repo_path:
                workspace = Path(str(repo_path))
        except Exception as exc:  # noqa: BLE001 — never fail save on path helper
            ctx.logger.debug("get_repo_path failed during save_memory: %s", exc)

    git_ctx = collect_git_context(workspace)
    return git_fields_for_storage(git_ctx)


def register(registry: dict) -> None:
    registry["turingmind_list_memory"] = handle_list_memory
    registry["turingmind_get_memory"] = handle_get_memory
    registry["turingmind_save_memory"] = handle_save_memory
    registry["turingmind_delete_memory"] = handle_delete_memory
    registry["turingmind_detect_conflicts"] = handle_detect_conflicts
    registry["turingmind_resolve_conflict"] = handle_resolve_conflict
    registry["turingmind_ground"] = handle_ground
    # NOTE: simulate_impact, explain_decision, get_memory_stats remain
    # unregistered — no v2 tool definitions, not exposed to agents.


async def handle_list_memory(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    workspace_id = arguments.get("workspace_id")
    repos_arg = arguments.get("repos") or []
    if isinstance(repos_arg, str):
        repos_arg = [r.strip() for r in repos_arg.split(",") if r.strip()]
    if not repo and not workspace_id and not repos_arg:
        return [
            TextContent(
                type="text",
                text="❌ **Missing required field:** `repo` (or `workspace_id` / `repos`)",
            )
        ]
    category = arguments.get("category", "all")
    status = arguments.get("status", "all")
    scope = arguments.get("scope")
    branch = arguments.get("branch")
    include_other_branches = bool(arguments.get("include_other_branches", False))
    security_tag = arguments.get("security_tag")
    page = arguments.get("page", 1)
    limit = arguments.get("limit", 50)
    search = arguments.get("search")
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        from turingmind_mcp.workspace import merge_entry_lists, resolve_workspace

        ws = resolve_workspace(
            workspace_id=workspace_id,
            repos=repos_arg,
            primary_repo=repo or None,
        )
        targets = [r for r in ws.repos if r] or ([repo] if repo else [])
        db = ctx.get_db()
        try:
            page_n = max(1, int(page or 1))
        except (TypeError, ValueError):
            page_n = 1
        try:
            limit_n = max(1, int(limit or 50))
        except (TypeError, ValueError):
            limit_n = 50
        # Fetch enough per repo to page after merge (cap to keep queries bounded).
        fetch_limit = min(max(limit_n * page_n, limit_n), 500)
        batches = []
        for r in targets:
            rows = db.list_memory_entries(
                repo=r,
                memory_type=category if category != "all" else None,
                status=status if status != "all" else None,
                scope=scope,
                branch=branch if r == (ws.primary_repo or repo) else None,
                include_other_branches=include_other_branches,
                page=1,
                limit=fetch_limit,
                search=search,
            )
            batch = []
            for e in rows:
                e = dict(e)
                e["repo"] = r
                batch.append(e)
            batches.append(batch)
        merged = merge_entry_lists(batches, limit=fetch_limit * max(1, len(targets)))
        if security_tag:
            merged = [
                e for e in merged
                if e.get("security_tags") and security_tag in e.get("security_tags", [])
            ]
        total = len(merged)
        start = (page_n - 1) * limit_n
        entries = merged[start : start + limit_n]
        # Machine-parseable JSON: agents need memory_id to round-trip into
        # get_memory / save_memory, and full content to act on the entry.
        payload = {
            "total": total,
            "page": page_n,
            "limit": limit_n,
            "repo": ws.primary_repo or repo,
            "repos": targets,
            "workspace_id": ws.workspace_id,
            "entries": [
                {
                    "memory_id": e["memory_id"],
                    "type": e["type"],
                    "status": e["status"],
                    "content": e["content"],
                    "scope": e["scope"],
                    "confidence": e["confidence"],
                    "repo": e.get("repo"),
                    "branch": e.get("branch"),
                    "head_sha": e.get("head_sha"),
                    "scope_tier": e.get("scope_tier"),
                    "node_id": e.get("node_id"),
                    "security_tags": e.get("security_tags") or [],
                    "created_at": e.get("created_at"),
                    "updated_at": e.get("updated_at"),
                    "expires_at": e.get("expires_at"),
                }
                for e in entries
            ],
        }
        return [TextContent(type="text", text=json.dumps(payload, indent=2))]
    except Exception as e:
        ctx.logger.exception("List memory failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_get_memory(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    memory_id = arguments.get("memory_id", "")
    if not repo or not memory_id:
        return [
            TextContent(type="text", text="❌ **Missing required fields:** `repo`, `memory_id`")
        ]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        entry = db.get_memory_entry(memory_id)
        if not entry:
            return [
                TextContent(type="text", text=f"❌ **Memory entry not found:** `{memory_id}`")
            ]
        evidence = db.get_evidence(memory_id)
        payload = {
            "memory_id": memory_id,
            "type": entry["type"],
            "status": entry["status"],
            "content": entry["content"],
            "scope": entry["scope"],
            "confidence": entry["confidence"],
            "node_id": entry.get("node_id"),
            "security_tags": entry.get("security_tags") or [],
            "yaml_definition": entry.get("yaml_definition"),
            "created_at": entry.get("created_at"),
            "updated_at": entry.get("updated_at"),
            "expires_at": entry.get("expires_at"),
            "evidence": [
                {
                    "evidence_type": e["evidence_type"],
                    "content": e["content"],
                    "file_path": e.get("file_path"),
                    "line_number": e.get("line_number"),
                }
                for e in evidence
            ],
        }
        return [TextContent(type="text", text=json.dumps(payload, indent=2))]
    except Exception as e:
        ctx.logger.exception("Get memory failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_save_memory(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    memory_type = arguments.get("type")
    content = arguments.get("content", "")
    scope = arguments.get("scope", "")
    if not repo or not memory_type or not content or not scope:
        return [
            TextContent(
                type="text",
                text="❌ **Missing required fields:** `repo`, `type`, `content`, `scope`",
            )
        ]
    if not ctx.get_db or not ctx.get_memory_manager:
        return [TextContent(type="text", text="❌ **Database/memory manager not available**")]
    try:
        memory_manager = ctx.get_memory_manager()
        memory_id = arguments.get("memory_id")
        db = ctx.get_db()
        git_fields = _git_fields_for_mcp_save(ctx, arguments)
        if memory_id:
            success = db.update_memory_entry(
                memory_id=memory_id,
                content=content,
                scope=scope,
                confidence=arguments.get("confidence"),
                status=arguments.get("status"),
                security_tags=arguments.get("security_tags"),
                yaml_definition=arguments.get("yaml_definition"),
            )
            if not success:
                return [
                    TextContent(type="text", text=f"❌ **Memory entry not found:** `{memory_id}`")
                ]
            # Promoting a candidate → resolve linked promotion finding(s).
            new_status = arguments.get("status")
            if new_status == "active":
                finding_id = arguments.get("finding_id")
                if finding_id:
                    db.resolve_finding(finding_id, "actioned")
                else:
                    for finding in db.list_findings(repo=repo, status="pending", limit=100):
                        if (
                            finding.get("memory_id") == memory_id
                            and finding.get("finding_type") == "promotion_candidate"
                        ):
                            db.resolve_finding(finding["finding_id"], "actioned")
        else:
            if memory_type == "explicit_rule":
                result = memory_manager.create_explicit_rule(
                    repo=repo,
                    content=content,
                    scope=scope,
                    yaml_definition=arguments.get("yaml_definition"),
                    security_tags=arguments.get("security_tags"),
                    branch=git_fields["branch"],
                    head_sha=git_fields["head_sha"],
                    git_dirty=git_fields["git_dirty"],
                    scope_tier=git_fields["scope_tier"],
                )
                memory_id = result["memory_id"]
            elif memory_type == "session_context":
                memory_id, _deduped = memory_manager.create_session_context(
                    repo=repo,
                    content=content,
                    scope=scope,
                    evidence=arguments.get("evidence", []),
                    branch=git_fields["branch"],
                    head_sha=git_fields["head_sha"],
                    git_dirty=git_fields["git_dirty"],
                    scope_tier=git_fields["scope_tier"],
                )
            else:
                memory_id = db.create_memory_entry(
                    repo=repo,
                    memory_type=memory_type,
                    content=content,
                    scope=scope,
                    confidence=arguments.get("confidence", 0.8),
                    security_tags=arguments.get("security_tags"),
                    yaml_definition=arguments.get("yaml_definition"),
                    node_id=arguments.get("node_id"),
                    branch=git_fields["branch"],
                    head_sha=git_fields["head_sha"],
                    git_dirty=git_fields["git_dirty"],
                    scope_tier=git_fields["scope_tier"],
                )
        if arguments.get("evidence"):
            # session_context already stores evidence on create — avoid duplicates.
            skip_evidence = (
                memory_type == "session_context" and not arguments.get("memory_id")
            )
            if not skip_evidence:
                for ev in arguments["evidence"]:
                    db.add_evidence(
                        memory_id=memory_id,
                        evidence_type=ev.get("type", "manual"),
                        content=ev.get("content", ""),
                        file_path=ev.get("file"),
                        line_number=ev.get("line"),
                    )
        payload = {
            "status": "saved",
            "memory_id": memory_id,
            "type": memory_type,
            "content": content,
            "scope": scope,
            "branch": git_fields.get("branch"),
            "head_sha": git_fields.get("head_sha"),
            "scope_tier": git_fields.get("scope_tier"),
            "git_dirty": bool(git_fields.get("git_dirty")),
        }
        return [TextContent(type="text", text=json.dumps(payload, indent=2))]
    except Exception as e:
        ctx.logger.exception("Save memory failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_delete_memory(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    memory_id = arguments.get("memory_id", "")
    action = arguments.get("action", "deprecate")
    if not repo or not memory_id:
        return [
            TextContent(type="text", text="❌ **Missing required fields:** `repo`, `memory_id`")
        ]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        success = db.delete_memory_entry(memory_id, deprecate=(action == "deprecate"))
        if not success:
            return [
                TextContent(type="text", text=f"❌ **Memory entry not found:** `{memory_id}`")
            ]
        return [
            TextContent(
                type="text",
                text=(
                    f"✅ **Memory Entry {action}d**\n\n"
                    f"- **ID:** {memory_id}\n"
                    f"- **Action:** {action}"
                ),
            )
        ]
    except Exception as e:
        ctx.logger.exception("Delete memory failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_ground(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    """One-shot grounding: rules + patterns + working set + top actions."""
    repo = arguments.get("repo") or ""
    workspace_id = arguments.get("workspace_id")
    repos_arg = arguments.get("repos") or []
    if isinstance(repos_arg, str):
        repos_arg = [r.strip() for r in repos_arg.split(",") if r.strip()]
    if not repo and not workspace_id and not repos_arg:
        return [
            TextContent(
                type="text",
                text="❌ **Missing required field:** `repo` (or `workspace_id` / `repos`)",
            )
        ]
    if not ctx.get_memory_manager:
        return [TextContent(type="text", text="❌ **Memory manager not available**")]

    files = arguments.get("files") or []
    if isinstance(files, str):
        files = [f.strip() for f in files.split(",") if f.strip()]
    query = arguments.get("query") or arguments.get("search")
    limit = int(arguments.get("limit", 10))
    queue_limit = int(arguments.get("queue_limit", 5))

    from turingmind_mcp.grounding import compose_ground
    from turingmind_mcp.profile_config import filter_decision_queue_gaps

    def _queue_builder(*, repo: str, limit: int = 15):
        gaps: list = []
        try:
            from turingmind_mcp.v2_engine.handlers import detect_graph_gaps

            gaps.extend(detect_graph_gaps(repo))
        except Exception:
            pass
        if ctx.get_db:
            try:
                for f in ctx.get_db().list_findings(repo=repo, status="pending", limit=50):
                    gaps.append({
                        "gap_type": f["finding_type"],
                        "severity": f["severity"],
                        "node_id": f.get("node_id"),
                        "memory_id": f.get("memory_id"),
                        "finding_id": f["finding_id"],
                        "action": f["action"],
                        "repo": repo,
                    })
            except Exception:
                pass
        gaps = filter_decision_queue_gaps(gaps, scope=arguments.get("scope") or "memory")
        return {"queue": gaps[:limit]}

    try:
        payload = compose_ground(
            ctx.get_memory_manager(),
            repo=repo or None,
            repos=repos_arg or None,
            workspace_id=workspace_id,
            files=list(files),
            query=query,
            limit=limit,
            queue_limit=queue_limit,
            branch=arguments.get("branch"),
            head=arguments.get("head"),
            dirty=arguments.get("dirty"),
            decision_queue_builder=_queue_builder,
        )
        return [TextContent(type="text", text=json.dumps(payload, indent=2))]
    except Exception as e:
        ctx.logger.exception("Ground failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_detect_conflicts(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    memory_id = arguments.get("memory_id", "")
    if not repo or not memory_id:
        return [
            TextContent(type="text", text="❌ **Missing required fields:** `repo`, `memory_id`")
        ]
    if not ctx.get_memory_manager:
        return [TextContent(type="text", text="❌ **Memory manager not available**")]
    try:
        memory_manager = ctx.get_memory_manager()
        conflicts = memory_manager.detect_conflicts(repo, memory_id)
        if not conflicts:
            return [TextContent(type="text", text=json.dumps([]))]
        out = [
            {
                "id": c.get("conflict_id", c.get("id", str(i))),
                "type": c.get("type", ""),
                "severity": c.get("severity", ""),
                "description": c.get("description", ""),
            }
            for i, c in enumerate(conflicts)
        ]
        return [TextContent(type="text", text=json.dumps(out, indent=2))]
    except Exception as e:
        ctx.logger.exception("Detect conflicts failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_resolve_conflict(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    conflict_id = arguments.get("conflict_id", "")
    strategy = arguments.get("strategy", "")
    if not repo or not conflict_id or not strategy:
        return [
            TextContent(
                type="text",
                text="❌ **Missing required fields:** `repo`, `conflict_id`, `strategy`",
            )
        ]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        success = db.resolve_conflict(conflict_id, strategy)
        if not success:
            return [
                TextContent(type="text", text=f"❌ **Conflict not found:** `{conflict_id}`")
            ]
        return [
            TextContent(
                type="text",
                text=(
                    f"✅ **Conflict Resolved**\n\n"
                    f"- **Conflict ID:** {conflict_id}\n"
                    f"- **Strategy:** {strategy}"
                ),
            )
        ]
    except Exception as e:
        ctx.logger.exception("Resolve conflict failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_simulate_impact(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    memory_ids = arguments.get("memory_ids", [])
    test_files = arguments.get("test_files")
    if not repo:
        return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]
    impact_obj = {
        "repo": repo,
        "memory_ids": memory_ids,
        "test_files": test_files if test_files else [],
        "simulated": True,
    }
    return [TextContent(type="text", text=json.dumps(impact_obj, indent=2))]


async def handle_explain_decision(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    issue_id = arguments.get("issue_id")
    file_path = arguments.get("file")
    line = arguments.get("line")
    if not repo:
        return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        usage = db.get_memory_usage(
            repo=repo, issue_id=issue_id, file_path=file_path, line_number=line
        )
        if not usage:
            return [
                TextContent(type="text", text="ℹ️ **No memory usage found for this decision**")
            ]
        total_weight = sum(u["weight"] for u in usage)
        return [
            TextContent(
                type="text",
                text=(
                    f"💡 **Decision Explanation**\n\n"
                    f"- **Total influence:** {total_weight:.2f}\n"
                    + "\n".join(
                        f"- **{u['type']}** ({u['weight']*100:.0f}%): {u['content'][:60]}..."
                        for u in usage[:10]
                    )
                ),
            )
        ]
    except Exception as e:
        ctx.logger.exception("Explain decision failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]


async def handle_get_memory_stats(arguments: dict, ctx: ToolContext) -> list[TextContent]:
    repo = arguments.get("repo", "")
    if not repo:
        return [TextContent(type="text", text="❌ **Missing required field:** `repo`")]
    if not ctx.get_db:
        return [TextContent(type="text", text="❌ **Database not available**")]
    try:
        db = ctx.get_db()
        with db.transaction() as cursor:
            cursor.execute(
                """
                SELECT type, status, COUNT(*) as count
                FROM memory_entries
                WHERE repo = ?
                GROUP BY type, status
                """,
                (repo,),
            )
            stats = cursor.fetchall()
        stats_obj = {
            "repo": repo,
            "by_type_status": [
                {"type": row[0], "status": row[1], "count": row[2]}
                for row in stats
            ],
        }
        return [TextContent(type="text", text=json.dumps(stats_obj, indent=2))]
    except Exception as e:
        ctx.logger.exception("Get memory stats failed")
        return [TextContent(type="text", text=f"❌ **Failed:** {type(e).__name__}: {e}")]
