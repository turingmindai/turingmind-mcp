"""Live memory-engine effectiveness assessment (Gate 2 / plugin smoke).

Runs against the local V2 API (port 8477) and ``~/.turingmind/memory.db`` to
score each capture → recall layer documented in the branch-memory scorecard.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import httpx

logger = logging.getLogger(__name__)

LayerStatus = Literal["pass", "warn", "fail", "skip"]
PROBE_PREFIX = "[memory-assess-probe]"


@dataclass
class LayerResult:
    """Outcome for one scorecard layer."""

    layer: str
    status: LayerStatus
    notes: str
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AssessmentReport:
    """Full effectiveness report."""

    repo: str
    api_url: str
    workspace_dir: Optional[str]
    layers: List[LayerResult]
    probe_memory_ids: List[str] = field(default_factory=list)

    def summary_table(self) -> str:
        """Markdown-friendly scorecard table."""
        icon = {"pass": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭️"}
        lines = ["| Layer | Status | Notes |", "|-------|--------|-------|"]
        for layer in self.layers:
            lines.append(
                f"| {layer.layer} | {icon.get(layer.status, '?')} {layer.status} | {layer.notes} |"
            )
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo": self.repo,
            "api_url": self.api_url,
            "workspace_dir": self.workspace_dir,
            "layers": [asdict(layer) for layer in self.layers],
            "probe_memory_ids": self.probe_memory_ids,
        }


def default_db_path() -> Path:
    return Path(os.environ.get("TURINGMIND_MEMORY_DB", Path.home() / ".turingmind" / "memory.db"))


def default_api_url() -> str:
    return os.environ.get("TURINGMIND_LOCAL_API_URL", "http://127.0.0.1:8477").rstrip("/")


def load_env_file() -> None:
    """Load ``~/.turingmind/env`` into os.environ (does not override existing)."""
    env_path = Path.home() / ".turingmind" / "env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def resolve_workspace_dir(explicit: Optional[Path] = None) -> Optional[Path]:
    if explicit is not None:
        return explicit.resolve()
    env = os.environ.get("TURINGMIND_WORKSPACE_DIR", "").strip()
    return Path(env).resolve() if env else None


def resolve_repo(explicit: Optional[str] = None, workspace: Optional[Path] = None) -> Optional[str]:
    if explicit and explicit.strip():
        return explicit.strip()
    env_repo = os.environ.get("TURINGMIND_DEFAULT_REPO", "").strip()
    if env_repo:
        return env_repo
    if workspace and (workspace / ".git").exists():
        remote = subprocess.run(
            ["git", "-C", str(workspace), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
        if remote.returncode == 0 and remote.stdout.strip():
            return _remote_to_repo(remote.stdout.strip())
    return None


def _remote_to_repo(url: str) -> str:
    if url.startswith("git@"):
        host_path = url.split(":", 1)[-1]
    else:
        host_path = url.split("://", 1)[-1].split("/", 1)[-1]
    return host_path.removesuffix(".git")


def _db_counts(db_path: Path, repo: str) -> Dict[str, int]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM observations WHERE repo=? AND event_type='chat_exchange'",
            (repo,),
        )
        chat = int(cur.fetchone()[0])
        cur.execute(
            "SELECT COUNT(*) FROM observations WHERE repo=? AND event_type IN ('edit_cluster','file_edit_cluster')",
            (repo,),
        )
        edit_obs = int(cur.fetchone()[0])
        cur.execute(
            "SELECT COUNT(*) FROM memory_entries WHERE repo=? AND type='session_context'",
            (repo,),
        )
        session_ctx = int(cur.fetchone()[0])
        cur.execute(
            "SELECT COUNT(*) FROM observations WHERE repo=? AND status='pending'",
            (repo,),
        )
        pending = int(cur.fetchone()[0])
        return {
            "chat_exchange_observations": chat,
            "edit_cluster_observations": edit_obs,
            "session_context_memories": session_ctx,
            "pending_observations": pending,
        }
    finally:
        conn.close()


def _workspace_git_ok(workspace: Optional[Path]) -> tuple[bool, Optional[str]]:
    if workspace is None:
        return False, "TURINGMIND_WORKSPACE_DIR is not set"
    if not (workspace / ".git").exists():
        return False, f"{workspace} is not a git repository root"
    branch = subprocess.run(
        ["git", "-C", str(workspace), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=False,
    )
    if branch.returncode != 0 or not branch.stdout.strip():
        return False, f"Could not read branch from {workspace}"
    return True, branch.stdout.strip()


def _git_payload_for_probe(git_payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Build API git blob with a valid scope_tier for the current tree state."""
    if not git_payload:
        return None
    from .git_context import derive_scope_tier

    branch = git_payload.get("branch")
    dirty = bool(git_payload.get("dirty", False))
    return {
        **git_payload,
        "scope_tier": derive_scope_tier(branch, dirty),
    }


