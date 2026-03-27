---
description: TuringMind Constraint Graph active governance rule. Must be obeyed before any implementation code is written.
---

# 🛑 TURINGMIND ACTIVE GOVERNANCE

**YOU ARE OPERATING IN A STRUCTURALLY GOVERNED WORKSPACE.**
You cannot bypass the TuringMind active architecture.

Every single time you are asked to write, modify, delete, or refactor code in this repository, you **MUST** follow this protocol. This is a hard cognitive constraint.

## The Rule of Prior Intent

**You may NOT write implementation code** (e.g., calling `write_to_file`, `replace_file_content`, or running build scripts) until you have proven your intent to the TuringMind V2 API. 

If you write code before declaring intent, you are in violation of standard operating procedure.

## How to Declare Intent

Before modifying any source file, you MUST do one of the following:

### Option A: Create a SpecNode directly via MCP (Preferred)
Use the `turingmind_create_spec_node` MCP tool to create a node for the exact feature/fix you are about to implement. 
- You must define the `contract.invariants` and `contract.metrics`. 
- The constraint graph dictates the code, not the other way around.

### Option B: Declare intent in `task.md` or `implementation_plan.md`
If you are planning a multi-step workflow across multiple files, you MUST write down your exact steps in `task.md` or `implementation_plan.md` using uncompleted checklist items (`- [ ]`).
- The background `turingmind watch` daemon monitors these files and automatically syncs your written intent to the V2 API via `POST /api/v2/intent`.
- You must wait for the plan file to save before generating code.

## Verification
Once you have written code, the structural file watcher will automatically detect it and sync it to the node/intent you generated above.

*Do not skip the intent phase under any circumstances, even for "quick fixes."*
