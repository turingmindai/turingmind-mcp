---
description: how to implement any feature, fix, or refactor using the constraint graph
---

# Constraint Graph Protocol

Every implementation — no matter how small — must go through the constraint graph.
The graph is the source of truth. A spec node must exist before code does.

## Steps

1. **Define the constraint**
   Call `turingmind_create_spec_node` with:
   - `title`: what the node does in one line
   - `level`: L0 (system) → L3 (function)
   - `surface_type`: `api_endpoint`, `internal`, `job`, or `hardware_bridge`
   - `contract.invariants`: rules that can never be broken (e.g. `"requires_jwt_bearer"`)
   - `contract.metrics`: measurable thresholds — always include `name`, `threshold`, `unit`, `direction`
     ```json
     { "name": "p95_latency", "threshold": 200, "unit": "ms", "direction": "below" }
     ```
   - `dependencies`: IDs of any upstream nodes this depends on

2. **Generate verification before writing code**
   Call `turingmind_generate_verification(node_id)`.
   Write the generated stub test names as actual test functions before any implementation.

3. **Implement**
   Write the code. Record file paths in `turingmind_update_spec_node(implementation.files=[...])`.

4. **Record evidence**
   Call `turingmind_record_execution_stage` with:
   - `stage`: `verified`
   - `status`: `verified`
   - `confidence`: float reflecting actual test results (not optimism)
   - `evidence`: `{ "kind": "test_run", "score": <float>, "detail": "<N passed, M failed, X% coverage>", "source": "cursor" }`

5. **Check blast radius on any change**
   If an existing node's contract changes, call:
   - `turingmind_apply_spec_delta` to propagate
   - `turingmind_get_impacted_nodes` to see what breaks downstream
   Re-verify all invalidated nodes before marking the change complete.

## Failure protocol

If a verification fails, call `turingmind_classify_failure`:
- `spec_gap` — the contract was underspecified
- `test_gap` — the tests didn't catch a real case
- `implementation_bug` — code is wrong
- `dependency_failure` — an upstream node is broken

Then call `turingmind_apply_fix` to reset the node to the right stage.

## Rules

- Never skip step 1. Writing code before creating a node means correctness is untracked.
- Never assert `confidence > 0.8` without real test evidence in the evidence field.
- A `regression` runtime signal always invalidates the node completely — do not re-use old verification stubs.
- Approval gates (`turingmind_request_approval`) are required for L0/L1 nodes before marking verified.