def _cleanup_probes(db_path: Path, repo: str, memory_ids: List[str]) -> None:
    if not memory_ids or not db_path.exists():
        return
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        for mid in memory_ids:
            cur.execute("DELETE FROM memory_evidence WHERE memory_id=?", (mid,))
            cur.execute("DELETE FROM memory_entries WHERE memory_id=? AND repo=?", (mid, repo))
        conn.commit()
    finally:
        conn.close()


def _api_health(client: httpx.Client) -> bool:
    try:
        response = client.get("/api/v2/health", timeout=5.0)
        return response.status_code == 200 and response.json().get("status") == "ok"
    except httpx.HTTPError:
        return False


def assess_chat_capture(db_path: Path, repo: str) -> LayerResult:
    counts = _db_counts(db_path, repo)
    chat = counts.get("chat_exchange_observations", 0)
    if chat >= 1:
        return LayerResult(
            layer="Chat capture",
            status="pass",
            notes=f"{chat} chat_exchange observation(s) for {repo}",
            evidence=counts,
        )
    return LayerResult(
        layer="Chat capture",
        status="fail",
        notes="No chat_exchange observations — chat poller or plugin may be inactive",
        evidence=counts,
    )


def assess_file_edit_memory(
    client: httpx.Client,
    db_path: Path,
    repo: str,
    git_payload: Optional[Dict[str, Any]],
    probe_ids: List[str],
) -> LayerResult:
    counts = _db_counts(db_path, repo)
    live_hook = counts.get("session_context_memories", 0) > 0 or counts.get(
        "edit_cluster_observations", 0
    ) > 0

    probe_content = f"{PROBE_PREFIX} file-edit hook simulation"
    payload: Dict[str, Any] = {
        "repo": repo,
        "type": "session_context",
        "content": probe_content,
        "scope": "repo",
        "evidence": [{"type": "edit_cluster", "content": "assess/targeted_fix"}],
        "ttl_hours": 1,
    }
    if git_payload:
        payload["git"] = git_payload

    api_ok = False
    memory_id: Optional[str] = None
    try:
        response = client.post("/api/v2/memory", json=payload, timeout=10.0)
        if response.status_code == 200:
            memory_id = response.json().get("memory_id")
            if memory_id:
                probe_ids.append(memory_id)
            api_ok = True
    except httpx.HTTPError as exc:
        logger.warning("File-edit simulation failed: %s", exc)

    if live_hook and api_ok:
        return LayerResult(
            layer="File-edit → memory",
            status="pass",
            notes=(
                f"Hook artifacts present ({counts.get('session_context_memories', 0)} session_context, "
                f"{counts.get('edit_cluster_observations', 0)} edit_cluster obs); API path OK"
            ),
            evidence={**counts, "api_simulation": True, "probe_memory_id": memory_id},
        )
    if api_ok:
        return LayerResult(
            layer="File-edit → memory",
            status="warn",
            notes=(
                "API hook path works; no live hook artifacts yet — edit 2+ related files, "
                "wait 5s debounce, re-run assess"
            ),
            evidence={**counts, "api_simulation": True, "probe_memory_id": memory_id},
        )
    if live_hook:
        return LayerResult(
            layer="File-edit → memory",
            status="warn",
            notes="Live hook memories found but API simulation failed",
            evidence=counts,
        )
    return LayerResult(
        layer="File-edit → memory",
        status="fail",
        notes="No hook artifacts and API simulation failed",
        evidence=counts,
    )


