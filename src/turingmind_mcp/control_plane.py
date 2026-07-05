from __future__ import annotations

import datetime
import uuid
import logging
from typing import Any, Dict, List, Optional

from .database import MemoryDatabase
from .recall_bundle import (
    rank_memories,
    compute_delta_bundle,
    RecallBundle,
    PolicySpec,
)
from .session_lifecycle import session_expires_at

from .subsystem_config import load_subsystem_map, match_subsystem

logger = logging.getLogger("turingmind-mcp.control-plane")

class CognitionControlPlane:
    @staticmethod
    def resolve_subsystem(
        file_path: str,
        subsystem_map: Optional[Dict[str, List[str]]] = None,
    ) -> str:
        configured = match_subsystem(file_path, subsystem_map or {})
        if configured:
            return configured

        parts = [p for p in file_path.replace("\\", "/").split("/") if p and p != "."]
        if not parts:
            return "repo"
        if len(parts) == 1:
            return "repo"
        if parts[0] in ("src", "lib") and len(parts) > 1:
            parts = parts[1:]
        if len(parts) > 1 and parts[0] in ("turingmind_mcp", "turingmind", "repochatindex"):
            parts = parts[1:]
        sub = parts[0]
        if "." in sub:
            sub = sub.split(".")[0]
        return sub

    @classmethod
    def sync_codebase(
        cls,
        db: MemoryDatabase,
        repo: str,
        files: List[str],
        composer_id: Optional[str] = None,
        session_id: Optional[str] = None,
        branch: Optional[str] = None,
        head_sha: Optional[str] = None,
        workspace_root: Optional[str] = None,
        cluster_meta: Optional[Dict[str, Any]] = None,
        impacted_nodes: Optional[List[str]] = None,
        cascades: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if impacted_nodes is None:
            impacted_nodes = []
        if cascades is None:
            cascades = []

        session_key = session_id or composer_id or "default_session"
        if composer_id:
            from .chat_observation_poller import register_active_composer
            register_active_composer(composer_id)

        # Auto-bootstrap spec nodes graph if empty (TC-CP-32) — governed only
        from .profile_config import is_memory_profile

        if not is_memory_profile():
            cls.bootstrap_repo_if_empty(db, repo)

        # 1. Load or create Coding Session (CP-SPEC-02)
        sess = db.get_coding_session(session_key, repo)
        if not sess:
            sess_id = str(uuid.uuid4())
            expires_at = session_expires_at()
            db.create_coding_session(
                session_id=sess_id,
                composer_id=session_key,
                repo=repo,
                branch=branch,
                head_sha=head_sha,
                expires_at=expires_at,
            )
            sess = db.get_coding_session(session_key, repo)

        sess_id = sess["session_id"] if sess else str(uuid.uuid4())
        loaded_scopes = sess["loaded_scopes"] if sess else []
        touched_files = sess["touched_files"] if sess else []
        touched_subsystems = sess["touched_subsystems"] if sess else []
        recall_history = sess["recall_history"] if sess else []

        # 2. Resolve subsystems (Memory Pager Subsystem Matching)
        subsystem_map = load_subsystem_map(workspace_root)
        current_subsystems = []
        for f in files:
            sub = cls.resolve_subsystem(f, subsystem_map=subsystem_map)
            if sub not in current_subsystems:
                current_subsystems.append(sub)

        # 3. Detect Page Faults & Drift
        page_fault = False
        new_scopes = list(loaded_scopes)
        for sub in current_subsystems:
            if sub not in loaded_scopes:
                page_fault = True
                new_scopes.append(sub)

        drift_detected = False
        if loaded_scopes and any(sub not in loaded_scopes for sub in current_subsystems):
            drift_detected = True

        new_touched_files = list(set(touched_files + files))
        new_touched_subsystems = list(set(touched_subsystems + current_subsystems))

        # 4. Rank Memories & Hydrate Delta Bundle
        ranked = rank_memories(
            db=db,
            repo=repo,
            files=files,
            branch=branch,
        )

        delta_res = compute_delta_bundle(
            candidate_rules=ranked["explicit_rules"],
            candidate_patterns=ranked["learned_patterns"],
            recall_history=recall_history,
        )

        schema_failures = int(ranked.get("schema_failures") or 0)

        delta_rules = delta_res["delta_rules"]
        delta_patterns = delta_res["delta_patterns"]
        bundle_delta = {
            "added_rule_ids": delta_res["added_rule_ids"],
            "removed_rule_ids": [],
            "unchanged": delta_res["unchanged"],
        }

        # 5. Fetch top findings (needed for bundle schema)
        raw_findings = db.list_findings(repo=repo, status="pending", limit=5)
        queue_top = []
        for f in raw_findings:
            queue_top.append({
                "gap_type": f.get("finding_type", "unknown"),
                "severity": f.get("severity", "medium"),
                "action": f.get("action", f.get("description", ""))[:500],
                "finding_id": f.get("finding_id"),
                "memory_id": f.get("memory_id")
            })

        # 6. Formulate Policy
        policy = {
            "hydrate_required": page_fault,
            "code": "TM-DRIFT-002" if drift_detected else None,
            "message": f"Subsystem drift detected. Touch cross-module: {loaded_scopes} -> {current_subsystems}" if drift_detected else None,
            "required_tools": []
        }

        # 7. Validate bundle BEFORE persisting recall_history
        schema_error = False
        try:
            RecallBundle(
                explicit_rules=delta_rules,
                learned_patterns=delta_patterns,
                queue_top=queue_top,
                policy=PolicySpec(**policy),
            )
        except Exception as schema_err:
            logger.error("Bundle schema validation failed: %s", schema_err)
            schema_error = True
            delta_rules = []
            delta_patterns = []
            policy = {
                "hydrate_required": True,
                "code": "TM-SCHEMA-ERR",
                "message": f"Recall bundle validation failed: {schema_err}"[:400],
                "required_tools": [],
            }
            bundle_delta = {
                "added_rule_ids": [],
                "removed_rule_ids": [],
                "unchanged": True,
            }

        if schema_failures > 0 and not schema_error:
            schema_error = True
            policy = {
                "hydrate_required": True,
                "code": "TM-SCHEMA-ERR",
                "message": (
                    f"{schema_failures} memory entr(y/ies) failed recall_bundle schema validation"
                )[:400],
                "required_tools": [],
            }
            delta_rules = []
            delta_patterns = []
            bundle_delta = {
                "added_rule_ids": [],
                "removed_rule_ids": [],
                "unchanged": True,
            }

        if schema_error:
            new_recall_history = list(recall_history)
        else:
            new_recall_history = list(set(recall_history + bundle_delta["added_rule_ids"]))

        # 8. Update SQLite Session (after validation)
        if sess:
            policy_state = "drift" if drift_detected else "hydrated"
            expires_at = session_expires_at()
            db.update_coding_session(
                session_id=sess_id,
                loaded_scopes=new_scopes,
                touched_files=new_touched_files,
                touched_subsystems=new_touched_subsystems,
                recall_history=new_recall_history,
                policy_state=policy_state,
                expires_at=expires_at,
            )

        result = {
            "status": "synced",
            "repo": repo,
            "direct_impact_count": len(impacted_nodes),
            "direct_impact_nodes": impacted_nodes,
            "cascades_triggered": len(cascades),
            "session": {
                "session_id": sess_id,
                "composer_id": session_key,
                "repo": repo,
                "branch": branch,
                "loaded_scopes": new_scopes,
                "last_seen_at": datetime.datetime.utcnow().isoformat()
            },
            "delivery": {
                "channel": "mcp_json",
                "bundle_version": "1.0",
                "is_delta": not bundle_delta["unchanged"] and not schema_error,
                "token_budget_used": len(delta_rules) + len(delta_patterns),
                "token_budget_max": 16,
            },
            "recall_bundle": {
                "explicit_rules": [r.model_dump() for r in delta_rules],
                "learned_patterns": [p.model_dump() for p in delta_patterns],
                "queue_top": queue_top,
                "policy": policy,
            },
            "bundle_delta": bundle_delta,
        }
        if cluster_meta:
            result["cluster"] = cluster_meta

        return result

    @staticmethod
    def patch_session(
        db: MemoryDatabase,
        session_id: str,
        loaded_scopes: Optional[List[str]] = None,
        touched_files: Optional[List[str]] = None,
        touched_subsystems: Optional[List[str]] = None,
        recall_history: Optional[List[str]] = None,
        policy_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        sess = db.get_coding_session_by_id(session_id)
        if not sess:
            raise KeyError("Session not found")

        merged_scopes = loaded_scopes if loaded_scopes is not None else sess["loaded_scopes"]
        merged_files = touched_files if touched_files is not None else sess["touched_files"]
        merged_subsystems = touched_subsystems if touched_subsystems is not None else sess["touched_subsystems"]
        merged_recall = recall_history if recall_history is not None else sess["recall_history"]
        merged_policy = policy_state if policy_state is not None else sess["policy_state"]

        expires_at = session_expires_at()

        db.update_coding_session(
            session_id=session_id,
            loaded_scopes=merged_scopes,
            touched_files=merged_files,
            touched_subsystems=merged_subsystems,
            recall_history=merged_recall,
            policy_state=merged_policy,
            expires_at=expires_at,
        )
        return db.get_coding_session_by_id(session_id)

    @staticmethod
    def run_session_gc(db: MemoryDatabase) -> Dict[str, Any]:
        from .session_lifecycle import run_session_gc as _run_gc
        return _run_gc(db)

    @staticmethod
    def bootstrap_repo_if_empty(db: MemoryDatabase, repo: str) -> None:
        """Auto-bootstrap spec node graph structure if it has 0 nodes (TC-CP-32)."""
        from .v2_engine.database import get_all_spec_nodes, save_spec_node
        from .v2_engine.models import SpecNode, NodeState, Implementation
        from .v2_engine.models import NodeLevel, SurfaceType, SpecStatus, ExecutionStage

        try:
            nodes = get_all_spec_nodes(repo)
            if not nodes:
                logger.info(f"Auto-bootstrapping default SpecNode constraint graph for repo {repo}")
                root_node = SpecNode(
                    id=f"bootstrap-root-{repo.replace('/', '-')}",
                    repo=repo,
                    title="System Spec Root Constraints",
                    level=NodeLevel.L0_INFRA,
                    surface_type=SurfaceType.INTERNAL,
                    dependencies=[],
                    owner="arch-lead",
                    description="Auto-bootstrapped root spec constraints node for repo.",
                    implementation=Implementation(files=["src/reconcile.py"], functions=[]),
                    state=NodeState(
                        status=SpecStatus.VERIFIED,
                        stage=ExecutionStage.VERIFIED,
                        confidence=1.0,
                        evidence=[]
                    ),
                )
                save_spec_node(root_node)
        except Exception as e:
            logger.error(f"Error bootstrapping spec nodes: {e}")


