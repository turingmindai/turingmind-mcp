---
description: how to implement any feature, fix, or refactor using the TuringMind constraint graph
---

# TuringMind Constraint Graph Workflow

Every implementation — no matter how small — goes through the constraint graph.
The graph is the source of truth. A node must exist before code does.

## Steps

// turbo
1. **Recover session state**
   ```bash
   turingmind graph status
   ```
   Note the node counts and identify any in-progress work.

// turbo
2. **Check the decision queue**
   ```bash
   turingmind queue pop
   ```
   If the queue has items, address the top one first before starting new work.

3. **Create or identify a spec node**
   Every code change must trace to a node. If one doesn't exist:
   ```bash
   turingmind node create "<description>" --level L3_API --surface-type internal
   ```

4. **Write the implementation**
   Edit the relevant files. The background `turingmind watch` daemon will automatically detect your file saves, cluster them, and sync to the graph. No manual sync is required.

6. **Run tests**
   ```bash
   # Use the project's test runner
   python3 -m pytest tests/ -v
   # or: npm test
   ```

// turbo
7. **Re-check the queue**
   ```bash
   turingmind queue
   ```
   Resolve any new gaps that appeared from cascading confidence changes.

8. **Done** when `turingmind queue` returns "✅ No gaps. Graph is healthy."