def assess_chat_durable_memory(client: httpx.Client, db_path: Path, repo: str) -> LayerResult:
    counts = _db_counts(db_path, repo)
    chat = counts.get("chat_exchange_observations", 0)
    if chat == 0:
        return LayerResult(
            layer="Chat → durable memory",
            status="skip",
            notes="No chat observations to assess",
            evidence=counts,
        )

    stats: Dict[str, Any] = {}
    try:
        response = client.post("/api/v2/reconcile", json={"repo": repo}, timeout=30.0)
        if response.status_code == 200:
            stats = response.json()
    except httpx.HTTPError as exc:
        return LayerResult(
            layer="Chat → durable memory",
            status="fail",
            notes=f"Reconcile request failed: {exc}",
            evidence=counts,
        )

    mined = int(stats.get("patterns_mined", 0))
    accepted = int(stats.get("observations_accepted", 0))
    if mined > 0 or accepted > 0:
        return LayerResult(
            layer="Chat → durable memory",
            status="pass",
            notes=f"Reconcile promoted chat funnel: patterns_mined={mined}, accepted={accepted}",
            evidence={**counts, **stats},
        )

    return LayerResult(
        layer="Chat → durable memory",
        status="warn",
        notes=(
            f"By design: {chat} chat_exchange obs stay draft until recurrence/distillation "
            f"(TC-C01); reconcile patterns_mined=0"
        ),
        evidence={**counts, **stats},
    )


def assess_mcp_crud(
    client: httpx.Client,
    repo: str,
    probe_ids: List[str],
) -> LayerResult:
    token = uuid.uuid4().hex[:8]
    content = f"{PROBE_PREFIX} MCP/REST round-trip {token}"
    save_payload = {
        "repo": repo,
        "type": "explicit_rule",
        "content": content,
        "scope": "repo",
        "confidence": 0.85,
    }
    try:
        save = client.post("/api/v2/memory", json=save_payload, timeout=10.0)
        if save.status_code != 200:
            return LayerResult(
                layer="MCP list/save/recall",
                status="fail",
                notes=f"POST /memory failed: {save.status_code}",
            )
        memory_id = save.json().get("memory_id")
        if memory_id:
            probe_ids.append(memory_id)

        listed = client.get("/api/v2/memory", params={"repo": repo, "search": token}, timeout=10.0)
        if listed.status_code != 200:
            return LayerResult(
                layer="MCP list/save/recall",
                status="fail",
                notes=f"GET /memory failed: {listed.status_code}",
            )
        entries = listed.json().get("entries") or []
        if not any(e.get("memory_id") == memory_id for e in entries):
            return LayerResult(
                layer="MCP list/save/recall",
                status="fail",
                notes="Saved memory not returned by list/search",
            )

        relevant = client.get(
            "/api/v2/memory/relevant",
            params={"repo": repo, "files": "README.md"},
            timeout=10.0,
        )
        if relevant.status_code != 200:
            return LayerResult(
                layer="MCP list/save/recall",
                status="warn",
                notes="Save/list OK; GET /memory/relevant failed",
                evidence={"memory_id": memory_id},
            )

        return LayerResult(
            layer="MCP list/save/recall",
            status="pass",
            notes="REST save, list/search, and relevant recall round-trip OK",
            evidence={"memory_id": memory_id},
        )
    except httpx.HTTPError as exc:
        return LayerResult(
            layer="MCP list/save/recall",
            status="fail",
            notes=f"HTTP error: {exc}",
        )


