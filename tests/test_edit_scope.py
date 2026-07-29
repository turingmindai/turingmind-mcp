"""IDE-agnostic edit-scope contracts."""

from __future__ import annotations

from turingmind_mcp.edit_scope import (
    check_against_store,
    check_scope_breach,
    declare_scope,
    extract_paths_from_text,
    path_in_scope,
    seed_scope_from_prompt,
)


def test_extract_and_match_paths():
    paths = extract_paths_from_text(
        "Please fix src/auth/session.ts and packages/ui/Button.tsx"
    )
    assert "src/auth/session.ts" in paths
    scope = {
        "prefixes": ["src/auth/"],
        "modules": ["packages/ui"],
        "files": [],
    }
    assert path_in_scope("src/auth/tokens.ts", scope)
    assert path_in_scope("packages/ui/Button.tsx", scope)
    assert not path_in_scope("src/api/routes.ts", scope)


def test_undeclared_wide_breach():
    breach = check_scope_breach(
        {"status": "undeclared", "prefixes": [], "modules": [], "files": []},
        ["src/a/one.ts", "src/b/two.ts", "packages/c/three.ts"],
        cluster_type="cross_module",
        cluster_severity="medium",
    )
    assert breach and breach["breached"] is True
    assert "without a declared" in breach["reason"]


def test_declare_and_check_store(memory_db, tier_repo):
    seeded = seed_scope_from_prompt(
        repo=tier_repo,
        prompt="Only touch src/auth/session.ts",
        conversation_id="c1",
    )
    stored = declare_scope(memory_db, seeded)
    assert stored["status"] == "seeded"

    result = check_against_store(
        memory_db,
        repo=tier_repo,
        files=["src/auth/session.ts", "src/api/routes.ts"],
        conversation_id="c1",
        cluster_type="cross_module",
    )
    assert result["breached"] is True
    assert "src/api/routes.ts" in result["breach"]["out_of_scope"]


def test_seed_does_not_clobber_declared(memory_db, tier_repo):
    declare_scope(
        memory_db,
        {
            "repo": tier_repo,
            "status": "declared",
            "intent": "auth only",
            "prefixes": ["src/auth/"],
            "modules": ["src/auth"],
            "files": [],
            "source": "agent",
            "conversation_id": "c1",
        },
    )
    weak = seed_scope_from_prompt(
        repo=tier_repo,
        prompt="Also fix packages/ui/Button.tsx",
        conversation_id="c1",
    )
    stored = declare_scope(memory_db, weak)
    assert stored["status"] == "declared"
    assert stored["prefixes"] == ["src/auth/"]
