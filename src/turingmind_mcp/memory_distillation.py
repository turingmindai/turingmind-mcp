"""Optional LLM distillation at queue adjudication time.

Drafts human-reviewable memory text for reconciliation findings. Never
auto-activates — agents/humans must explicitly promote via existing flows.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from .database import MemoryDatabase
from .secret_scrub import scrub_secrets

logger = logging.getLogger(__name__)


class DistillationError(Exception):
    """Raised when draft generation cannot proceed."""


def distillation_enabled() -> bool:
    """True unless explicitly disabled via env."""
    return os.getenv("TURINGMIND_LLM_DISTILL", "1").strip() not in ("0", "false", "no")


def _get_finding(db: MemoryDatabase, finding_id: str) -> Dict[str, Any]:
    cursor = db.conn.cursor()
    row = cursor.execute(
        "SELECT * FROM reconcile_findings WHERE finding_id = ?",
        (finding_id,),
    ).fetchone()
    if not row:
        raise DistillationError(f"Finding not found: {finding_id}")
    result = dict(row)
    if result.get("evidence"):
        try:
            import json

            result["evidence"] = json.loads(result["evidence"])
        except (json.JSONDecodeError, TypeError):
            pass
    return result


def _observation_snippets(db: MemoryDatabase, evidence: List[dict]) -> List[str]:
    snippets: List[str] = []
    for ev in evidence or []:
        if ev.get("type") != "observation":
            continue
        obs_id = ev.get("content")
        if not obs_id:
            continue
        row = db.conn.execute(
            "SELECT event_type, content FROM observations WHERE observation_id = ?",
            (obs_id,),
        ).fetchone()
        if row:
            snippets.append(f"[{row['event_type']}] {row['content']}")
    return snippets


def _memory_snippets(db: MemoryDatabase, memory_ids: List[str]) -> List[str]:
    snippets: List[str] = []
    for mid in memory_ids:
        entry = db.get_memory_entry(mid)
        if entry:
            snippets.append(
                f"[{entry['type']} scope={entry.get('scope', 'repo')}] "
                f"{entry['content']}"
            )
    return snippets


def _gather_context(db: MemoryDatabase, finding: Dict[str, Any]) -> Dict[str, Any]:
    evidence = finding.get("evidence") or []
    memory_ids: List[str] = []
    if finding.get("memory_id"):
        memory_ids.append(finding["memory_id"])
    for ev in evidence:
        if ev.get("type") == "memory" and ev.get("content"):
            memory_ids.append(ev["content"])

    return {
        "finding_type": finding["finding_type"],
        "action": finding["action"],
        "observations": _observation_snippets(db, evidence),
        "memories": _memory_snippets(db, list(dict.fromkeys(memory_ids))),
    }


def _build_prompt(context: Dict[str, Any]) -> str:
    obs_block = "\n".join(f"- {s}" for s in context["observations"]) or "- (none)"
    mem_block = "\n".join(f"- {s}" for s in context["memories"]) or "- (none)"
    return f"""Review this memory-system finding and write ONE concise draft memory entry (1-3 sentences).

Finding type: {context['finding_type']}
Suggested action: {context['action']}

Related observations:
{obs_block}

Related memories:
{mem_block}