def assess_branch_recall_explicit(
    client: httpx.Client,
    repo: str,
    git_payload: Optional[Dict[str, Any]],
    probe_ids: List[str],
) -> LayerResult:
    if not git_payload or not git_payload.get("branch"):
        return LayerResult(
            layer="Branch recall (explicit branch)",
            status="skip",
            notes="No git context available for branch isolation probe",
        )

    branch = git_payload["branch"]
    other = "feature/memory-assess-other"
    scope_file = "turingmind-mcp/src/turingmind_mcp/memory_effectiveness_assess.py"
    content = f"{PROBE_PREFIX} branch-scoped on {branch}"

    payload = {
        "repo": repo,
        "type": "explicit_rule",
        "content": content,
        "scope": scope_file,
        "confidence": 0.9,
        "git": _git_payload_for_probe(git_payload),
    }
    try:
        save = client.post("/api/v2/memory", json=payload, timeout=10.0)
        if save.status_code != 200:
            return LayerResult(
                layer="Branch recall (explicit branch)",
                status="fail",
                notes=f"Branch memory save failed: {save.status_code}",
            )
        memory_id = save.json().get("memory_id")
        if memory_id:
            probe_ids.append(memory_id)

        on_branch = client.get(
            "/api/v2/memory/relevant",
            params={"repo": repo, "files": scope_file, "branch": branch},
            timeout=10.0,
        )
        off_branch = client.get(
            "/api/v2/memory/relevant",
            params={"repo": repo, "files": scope_file, "branch": other},
            timeout=10.0,
        )
        if on_branch.status_code != 200 or off_branch.status_code != 200:
            return LayerResult(
                layer="Branch recall (explicit branch)",
                status="fail",
                notes="Relevant recall requests failed",
            )

        on_ids = {e["memory_id"] for e in on_branch.json().get("entries", [])}
        off_ids = {e["memory_id"] for e in off_branch.json().get("entries", [])}
        if memory_id in on_ids and memory_id not in off_ids:
            return LayerResult(
                layer="Branch recall (explicit branch)",
                status="pass",
                notes=f"Branch memory visible on {branch}, excluded on {other}",
                evidence={"memory_id": memory_id, "branch": branch},
            )
        return LayerResult(
            layer="Branch recall (explicit branch)",
            status="fail",
            notes=f"Isolation failed: on_branch={memory_id in on_ids}, off_branch={memory_id in off_ids}",
            evidence={"on_ids": list(on_ids), "off_ids": list(off_ids)},
        )
    except httpx.HTTPError as exc:
        return LayerResult(
            layer="Branch recall (explicit branch)",
            status="fail",
            notes=f"HTTP error: {exc}",
        )


def assess_branch_recall_inferred(
    client: httpx.Client,
    repo: str,
    workspace: Optional[Path],
    git_payload: Optional[Dict[str, Any]],
    probe_ids: List[str],
) -> LayerResult:
    ok, detail = _workspace_git_ok(workspace)
    if not ok:
        return LayerResult(
            layer="Branch recall (inferred)",
            status="fail",
            notes=f"{detail}. Set TURINGMIND_WORKSPACE_DIR to the git repo root.",
            evidence={"workspace_dir": str(workspace) if workspace else None},
        )

    if not git_payload or not git_payload.get("branch"):
        return LayerResult(
            layer="Branch recall (inferred)",
            status="fail",
            notes=f"Git root OK ({detail}) but could not build git payload",
        )

    scope_file = "turingmind-mcp/src/turingmind_mcp/branch_recall.py"
    content = f"{PROBE_PREFIX} inferred branch recall on {git_payload['branch']}"
    payload = {
        "repo": repo,
        "type": "explicit_rule",
        "content": content,
        "scope": scope_file,
        "confidence": 0.9,
        "git": _git_payload_for_probe(git_payload),
    }
    try:
        save = client.post("/api/v2/memory", json=payload, timeout=10.0)
        if save.status_code != 200:
            return LayerResult(
                layer="Branch recall (inferred)",
                status="fail",
                notes=f"Probe save failed: {save.status_code}",
            )
        memory_id = save.json().get("memory_id")
        if memory_id:
            probe_ids.append(memory_id)

        inferred = client.get(
            "/api/v2/memory/relevant",
            params={"repo": repo, "files": scope_file},
            timeout=10.0,
        )
        if inferred.status_code != 200:
            return LayerResult(
                layer="Branch recall (inferred)",
                status="fail",
                notes=f"GET /memory/relevant (no branch param) failed: {inferred.status_code}",
            )

        ids = {e["memory_id"] for e in inferred.json().get("entries", [])}
        if memory_id in ids:
            return LayerResult(
                layer="Branch recall (inferred)",
                status="pass",
                notes=f"Server inferred branch {git_payload['branch']} from workspace",
                evidence={"workspace_dir": str(workspace), "branch": git_payload["branch"]},
            )

        return LayerResult(
            layer="Branch recall (inferred)",
            status="fail",
            notes=(
                f"Workspace is git root ({workspace}) but branch-scoped probe not recalled "
                "without explicit branch — restart API after fixing env"
            ),
            evidence={"workspace_dir": str(workspace), "returned_ids": list(ids)},
        )
    except httpx.HTTPError as exc:
        return LayerResult(
            layer="Branch recall (inferred)",
            status="fail",
            notes=f"HTTP error: {exc}",
        )


