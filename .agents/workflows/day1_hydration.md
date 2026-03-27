---
description: Hydrate a scaffolded TuringMind project graph (Resolve Day 1 Orphans)
---

# Day 1 Hydration Strategy

When a project is initially scaffolded, the `turingmind_bootstrap_codebase` tool generates the node architecture but does not automatically wire all dependencies, metrics, or invariants. This creates "phantom orphans". 

To run the hydration pass:

1. **Trigger the Skill**: Use the `graph-enrichment` skill to deeply analyze the codebase and wire the disjointed nodes.
2. **Retrieve Orphans**: Use `turingmind_get_decision_queue` to identify nodes flagged with the `orphan_node` gap.
3. **Execute Updates**: For each orphan node:
   - Determine its valid upstream dependencies by reading the actual source code.
   - Use `turingmind_update_spec_node` (for Cursor) or `turingmind node update` (for Antigravity) to explicitly set the `dependencies` array.
   - Add appropriate `metrics` and `invariants` to complete its contract.
4. **Final Verification**: Once the queue is clear of orphans, run `turingmind test` (Antigravity) or `turingmind_run_verification` (Cursor) on the newly hydrated nodes to prove the graph resolves with >0.0 confidence.
