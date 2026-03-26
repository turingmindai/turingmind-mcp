# TuringMind TDD/SDD Workflow - MANDATORY RULES

## CRITICAL: No Code Edits Without EditPlan

You MUST follow this EXACT workflow for ANY code changes. Reasoning is not observed — it is demanded.

---

## Phase 1: CREATE EDIT PLAN (MANDATORY)

Before touching ANY code file, you MUST call:

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

### Rules:
- ❌ NEVER skip this step
- ❌ NEVER make code edits without a plan_id
- ❌ NEVER proceed if this tool returns an error
- ✅ ALWAYS provide clear acceptance criteria
- ✅ ALWAYS list all files that will be modified

---

## Phase 2: GENERATE SPEC (MANDATORY)

After creating the EditPlan, you MUST call:

```
turingmind_generate_spec(plan_id="...")
```

This generates Gherkin-style specifications from your acceptance criteria.

### What it produces:
- Feature description
- Scenario definitions (Given/When/Then)
- Requirement IDs for traceability

---

## Phase 3: DEFINE TESTS - RED PHASE (MANDATORY)

You MUST define required tests:

```
turingmind_define_required_tests(
  spec_id="...",
  tests=[
    {
      "name": "test_descriptive_name",
      "type": "unit",  // or "integration", "e2e"
      "test_file": "tests/test_feature.py",
      "failure_mode": "Expected X but got Y"
    }
  ]
)
```

### Rules:
- ✅ Each acceptance criterion should have at least one test
- ✅ Tests must be specific and measurable
- ✅ Include failure_mode to describe what failing looks like

---

## Phase 4: WRITE FAILING TESTS (MANDATORY)

Write the test code. Tests MUST FAIL initially.

Then validate:

```
turingmind_validate_tests_written(plan_id="...")
```

### Rules:
- ❌ NEVER write implementation code before tests
- ❌ NEVER skip the failing test validation
- ✅ Tests should fail for the RIGHT reason (missing implementation)

---

## Phase 5: REQUEST CODE EDIT - GREEN PHASE

ONLY after tests are validated, you may request edit permission:

```
turingmind_request_code_edit(
  plan_id="...",
  file_path="src/feature.py",
  edit_type="modify",  // or "create", "delete"
  acceptance_criteria_addressed=["criteria from plan"]
)
```

### Rules:
- If this returns "rejected", you CANNOT make the edit
- You must specify which acceptance criteria the edit addresses
- Only files listed in the EditPlan can be edited

---

## Phase 6: VALIDATE AND COMPLETE

After edits, validate tests pass:

```
turingmind_validate_tests_passing(plan_id="...")
```

Then complete the cycle:

```
turingmind_complete_tdd_cycle(plan_id="...")
```

---

## ENFORCEMENT SUMMARY

| Phase | Tool | Required |
|-------|------|----------|
| 1. Planning | `turingmind_create_edit_plan` | ✅ MANDATORY |
| 2. Spec | `turingmind_generate_spec` | ✅ MANDATORY |
| 3. Define Tests | `turingmind_define_required_tests` | ✅ MANDATORY |
| 4. Write Tests | `turingmind_validate_tests_written` | ✅ MANDATORY |
| 5. Code Edit | `turingmind_request_code_edit` | ✅ MANDATORY |
| 6. Validate | `turingmind_validate_tests_passing` | ✅ MANDATORY |
| 7. Complete | `turingmind_complete_tdd_cycle` | ✅ MANDATORY |

---

## WHY THIS MATTERS

Every AI-generated change comes with:
- ✅ Auditable design rationale
- ✅ Explicit acceptance criteria
- ✅ Test coverage proof
- ✅ Full traceability

This is what enterprise trust looks like.

---

## QUICK REFERENCE

### Check Current Status
```
turingmind_get_tdd_status(repo="owner/repo")
```

### Get Full Audit Trail
```
turingmind_get_audit_trail(plan_id="...")
```

---

## EXAMPLE WORKFLOW

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

**Step 7: Make the Edit (implement the fix)**

**Step 8: Validate Tests Pass**
```
turingmind_validate_tests_passing(plan_id="abc123...")
```

**Step 9: Complete Cycle**
```
turingmind_complete_tdd_cycle(plan_id="abc123...")
```

---

## VIOLATIONS

If you attempt to edit code without following this workflow:

1. The edit WILL BE REJECTED
2. You MUST restart from Phase 1
3. All reasoning MUST be explicit

**There are no shortcuts. This is by design.**
