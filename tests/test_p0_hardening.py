from __future__ import annotations

import json
from pathlib import Path

import pytest

from turingmind_mcp.memory_ranker import (
    entry_relevance_score,
    file_scope_score,
    rank_rules_and_patterns,
    score_explicit_rule,
)
from turingmind_mcp.recall_delivery import (
    apply_recall_delivery,
    load_recalled_index,
    reset_index_if_composer_changed,
    save_session_meta,
)


def test_file_scope_score_exact_match():
    assert file_scope_score("database/postgres.py", ["database/postgres.py"]) == 1.0


def test_score_explicit_rule_repo_wide():
    row = {"scope": "repo", "content": "rule", "memory_id": "m1"}
    assert score_explicit_rule(row, ["app.py"]) == 0.8


def test_rank_rules_and_patterns_orders_by_score(api_client, tier_repo):
    _, db = api_client
    db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="Repo-wide rule",
        scope="repo",
        branch="main",
        confidence=1.0,
    )
    db.create_memory_entry(
        repo=tier_repo,
        memory_type="explicit_rule",
        content="Scoped rule",
        scope="database/postgres.py",
        branch="main",
        confidence=1.0,
    )

    ranked = rank_rules_and_patterns(
        db, tier_repo, ["database/postgres.py"], branch="main"
    )
    rules = ranked["explicit_rules"]
    assert len(rules) >= 2
    assert rules[0].scope == "database/postgres.py"
    assert rules[0].score >= rules[1].score


def test_entry_relevance_score_learned_pattern():
    entry = {
        "type": "learned_pattern",
        "scope": "database/postgres.py",
        "confidence": 0.8,
    }
    score = entry_relevance_score(entry, ["database/postgres.py"])
    assert score > 0.5


def test_reset_index_on_composer_change(tmp_path: Path):
    save_session_meta(tmp_path, {"composer_id": "composer-a"})
    index_path = tmp_path / ".turingmind" / "recalled-index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps({"memory_ids": ["m1"], "rules": [{"memory_id": "m1"}], "patterns": []}),
        encoding="utf-8",
    )

    reset_index_if_composer_changed(
        tmp_path,
        {"session": {"composer_id": "composer-b"}},
    )
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["memory_ids"] == []


def test_apply_recall_delivery_writes_recalled_md(tmp_path: Path):
    sync_response = {
        "repo": "test/repo",
        "session": {"session_id": "s1", "composer_id": "c1", "repo": "test/repo"},
        "delivery": {"is_delta": True},
        "bundle_delta": {"added_rule_ids": ["rule-1"], "unchanged": False},
        "recall_bundle": {
            "explicit_rules": [
                {
                    "memory_id": "rule-1",
                    "type": "explicit_rule",
                    "content": "Always validate input.",
                    "scope": "app.py",
                    "score": 1.0,
                }
            ],
            "learned_patterns": [],
            "queue_top": [],
            "policy": {"hydrate_required": False},
        },
    }

    result = apply_recall_delivery(tmp_path, sync_response)
    assert result["written"] is True
    recalled = tmp_path / ".turingmind" / "recalled.md"
    assert recalled.exists()
    assert "Always validate input." in recalled.read_text(encoding="utf-8")
    index = load_recalled_index(tmp_path)
    assert "rule-1" in index["memory_ids"]
