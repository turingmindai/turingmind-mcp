---
name: TuringMind Constraint Graph Protocol
description: Mandatory protocol for implementing any feature, fix, or refactor using the SpecNode constraint graph. Read this whenever a code change is requested.
---

# When to invoke this skill

Read and apply this protocol **every time** you are asked to implement, fix, or refactor any code in this repository. No exceptions — even for small changes.

If the user says "quick fix" or "just change X", still follow steps 0–1 before writing code. Only skip steps 2–5 for truly trivial single-line typo fixes with no behavioral impact.

# Protocol

## Step 0 — Session recovery (do this first, every new conversation)

Call `turingmind_list_spec_nodes(repo=<current_repo>)` to recover the graph from the previous session. Note the IDs of nodes being worked on. Never create a duplicate node for something that already has one.

## Step 1 — Define the constraint (before writing code)

Call `turingmind_create_spec_node` with:
- `title`: one-line description of what this node does
- `level`: L0 (system) → L3 (function-level)
- `surface_type`: `api_endpoint` | `internal` | `job` | `hardware_bridge`
- `contract.invariants`: list of rules that must never break (e.g. `"requires_jwt_bearer"`)
- `contract.metrics`: list of `{ name, threshold, unit, direction }` objects — these must be real numbers, not descriptions
- `dependencies`: IDs of upstream nodes this depends on

## Step 2 — Generate verification stubs (before writing implementation)

Call `turingmind_generate_verification(node_id=..., test_dir="<path/to/tests>")`.

This writes real `.py` stub files to disk. Do not write implementation code until stubs exist.

## Step 3 — Implement

Write the implementation. Update `turingmind_update_spec_node(implementation.files=[...])` with the files you create.

## Step 4 — Run verification (with real test execution)

Call `turingmind_run_verification(node_id=..., test_dir="<path/to/tests>", python_bin="<path/to/.venv/bin/python>")`.

This executes pytest, parses results, and records machine-verified Evidence on the node automatically. Do **not** call `record_execution_stage` manually unless pytest cannot be run.

## Step 5 — Blast radius check

If you changed an existing node's contract, call:
- `turingmind_apply_spec_delta` — propagates the change
- `turingmind_get_impacted_nodes` — shows what breaks downstream

Re-verify all invalidated nodes before marking the work complete.

# Failure handling

If Step 4 fails, call `turingmind_classify_failure` with the appropriate kind:
- `spec_gap` — contract was too vague
- `test_gap` — tests didn't cover the real behavior
- `implementation_bug` — code is wrong
- `dependency_failure` — upstream node broke

Then call `turingmind_apply_fix` to reset to the right stage.

# Hard rules

1. **Never write code before step 1.** A node must exist.
2. **Never assert `confidence > 0.8` without machine-verified Evidence.** If pytest didn't run, confidence max is 0.5.
3. **A `regression` signal always fully invalidates a node.** Old stubs are discarded.
4. **L0 and L1 nodes require `turingmind_request_approval` before marking verified.**
5. **Step 0 is not optional.** Always call `list_spec_nodes` at the start of a session.
