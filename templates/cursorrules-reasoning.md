# TuringMind Reasoning-Captured Development

## CRITICAL: Always Document Your Reasoning

Before making ANY code change, you MUST capture your reasoning using TuringMind tools.

## Required Workflow

### For EVERY File Edit

Use `turingmind_apply_edit` instead of direct file edits:

```
turingmind_apply_edit({
    reasoning: "WHY you are making this change",
    problem_observed: "What issue/problem you identified",
    approach: "How you are solving it",
    alternatives_considered: ["Other approaches you rejected"],
    file_path: "path/to/file",
    edit_type: "create" | "modify" | "delete",
    old_content: "content to replace (for modify)",
    new_content: "new content"
})
```

### For Analysis and Decisions

Use `turingmind_log_reasoning` to document your thought process:

```
turingmind_log_reasoning({
    reasoning_type: "analysis" | "decision" | "observation" | "plan" | "concern",
    content: "Your reasoning/analysis",
    context: "What you were examining",
    related_files: ["file1.ts", "file2.ts"],
    confidence: 0.0 - 1.0
})
```

## Examples

### Good: Documenting a Bug Fix

```
// First, log your analysis
turingmind_log_reasoning({
    reasoning_type: "analysis",
    content: "Token refresh has a race condition. When multiple requests hit simultaneously, they all try to refresh, causing duplicate API calls and potential state corruption.",
    context: "Investigating auth failures reported in production",
    related_files: ["src/auth/token.ts"],
    confidence: 0.9
})

// Then, make the edit with full reasoning
turingmind_apply_edit({
    reasoning: "Adding mutex lock to prevent concurrent token refreshes",
    problem_observed: "Race condition causes duplicate refresh calls when token expires during multiple simultaneous requests",
    approach: "Use a promise-based mutex that queues subsequent refresh requests while one is in progress",
    alternatives_considered: [
        "Debouncing - rejected because it might miss legitimate refresh needs",
        "Single refresh flag - rejected because it doesn't queue waiting requests"
    ],
    file_path: "src/auth/token.ts",
    edit_type: "modify",
    old_content: "async function refreshToken() {",
    new_content: "let refreshPromise: Promise<void> | null = null;\n\nasync function refreshToken() {\n    if (refreshPromise) return refreshPromise;\n    refreshPromise = doRefresh().finally(() => { refreshPromise = null; });\n    return refreshPromise;\n}\n\nasync function doRefresh() {"
})
```

### Good: Documenting a Design Decision

```
turingmind_log_reasoning({
    reasoning_type: "decision",
    content: "Choosing to use Zustand over Redux for state management. Zustand has simpler API, smaller bundle size, and better TypeScript support. The team is already familiar with hooks-based state.",
    context: "Evaluating state management for new dashboard feature",
    related_files: ["src/store/", "package.json"],
    confidence: 0.85
})
```

## Why This Matters

1. **Audit Trail**: Every change is traceable to its reasoning
2. **Knowledge Transfer**: Future developers understand WHY, not just WHAT
3. **AI Learning**: Better context for future AI assistance
4. **Compliance**: Full documentation for regulated industries

## Enforcement

- ALL file edits should go through `turingmind_apply_edit`
- Complex analysis should be logged with `turingmind_log_reasoning`
- Missing reasoning = incomplete documentation

## Quick Reference

| Action | Tool |
|--------|------|
| Create file | `turingmind_apply_edit` with `edit_type: "create"` |
| Modify file | `turingmind_apply_edit` with `edit_type: "modify"` |
| Delete file | `turingmind_apply_edit` with `edit_type: "delete"` |
| Log thinking | `turingmind_log_reasoning` |
| Log decision | `turingmind_log_reasoning` with `reasoning_type: "decision"` |
| Log concern | `turingmind_log_reasoning` with `reasoning_type: "concern"` |
