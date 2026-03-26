# TDD/SDD Workflow Documentation

## Overview

TuringMind-MCP includes a complete Test-Driven Development (TDD) and Spec-Driven Development (SDD) workflow system with full audit trails.

## Why This Matters

Every AI-generated change comes with:
- ✅ **Auditable design rationale** - Every change has documented reasoning
- ✅ **Explicit acceptance criteria** - Clear definition of success
- ✅ **Test coverage proof** - Tests must exist before code
- ✅ **Full traceability** - Complete audit trail for compliance

## Workflow Phases

### Phase 1: Planning (MANDATORY)

Create an EditPlan - the mandatory reasoning artifact before any code changes.

```
turingmind_create_edit_plan(
  repo="owner/repo",
  change_intent="Clear description of what you're doing",
  files_affected=["list", "of", "files"],
  acceptance_criteria=["When X, then Y", "When A, then B"],
  observed_problem=["Problem 1", "Problem 2"],
  design_decisions=["Decision 1", "Decision 2"],
  non_goals=["What this does NOT do"]
)
```

### Phase 2: Specification

Generate Gherkin-style specifications from acceptance criteria.

```
turingmind_generate_spec(plan_id="...")
```

### Phase 3: RED Phase - Define Tests

Define required tests before writing any code.

```
turingmind_define_required_tests(
  spec_id="...",
  tests=[
    {
      "name": "test_descriptive_name",
      "type": "unit",
      "test_file": "tests/test_feature.py",
      "failure_mode": "Expected X but got Y"
    }
  ]
)
```

### Phase 4: Write Failing Tests

Write the test code. Tests MUST FAIL initially.

Then validate:

```
turingmind_validate_tests_written(plan_id="...")
```

### Phase 5: GREEN Phase - Implement

Request permission to edit files:

```
turingmind_request_code_edit(
  plan_id="...",
  file_path="src/feature.py",
  edit_type="modify",
  acceptance_criteria_addressed=["criteria from plan"]
)
```

### Phase 6: REFACTOR Phase

Validate tests pass:

```
turingmind_validate_tests_passing(plan_id="...")
```

### Phase 7: Complete

Complete the cycle and generate audit trail:

```
turingmind_complete_tdd_cycle(plan_id="...")
```

## MCP Tools Reference

| Tool | Purpose | Phase |
|------|---------|-------|
| `turingmind_create_edit_plan` | Create reasoning artifact | Planning |
| `turingmind_generate_spec` | Generate Gherkin specs | Spec |
| `turingmind_define_required_tests` | Define test requirements | RED |
| `turingmind_validate_tests_written` | Validate tests exist | RED → GREEN |
| `turingmind_request_code_edit` | Request edit permission | GREEN |
| `turingmind_validate_tests_passing` | Validate tests pass | GREEN → REFACTOR |
| `turingmind_complete_tdd_cycle` | Complete with audit trail | Complete |
| `turingmind_get_tdd_status` | Check current status | Any |
| `turingmind_get_audit_trail` | Get full audit trail | Any |

## Database Schema

### edit_plans
Stores EditPlan reasoning artifacts:
- `plan_id` - Unique identifier
- `repo` - Repository identifier
- `session_id` - TDD session reference
- `change_intent` - What you're trying to accomplish
- `files_affected` - Files that will be modified
- `observed_problem` - Problems that led to this change
- `design_decisions` - Key design decisions
- `non_goals` - What this change does NOT do
- `acceptance_criteria` - Success criteria
- `status` - draft, approved, completed

### specifications
Gherkin specs and requirements:
- `spec_id` - Unique identifier
- `plan_id` - Reference to EditPlan
- `title` - Specification title
- `gherkin_spec` - Gherkin-format specification
- `requirements` - List of requirements with IDs

### required_tests
Test definitions and status:
- `test_id` - Unique identifier
- `spec_id` - Reference to specification
- `plan_id` - Reference to EditPlan
- `test_name` - Test function name
- `test_type` - unit, integration, e2e
- `test_file` - Path to test file
- `failure_mode` - What failing looks like
- `status` - pending, written, passing

### code_edits
Tracked code edits with traceability:
- `edit_id` - Unique identifier
- `plan_id` - Reference to EditPlan
- `file_path` - File being edited
- `edit_type` - create, modify, delete
- `acceptance_criteria_met` - Which criteria this addresses

### tdd_sessions
Workflow sessions with phase tracking:
- `session_id` - Unique identifier
- `repo` - Repository identifier
- `plan_id` - Reference to EditPlan
- `current_phase` - planning, spec, red, green, refactor, done
- `time_in_*_ms` - Time spent in each phase

## VS Code Extension

The companion VS Code extension (`turingmind-vscode`) provides:

### Commands
- `TuringMind: Start TDD Cycle` - Start a new TDD workflow
- `TuringMind: Get TDD Status` - Check current status
- `TuringMind: Show Kanban Board` - Visual workflow tracking
- `TuringMind: Enable TDD Mode` - Enable pre-edit validation
- `TuringMind: Disable TDD Mode` - Disable validation

### Configuration

```json
{
  "turingmind.tdd.enabled": true,
  "turingmind.tdd.strictMode": false,
  "turingmind.tdd.allowTestEdits": true,
  "turingmind.tdd.allowConfigEdits": true
}
```

