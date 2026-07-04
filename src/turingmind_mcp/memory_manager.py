"""
Memory management handlers for different memory categories.

Handles:
- Repo Facts (auto-extracted)
- Learned Patterns (auto-learned from feedback)
- Explicit Rules (user-defined)
- Session Context (ephemeral)
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .database import MemoryDatabase

logger = logging.getLogger("turingmind-mcp")


class MemoryManager:
    """Manages memory entries across all categories."""

    def __init__(self, db: MemoryDatabase):
        """Initialize memory manager."""
        self.db = db

    # Repo Facts (auto-extracted, read-only)
    def extract_repo_facts(
        self,
        repo: str,
        files: List[str],
        project_root: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:
        """Extract repo facts from an indexed file list (bootstrap / sync)."""
        facts: List[Dict[str, Any]] = []
        if not files:
            return facts

        normalized = [f.replace("\\", "/") for f in files]
        basenames = {Path(f).name for f in normalized}
        ext_counts = Counter(Path(f).suffix.lower() for f in normalized if Path(f).suffix)

        def _add(content: str, confidence: float, evidence: List[str]) -> None:
            facts.append({
                "type": "repo_fact",
                "content": content,
                "scope": "repo",
                "confidence": confidence,
                "evidence": evidence,
            })

        if "package.json" in basenames or any(n.endswith("/package.json") for n in normalized):
            _add("Node.js / JavaScript project (package.json present)", 0.9, ["package.json"])
        if "pyproject.toml" in basenames or "requirements.txt" in basenames:
            _add("Python project (pyproject.toml or requirements.txt present)", 0.9,
                 [n for n in ("pyproject.toml", "requirements.txt") if n in basenames])
        if "go.mod" in basenames:
            _add("Go module project (go.mod present)", 0.9, ["go.mod"])
        if "Cargo.toml" in basenames:
            _add("Rust project (Cargo.toml present)", 0.9, ["Cargo.toml"])
        if self._detect_monorepo(normalized):
            _add("Monorepo structure detected (multiple package manifests)", 0.95,
                 ["multiple package.json or workspace roots"])

        top_ext = ext_counts.most_common(3)
        if top_ext:
            langs = ", ".join(f"{ext or '(no ext)'}×{count}" for ext, count in top_ext)
            _add(f"Primary file types: {langs}", 0.85, [f"{len(normalized)} files indexed"])

        if len(normalized) >= 500:
            _add(f"Large codebase (~{len(normalized)} indexed source files)", 0.8,
                 ["bootstrap file count"])

        if project_root:
            root = Path(project_root)
            for marker, label in (
                ("kubernetes", "Kubernetes manifests present"),
                (".github/workflows", "GitHub Actions CI present"),
                ("docker-compose.yml", "Docker Compose present"),
                ("Dockerfile", "Dockerfile present"),
            ):
                if (root / marker).exists():
                    _add(label, 0.85, [marker])

        return facts

    def persist_repo_facts(self, repo: str, facts: List[Dict[str, Any]]) -> List[str]:
        """Upsert repo_fact memories — skip duplicates by exact content."""
        existing = self.db.list_memory_entries(
            repo, memory_type="repo_fact", status="active"
        )
        known = {e["content"] for e in existing}
        created: List[str] = []
        for fact in facts:
            content = fact.get("content", "").strip()
            if not content or content in known:
                continue
            memory_id = self.db.create_memory_entry(
                repo=repo,
                memory_type="repo_fact",
                content=content,
                scope=fact.get("scope", "repo"),
                confidence=fact.get("confidence", 0.9),
                status="active",
                created_by="bootstrap:repo_facts",
            )
            for ev in fact.get("evidence") or []:
                if isinstance(ev, str):
                    self.db.add_evidence(memory_id, "bootstrap", ev)
            known.add(content)
            created.append(memory_id)
        return created

    def _detect_monorepo(self, files: List[str]) -> bool:
        """Detect if repository is a monorepo."""
        pkg_count = sum(1 for f in files if f.endswith("package.json") or "/package.json" in f)
        return pkg_count > 1

    def get_repo_facts(self, repo: str) -> List[Dict[str, Any]]:
        """Get all repo facts for a repository."""
        return self.db.list_memory_entries(repo, memory_type="repo_fact", status="active")

    # Learned Patterns (auto-learned from feedback)
    def learn_pattern_from_feedback(
        self,
        repo: str,
        pattern: str,
        file_path: Optional[str],
        reason: Optional[str],
    ) -> Optional[str]:
        """Learn a pattern from false positive feedback."""
        # Check if pattern already exists
        existing = self._find_existing_pattern(repo, pattern, file_path)

        if existing:
            # Increase confidence, add evidence
            current_confidence = existing.get("confidence", 0.7)
            new_confidence = min(current_confidence + 0.05, 0.95)

            self.db.update_memory_entry(existing["memory_id"], confidence=new_confidence)

            # Add evidence
            evidence_content = f"False positive feedback: {reason or 'No reason provided'}"
            self.db.add_evidence(
                existing["memory_id"],
                "feedback",
                evidence_content,
                file_path=file_path,
            )

            return existing["memory_id"]
        else:
            # Create new learned pattern
            memory_id = self.db.create_memory_entry(
                repo=repo,
                memory_type="learned_pattern",
                content=pattern,
                scope=file_path or "repo",
                confidence=0.7,  # Start with moderate confidence
            )

            # Add evidence
            if reason:
                self.db.add_evidence(
                    memory_id,
                    "feedback",
                    reason,
                    file_path=file_path,
                )

            return memory_id

    def _find_existing_pattern(
        self, repo: str, pattern: str, file_path: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Find an existing pattern with the same content.

        Prefers an exact scope match, but falls back to any scope: the same
        pattern seen in a different file is evidence it generalizes, and
        should reinforce the existing entry rather than duplicate it.
        """
        patterns = self.db.list_memory_entries(
            repo, memory_type="learned_pattern", status="active"
        )
        fallback = None
        for p in patterns:
            if p["content"] != pattern:
                continue
            target_scope = file_path or "repo"
            if p["scope"] == target_scope or p["scope"] == "repo":
                return p
            if fallback is None:
                fallback = p
        return fallback

    def get_learned_patterns(self, repo: str, status: str = "active") -> List[Dict[str, Any]]:
        """Get learned patterns."""
        patterns = self.db.list_memory_entries(
            repo, memory_type="learned_pattern", status=status
        )

        # Enrich with evidence counts
        for pattern in patterns:
            evidence = self.db.get_evidence(pattern["memory_id"])
            pattern["evidence_count"] = len(evidence)
            pattern["evidence"] = evidence[:5]  # Include first 5 evidence items

        return patterns

    # Explicit Rules (user-defined)
    def create_explicit_rule(
        self,
        repo: str,
        content: str,
        scope: str,
        yaml_definition: Optional[str] = None,
        security_tags: Optional[List[str]] = None,
        requires_approval: bool = False,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create an explicit rule."""
        status = "pending" if requires_approval else "active"

        memory_id = self.db.create_memory_entry(
            repo=repo,
            memory_type="explicit_rule",
            content=content,
            scope=scope,
            confidence=1.0,  # Explicit rules have full confidence
            status=status,
            yaml_definition=yaml_definition,
            security_tags=security_tags,
            created_by=created_by,
        )

        # Create approval request if needed
        if requires_approval:
            approval_id = str(uuid.uuid4())
            with self.db.transaction() as cursor:
                cursor.execute(
                    """
                    INSERT INTO memory_approvals (
                        approval_id, repo, memory_id, requested_by, status
                    ) VALUES (?, ?, ?, ?, 'pending')
                    """,
                    (approval_id, repo, memory_id, created_by or "system"),
                )

        # Detect conflicts
        conflicts = self.detect_conflicts(repo, memory_id)

        return {
            "memory_id": memory_id,
            "status": status,
            "conflicts": conflicts,
        }

    def get_explicit_rules(
        self, repo: str, include_pending: bool = False
    ) -> List[Dict[str, Any]]:
        """Get explicit rules."""
        status_filter = ["active"]
        if include_pending:
            status_filter.append("pending")

        rules = []
        for status in status_filter:
            rules.extend(
                self.db.list_memory_entries(repo, memory_type="explicit_rule", status=status)
            )

        # Enrich with evidence
        for rule in rules:
            evidence = self.db.get_evidence(rule["memory_id"])
            rule["evidence"] = evidence

        return rules

    def import_from_claude_md(self, repo: str, claude_md_content: str) -> List[str]:
        """Import explicit rules from CLAUDE.md."""
        memory_ids = []

        # Simple parsing - look for rule patterns
        # This is a simplified version - real implementation would need proper parsing
        lines = claude_md_content.split("\n")
        current_rule = None

        for line in lines:
            if line.strip().startswith("##") or line.strip().startswith("#"):
                # Save previous rule if exists
                if current_rule:
                    memory_id = self.db.create_memory_entry(
                        repo=repo,
                        memory_type="explicit_rule",
                        content=current_rule["content"],
                        scope=current_rule.get("scope", "repo"),
                        confidence=1.0,
                        status="active",
                    )
                    memory_ids.append(memory_id)
                    current_rule = None

                # Start new rule
                current_rule = {"content": line.strip(), "scope": "repo"}
            elif current_rule and line.strip():
                current_rule["content"] += "\n" + line.strip()

        # Save last rule
        if current_rule:
            memory_id = self.db.create_memory_entry(
                repo=repo,
                memory_type="explicit_rule",
                content=current_rule["content"],
                scope=current_rule.get("scope", "repo"),
                confidence=1.0,
                status="active",
            )
            memory_ids.append(memory_id)

        return memory_ids

    # Session Context (ephemeral)
    def create_session_context(
        self,
        repo: str,
        content: str,
        scope: str,
        evidence: List[Dict[str, Any]],
        expires_in_hours: int = 24,
        branch: Optional[str] = None,
        head_sha: Optional[str] = None,
        git_dirty: int = 0,
        scope_tier: str = "repo",
    ) -> str:
        """Create ephemeral session context."""
        expires_at = datetime.now() + timedelta(hours=expires_in_hours)

        memory_id = self.db.create_memory_entry(
            repo=repo,
            memory_type="session_context",
            content=content,
            scope=scope,
            confidence=0.8,
            expires_at=expires_at,
            branch=branch,
            head_sha=head_sha,
            git_dirty=git_dirty,
            scope_tier=scope_tier,
        )

        # Add evidence
        for ev in evidence:
            self.db.add_evidence(
                memory_id,
                ev.get("type", "conversation"),
                ev.get("content", ""),
                file_path=ev.get("file"),
                line_number=ev.get("line"),
            )

        return memory_id

    def get_session_context(
        self, repo: str, include_expired: bool = False
    ) -> List[Dict[str, Any]]:
        """Get active session context."""
        contexts = self.db.list_memory_entries(
            repo, memory_type="session_context", status="active"
        )

        if not include_expired:
            now = datetime.now()
            contexts = [
                c
                for c in contexts
                if not c.get("expires_at")
                or datetime.fromisoformat(c["expires_at"]) > now
            ]

        # Calculate time until expiry
        for context in contexts:
            if context.get("expires_at"):
                expires = datetime.fromisoformat(context["expires_at"])
                delta = expires - datetime.now()
                context["minutes_until_expiry"] = int(delta.total_seconds() / 60)
                context["is_expired"] = delta.total_seconds() < 0
            else:
                context["minutes_until_expiry"] = None
                context["is_expired"] = False

        return contexts

    # Conflict Detection
    def detect_conflicts(self, repo: str, new_memory_id: str) -> List[Dict[str, Any]]:
        """Detect conflicts with existing memory entries."""
        new_entry = self.db.get_memory_entry(new_memory_id)
        if not new_entry:
            return []

        conflicts = []

        # Get potentially conflicting entries
        candidates = self.db.list_memory_entries(repo, status="active")
        candidates = [
            c
            for c in candidates
            if c["memory_id"] != new_memory_id
            and (
                c["scope"] == new_entry["scope"]
                or c["scope"] == "repo"
                or new_entry["scope"] == "repo"
            )
        ]

        for candidate in candidates:
            conflict_type = self._analyze_conflict(new_entry, candidate)
            if conflict_type:
                conflict_id = self.db.create_conflict(
                    repo=repo,
                    memory_id_1=new_memory_id,
                    memory_id_2=candidate["memory_id"],
                    conflict_type=conflict_type["type"],
                    severity=conflict_type["severity"],
                    description=conflict_type["description"],
                )

                # Flag only — never change entry status here. The heuristic has
                # false positives, and auto-disabling would silently remove a
                # valid rule from active listings. Resolution happens explicitly
                # via resolve_conflict.

                conflicts.append({
                    "conflict_id": conflict_id,
                    "memory_1": new_entry,
                    "memory_2": candidate,
                    "type": conflict_type["type"],
                    "severity": conflict_type["severity"],
                })

        return conflicts

    def _analyze_conflict(
        self, entry1: Dict[str, Any], entry2: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Analyze if two entries conflict."""
        # Contradiction: opposite statements
        if self._is_contradiction(entry1["content"], entry2["content"]):
            return {
                "type": "contradiction",
                "severity": "high",
                "description": f"Entry 1: '{entry1['content'][:50]}...' contradicts Entry 2: '{entry2['content'][:50]}...'",
            }

        # Overlap: similar content, different scopes
        if self._is_overlap(entry1, entry2):
            return {
                "type": "overlap",
                "severity": "medium",
                "description": "Entries overlap but have different scopes",
            }

        # Scope conflict: same content, different scopes
        if entry1["content"] == entry2["content"] and entry1["scope"] != entry2["scope"]:
            return {
                "type": "scope_conflict",
                "severity": "low",
                "description": "Same rule applied to different scopes",
            }

        return None

    def _is_contradiction(self, content1: str, content2: str) -> bool:
        """Check if two contents contradict each other."""
        # Simple heuristic - check for negation patterns
        negations = ["not", "never", "avoid", "don't", "shouldn't", "must not"]
        content1_lower = content1.lower()
        content2_lower = content2.lower()

        for neg in negations:
            if neg in content1_lower and neg not in content2_lower:
                # Check if they're talking about the same thing
                if self._similar_topic(content1, content2):
                    return True

        return False

    def _similar_topic(self, content1: str, content2: str) -> bool:
        """Check if two contents are about similar topics."""
        # Extract keywords (simple version)
        words1 = set(re.findall(r"\b\w+\b", content1.lower()))
        words2 = set(re.findall(r"\b\w+\b", content2.lower()))

        # Check for significant overlap
        common = words1.intersection(words2)
        return len(common) >= 3

    def _is_overlap(self, entry1: Dict[str, Any], entry2: Dict[str, Any]) -> bool:
        """Check if entries overlap."""
        return (
            entry1["scope"] != entry2["scope"]
            and self._similar_topic(entry1["content"], entry2["content"])
        )

    def get_relevant_memory(
        self,
        repo: str,
        file_paths: List[str],
        *,
        exclude_types: Optional[List[str]] = None,
        limit: int = 50,
        branch: Optional[str] = None,
        head: Optional[str] = None,
        dirty: Optional[bool] = None,
        include_other_branches: bool = False,
        include_session_context: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return active memories relevant to ``file_paths`` (repo-wide + scoped).

        When ``TURINGMIND_BRANCH_MEMORY=1``, ranks by branch layer (SPEC-BR-04).
        SPEC-BR-01 Option B: ``session_context`` excluded unless
        ``include_session_context=True``.
        """
        from .branch_recall import (
            branch_rank_score,
            resolve_recall_context,
            sort_entries_by_branch_rank,
        )

        skip = set(exclude_types or [])
        if include_session_context:
            skip.discard("session_context")
        else:
            skip.add("session_context")

        recall = resolve_recall_context(
            branch=branch,
            head=head,
            dirty=dirty,
            include_other_branches=include_other_branches,
        )
        normalized_files = [_normalize_memory_path(f) for f in file_paths if f]

        if recall.ranking_enabled:
            entries = self.db.list_memory_entries_for_recall(
                repo,
                recall.branch,
                recall.head,
                include_other_branches=recall.include_other_branches,
                detached=recall.detached,
                status="active",
                exclude_types=list(skip) if skip else None,
                internal_limit=max(limit * 10, 200),
            )
        else:
            entries = self.db.list_memory_entries(
                repo, status="active", limit=max(limit * 5, 100)
            )

        unique: List[Dict[str, Any]] = []
        seen: set[str] = set()

        for entry in entries:
            if entry["type"] in skip:
                continue
            if recall.ranking_enabled and branch_rank_score(entry, recall) <= 0:
                continue
            scope = entry.get("scope") or "repo"
            if scope == "repo" or not normalized_files:
                if entry["memory_id"] not in seen:
                    seen.add(entry["memory_id"])
                    unique.append(entry)
                continue
            if any(_memory_scope_matches(scope, fp) for fp in normalized_files):
                if entry["memory_id"] not in seen:
                    seen.add(entry["memory_id"])
                    unique.append(entry)

        if recall.ranking_enabled:
            unique = sort_entries_by_branch_rank(unique, recall)

        return unique[:limit]

    # Cleanup
    def cleanup_expired_context(self) -> int:
        """Cleanup expired session context."""
        return self.db.cleanup_expired_context()


def _normalize_memory_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _memory_scope_matches(scope: str, file_path: str) -> bool:
    """True when a memory scope overlaps a file path."""
    if not scope or scope == "repo":
        return False
    scope_n = _normalize_memory_path(scope)
    file_n = _normalize_memory_path(file_path)
    if scope_n == file_n:
        return True
    if file_n.endswith("/" + scope_n) or scope_n.endswith("/" + file_n):
        return True
    return scope_n in file_n or file_n in scope_n

