---
name: TuringMind Constraint Graph Protocol
description: Mandatory protocol for implementing any feature, fix, or refactor using the SpecNode constraint graph. Read this whenever a code change is requested.
---

# When to invoke this skill

Read and apply this protocol **every time** you are asked to implement, fix, or refactor any code in this repository. No exceptions — even for small changes.

# How this skill works with Antigravity

You interact with the TuringMind Constraint Graph via the `turingmind` CLI.
All commands are run via `run_command`. All output is agent-readable markdown.

**Prerequisites:** 
1. The V2 API server must be running (`python3 -m turingmind_mcp.api_server`)
2. The TuringMind structural watcher must be running in the background (`turingmind watch --repo <current_repo> &`). If it isn't, start it.

# Protocol

## Step 0 — Session recovery (do this first, every new conversation)

// turbo
Run `turingmind graph status` to recover the graph from the previous session.

```bash
turingmind graph status --repo <current_repo>
```

Note the node counts and status distribution. Never create a duplicate node for something that already exists.

## Step 1 — Check the decision queue

// turbo
Run `turingmind queue pop` to see the highest-priority gap.

```bash
turingmind queue pop --repo <current_repo>
```

If the queue returns an action item, address it. If the queue is empty and the user has requested new work, proceed to Step 2.

## Step 2 — Define the constraint (before writing code)

Create a new spec node using the CLI:

```bash
turingmind node create "<one-line description>" \
  --repo <current_repo> \
  --level L3_API \
  --surface-type api_endpoint \
  --invariant "requires_jwt_bearer" \
  --depends-on "<upstream_node_id>"
```

Valid levels: `L0_SYSTEM`, `L1_FILE`, `L2_EXTERNAL`, `L3_API`
Valid surface types: `api_endpoint`, `internal`, `job`, `hardware_bridge`

> **Hard rule:** Never write code before creating or identifying a node for it.

## Step 3 — Implement

Write the implementation code. 

You usually do not need to manually sync your changes. The `turingmind watch` background daemon automatically detects your edits, buffers them over a 5-second quiet period, intelligently classifies the cluster (`targeted_fix`, `cross_module`, `refactor_burst`), and syncs the changes to the Graph API.

This enforces *Structural Governance*. Wait a few seconds after saving your files for the watcher to sync.

> **Fallback:** If you suspect the watcher is not running or you need an immediate sync, you can always sync manually:
> `turingmind sync <files> --cluster-type development`

## Step 4 — Verify

Run tests via the standard test runner for your project's ecosystem:

```bash
# Python
python3 -m pytest tests/ -v

# Node.js / TypeScript
npm test

# Go
go test ./...

# Rust
cargo test

# Java / Maven
mvn test

# Ruby
bundle exec rspec
```

After tests pass, the watcher will auto-capture and sync file changes. If tests fail, fix before proceeding.

## Step 5 — Check blast radius

After changing any existing node's contract or implementation, check the impact:

// turbo
```bash
turingmind queue --repo <current_repo>
```

If new gaps appeared (e.g., `dependency_failure`, `missing_boundary_edge`), resolve them before marking the work complete.

# Failure handling

If tests fail:
1. Read the error output carefully
2. Classify the failure type:
   - `spec_gap` — contract was too vague, need to tighten invariants
   - `test_gap` — tests didn't cover the real behavior
   - `implementation_bug` — code is wrong
   - `dependency_failure` — upstream node broke
3. Fix the root cause, then re-run Step 4

# Hard rules

1. **Never write code before Step 2.** A node must exist or be identified.
2. **Never claim verification without running tests.** If tests didn't run, the work is not verified.
3. **Always check the queue after edits.** Cascading failures may create new gaps when the watcher syncs files.
4. **Step 0 is not optional.** Always run `turingmind graph status` at the start of a session.