### Features
- **Pre-save validation**: Blocks saves without EditPlan approval
- **Status bar**: Shows current TDD phase (RED/GREEN/REFACTOR)
- **Kanban board**: Visual workflow tracking
- **Phase enforcement**: Prevents code edits in wrong phase

## Cursor Rules Template

A `.cursorrules` template is available at `templates/cursorrules-tdd.md` that enforces the TDD workflow in Cursor AI.

## Example Workflow

### User Request: "Fix the race condition in token refresh"

**Step 1: Create EditPlan**
```
turingmind_create_edit_plan(
  repo="myorg/myapp",
  change_intent="Fix race condition in token refresh",
  files_affected=["src/auth/token_manager.py", "tests/test_token_manager.py"],
  acceptance_criteria=[
    "Concurrent calls do not issue multiple refreshes",
    "All callers receive the refreshed token",
    "Existing callers remain unaffected"
  ],
  observed_problem=[
    "refresh_token can be called concurrently",
    "No mutex around shared state"
  ],
  design_decisions=[
    "Introduce async lock",
    "Fail fast if refresh in progress"
  ],
  non_goals=[
    "No API changes",
    "No refactor beyond auth module"
  ]
)
```

**Step 2: Generate Spec**
```
turingmind_generate_spec(plan_id="abc123...")
```

**Step 3: Define Tests**
```
turingmind_define_required_tests(
  spec_id="spec123...",
  tests=[
    {
      "name": "test_single_refresh_under_concurrency",
      "type": "unit",
      "test_file": "tests/test_token_manager.py",
      "failure_mode": "Multiple refresh calls detected"
    },
    {
      "name": "test_all_callers_receive_token",
      "type": "unit",
      "test_file": "tests/test_token_manager.py",
      "failure_mode": "Some callers did not receive token"
    }
  ]
)
```

**Step 4: Write Tests (in test file)**
```python
# tests/test_token_manager.py
async def test_single_refresh_under_concurrency():
    # This test should FAIL initially
    manager = TokenManager()
    results = await asyncio.gather(
        manager.refresh_token(),
        manager.refresh_token(),
        manager.refresh_token()
    )
    # Assert only one refresh was made
    assert manager.refresh_count == 1
```

**Step 5: Validate Tests Written**
```
turingmind_validate_tests_written(plan_id="abc123...")
```

**Step 6: Request Code Edit**
```
turingmind_request_code_edit(
  plan_id="abc123...",
  file_path="src/auth/token_manager.py",
  edit_type="modify",
  acceptance_criteria_addressed=["Concurrent calls do not issue multiple refreshes"]
)
```

**Step 7: Implement the Fix**
```python
# src/auth/token_manager.py
import asyncio

class TokenManager:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._refresh_in_progress = False
        self.refresh_count = 0
    
    async def refresh_token(self):
        async with self._lock:
            if self._refresh_in_progress:
                # Wait for existing refresh
                return self._current_token
            
            self._refresh_in_progress = True
            self.refresh_count += 1
            
            try:
                self._current_token = await self._do_refresh()
                return self._current_token
            finally:
                self._refresh_in_progress = False
```

**Step 8: Validate Tests Pass**
```
turingmind_validate_tests_passing(plan_id="abc123...")
```

**Step 9: Complete Cycle**
```
turingmind_complete_tdd_cycle(plan_id="abc123...")
```

## Audit Trail Output

The `turingmind_complete_tdd_cycle` tool returns a complete audit trail:

```json
{
  "plan_id": "abc123...",
  "status": "completed",
  "reasoning": {
    "change_intent": "Fix race condition in token refresh",
    "observed_problem": ["refresh_token can be called concurrently"],
    "design_decisions": ["Introduce async lock"],
    "non_goals": ["No API changes"],
    "acceptance_criteria": ["Concurrent calls do not issue multiple refreshes"]
  },
  "specifications": [{
    "spec_id": "spec123...",
    "title": "Fix race condition in token refresh",
    "gherkin_spec": "Feature: ...",
    "requirements": [{"id": "REQ-001", "description": "..."}]
  }],
  "tests": [{
    "test_id": "test456...",
    "test_name": "test_single_refresh_under_concurrency",
    "test_type": "unit",
    "status": "passing"
  }],
  "code_edits": [{
    "edit_id": "edit789...",
    "file_path": "src/auth/token_manager.py",
    "edit_type": "modify",
    "acceptance_criteria_met": ["Concurrent calls do not issue multiple refreshes"]
  }],
  "metrics": {
    "time_in_planning_ms": 5000,
    "time_in_spec_ms": 2000,
    "time_in_red_ms": 15000,
    "time_in_green_ms": 30000,
    "time_in_refactor_ms": 10000
  }
}
```

## Enforcement Summary

| Phase | Tool | Required |
|-------|------|----------|
| 1. Planning | `turingmind_create_edit_plan` | ✅ MANDATORY |
| 2. Spec | `turingmind_generate_spec` | ✅ MANDATORY |
| 3. Define Tests | `turingmind_define_required_tests` | ✅ MANDATORY |
| 4. Write Tests | `turingmind_validate_tests_written` | ✅ MANDATORY |
| 5. Code Edit | `turingmind_request_code_edit` | ✅ MANDATORY |
| 6. Validate | `turingmind_validate_tests_passing` | ✅ MANDATORY |
| 7. Complete | `turingmind_complete_tdd_cycle` | ✅ MANDATORY |

**There are no shortcuts. This is by design.**