Rules:
- Output ONLY the draft memory text (plain prose, no markdown, no JSON).
- Do not include secrets, tokens, or API keys.
- The draft is a proposal — it will NOT be saved automatically."""


async def _call_llm(prompt: str) -> str:
    from .llm.config import get_llm_provider

    provider = get_llm_provider("azure")
    if not provider:
        raise DistillationError(
            "LLM not configured. Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, "
            "and AZURE_OPENAI_DEPLOYMENT_NAME."
        )

    # Override JSON-oriented system prompt with plain-text distillation instructions.
    import httpx

    endpoint = provider.endpoint.rstrip("/")
    url = (
        f"{endpoint}/openai/deployments/{provider.deployment_name}"
        f"/chat/completions?api-version={provider.api_version}"
    )
    body = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You distill noisy engineering observations into clear, "
                    "durable memory entries. Respond with plain text only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 600,
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            url,
            headers={"Content-Type": "application/json", "api-key": provider.api_key},
            json=body,
        )
        if not response.is_success:
            raise DistillationError(
                f"LLM request failed: {response.status_code} {response.text[:200]}"
            )
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()


async def draft_finding_async(
    db: MemoryDatabase,
    finding_id: str,
) -> Dict[str, Any]:
    """Generate a review-only draft for a reconciliation finding."""
    if not distillation_enabled():
        raise DistillationError("LLM distillation disabled (TURINGMIND_LLM_DISTILL=0)")

    finding = _get_finding(db, finding_id)
    if finding.get("status") != "pending":
        raise DistillationError(
            f"Finding {finding_id} is {finding.get('status')} — only pending findings can be drafted"
        )

    context = _gather_context(db, finding)
    prompt = _build_prompt(context)
    raw = await _call_llm(prompt)
    draft = scrub_secrets(raw.strip().strip('"')) or ""

    return {
        "finding_id": finding_id,
        "finding_type": finding["finding_type"],
        "repo": finding["repo"],
        "draft_content": draft,
        "observation_count": len(context["observations"]),
        "memory_count": len(context["memories"]),
        "review_required": True,
        "message": "Draft only — not saved. Promote via remember/queue resolve after review.",
    }


def draft_finding(db: MemoryDatabase, finding_id: str) -> Dict[str, Any]:
    """Sync wrapper for REST/CLI callers."""
    return asyncio.run(draft_finding_async(db, finding_id))


_SKIP_COMMIT_PREFIXES = (
    "wip",
    "tmp",
    "temp",
    "chore:",
    "style:",
    "format",
    "merge ",
    "merge branch",
)


def _commit_worth_promoting(message: str, files: List[str]) -> bool:
    msg = (message or "").strip()
    if len(msg) < 12:
        return False
    lower = msg.lower()
    if any(lower.startswith(p) for p in _SKIP_COMMIT_PREFIXES):
        return False
    code_files = [
        f
        for f in (files or [])
        if f
        and not f.endswith((".md", ".txt", ".json", ".yml", ".yaml", ".lock"))
        and ".ds_store" not in f.lower()
    ]
    return bool(code_files) or len(msg) >= 40


def _heuristic_commit_drafts(message: str, files: List[str]) -> List[Dict[str, str]]:
    """Deterministic 0–2 draft patterns from commit message + paths (no LLM)."""
    msg = scrub_secrets((message or "").strip()) or ""
    if not msg:
        return []
    # Prefer first code file as scope; else package/dir of first path
    scope = "repo"
    for f in files or []:
        if f.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java")):
            scope = f
            break
    if scope == "repo" and files:
        parts = files[0].replace("\\", "/").split("/")
        if len(parts) > 1:
            scope = "/".join(parts[:-1]) or "repo"

    drafts = [
        {
            "content": f"From commit: {msg[:280]}",
            "scope": scope,
        }
    ]
    # Second candidate only when commit spans a clear package + message is rich
    dirs = {
        "/".join(f.replace("\\", "/").split("/")[:-1])
        for f in (files or [])
        if "/" in f.replace("\\", "/")
    }
    if len(dirs) == 1 and len(msg) >= 60:
        only = next(iter(dirs))
        if only and only != scope:
            drafts.append(
                {
                    "content": f"Commit touched `{only}`: {msg[:200]}",
                    "scope": only,
                }
            )
    return drafts[:2]


def propose_commit_candidates(
    db: MemoryDatabase,
    *,
    repo: str,
    message: str,
    files: Optional[List[str]] = None,
    branch: Optional[str] = None,
    head_sha: Optional[str] = None,
    use_llm: bool = False,
) -> Dict[str, Any]:
    """Create 0–2 ``learned_pattern`` candidates + promotion queue items from a commit.

    Extends the existing distillation surface — does not auto-activate.
    """
    import hashlib

    files = list(files or [])
    if not _commit_worth_promoting(message, files):
        return {
            "repo": repo,
            "candidates": [],
            "skipped": True,
            "reason": "commit_not_worth_promoting",
        }

    drafts = _heuristic_commit_drafts(message, files)
    if use_llm and distillation_enabled() and drafts:
        try:
            prompt = (
                "Write ONE concise learned_pattern (1-2 sentences) from this commit.\n"
                f"Message: {message[:400]}\n"
                f"Files: {', '.join(files[:15])}\n"
                "Output ONLY the pattern text."
            )
            raw = asyncio.run(_call_llm(prompt))
            text = scrub_secrets(raw.strip().strip('"')) or ""
            if text:
                drafts[0]["content"] = text
        except Exception as exc:
            logger.warning("Commit LLM distill failed; using heuristic: %s", exc)

    candidates: List[Dict[str, Any]] = []
    for draft in drafts:
        content = draft["content"]
        scope = draft["scope"]
        dedup = hashlib.sha256(f"commit|{repo}|{content[:120]}".encode()).hexdigest()[:16]
        # Skip if identical candidate already pending
        existing = db.list_findings(repo=repo, status="pending", limit=50)
        if any(
            f.get("dedup_key") == dedup or (f.get("action") or "").find(content[:80]) >= 0
            for f in existing
            if f.get("finding_type") == "promotion_candidate"
        ):
            continue

        memory_id = db.create_memory_entry(
            repo=repo,
            memory_type="learned_pattern",
            content=content,
            scope=scope,
            confidence=0.45,
            status="candidate",
            created_by="distill:commit",
            branch=branch,
            head_sha=head_sha,
        )
        finding_id = db.create_finding(
            repo=repo,
            finding_type="promotion_candidate",
            severity="medium",
            action=f"Promote commit-derived pattern? {content[:200]}",
            dedup_key=dedup,
            evidence=[
                {"type": "commit_message", "content": (message or "")[:300]},
                {"type": "files", "content": ", ".join(files[:20])},
            ],
            memory_id=memory_id,
        )
        candidates.append(
            {
                "memory_id": memory_id,
                "finding_id": finding_id,
                "content": content,
                "scope": scope,
                "status": "candidate",
            }
        )

    return {
        "repo": repo,
        "candidates": candidates,
        "skipped": False,
        "review_required": True,
        "message": "Candidates only — not active. Confirm via promote or stop-hook follow-up.",
    }