def run_assessment(
    *,
    repo: Optional[str] = None,
    api_url: Optional[str] = None,
    workspace_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    cleanup_probes: bool = True,
) -> AssessmentReport:
    """Run all scorecard layers and return structured results."""
    load_env_file()
    api = (api_url or default_api_url()).rstrip("/")
    db = db_path or default_db_path()
    workspace = resolve_workspace_dir(workspace_dir)
    resolved_repo = resolve_repo(repo, workspace)

    if not resolved_repo:
        raise ValueError(
            "Could not resolve repo — pass --repo or set TURINGMIND_DEFAULT_REPO / git remote"
        )

    probe_ids: List[str] = []
    layers: List[LayerResult] = []

    with httpx.Client(base_url=api, timeout=30.0) as client:
        if not _api_health(client):
            raise ConnectionError(
                f"API not reachable at {api} — start with `turingmind install-api-daemon status`"
            )

        git_payload: Optional[Dict[str, Any]] = None
        if workspace and (workspace / ".git").exists():
            from .git_context import collect_git_context

            ctx = collect_git_context(workspace)
            if ctx is not None:
                git_payload = {
                    "branch": ctx.branch,
                    "head": ctx.head,
                    "dirty": ctx.dirty,
                    "default_branch": ctx.default_branch,
                }

        layers.append(assess_chat_capture(db, resolved_repo))
        layers.append(assess_file_edit_memory(client, db, resolved_repo, git_payload, probe_ids))
        layers.append(assess_chat_durable_memory(client, db, resolved_repo))
        layers.append(assess_mcp_crud(client, resolved_repo, probe_ids))
        layers.append(assess_branch_recall_explicit(client, resolved_repo, git_payload, probe_ids))
        layers.append(assess_branch_recall_inferred(client, resolved_repo, workspace, git_payload, probe_ids))

    if cleanup_probes and probe_ids:
        _cleanup_probes(db, resolved_repo, probe_ids)

    return AssessmentReport(
        repo=resolved_repo,
        api_url=api,
        workspace_dir=str(workspace) if workspace else None,
        layers=layers,
        probe_memory_ids=probe_ids if not cleanup_probes else [],
    )


def format_report(report: AssessmentReport, *, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(report.to_dict(), indent=2)
    lines = [
        f"Memory effectiveness — {report.repo}",
        f"API: {report.api_url}",
        f"Workspace: {report.workspace_dir or '(unset)'}",
        "",
        report.summary_table(),
    ]
    failed = [layer for layer in report.layers if layer.status == "fail"]
    if failed:
        lines.extend(["", f"❌ {len(failed)} layer(s) failed — see notes above"])
    else:
        warns = sum(1 for layer in report.layers if layer.status == "warn")
        lines.extend(["", f"✅ No hard failures ({warns} warning(s))"])
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Assess live memory-engine effectiveness")
    parser.add_argument("--repo", help="Repository (owner/repo)")
    parser.add_argument("--api-url", help="V2 API base URL (default http://127.0.0.1:8477)")
    parser.add_argument("--workspace", type=Path, help="Git workspace for branch inference")
    parser.add_argument("--db", type=Path, help="Path to memory.db")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of table")
    parser.add_argument(
        "--keep-probes",
        action="store_true",
        help="Do not delete probe memories after run",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        report = run_assessment(
            repo=args.repo,
            api_url=args.api_url,
            workspace_dir=args.workspace,
            db_path=args.db,
            cleanup_probes=not args.keep_probes,
        )
    except (ValueError, ConnectionError) as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 1

    print(format_report(report, as_json=args.json))
    return 1 if any(layer.status == "fail" for layer in report.layers) else 0


if __name__ == "__main__":
    raise SystemExit(main())
