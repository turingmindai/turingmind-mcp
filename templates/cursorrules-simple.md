# TuringMind - Capture Reasoning With Every Edit

## ONE SIMPLE RULE

**Use `turingmind_apply_edit` for ALL file changes.**

```
turingmind_apply_edit({
    reasoning: "WHY you are making this change",
    file_path: "path/to/file",
    edit_type: "modify",
    old_content: "text to find",
    new_content: "replacement text"
})
```

That's it. Every edit captures its reasoning automatically.

---

## Full Parameters

```
turingmind_apply_edit({
    // REQUIRED
    reasoning: "WHY you are making this change",
    file_path: "path/to/file",
    edit_type: "create" | "modify" | "delete",
    
    // FOR MODIFY
    old_content: "text to find and replace",
    new_content: "replacement text",
    
    // FOR CREATE
    full_content: "entire file contents",
    
    // OPTIONAL BUT HELPFUL
    problem_observed: "What problem you found",
    approach: "How you're solving it",
    alternatives_considered: ["Other options you rejected"]
})
```

---

## For Thinking/Analysis

When you're analyzing code or making decisions (without editing), log it:

```
turingmind_log_reasoning({
    reasoning_type: "analysis",  // or "decision", "observation", "plan", "concern"
    content: "Your analysis or thought",
    related_files: ["relevant/files.ts"]
})
```

---

## Examples

### Updating a dependency

```
turingmind_apply_edit({
    reasoning: "Updating express to fix CVE-2024-1234 security vulnerability",
    file_path: "package.json",
    edit_type: "modify",
    old_content: '"express": "^4.17.1"',
    new_content: '"express": "^4.18.2"'
})
```

### Adding a null check

```
turingmind_apply_edit({
    reasoning: "Preventing crash when user object is undefined",
    problem_observed: "App crashes on logout when user is null",
    approach: "Add early return if user is falsy",
    file_path: "src/auth/profile.ts",
    edit_type: "modify",
    old_content: "function getProfile() {\n  return user.profile;",
    new_content: "function getProfile() {\n  if (!user) return null;\n  return user.profile;"
})
```

### Creating a new file

```
turingmind_apply_edit({
    reasoning: "Adding utility function for date formatting used across components",
    file_path: "src/utils/dates.ts",
    edit_type: "create",
    full_content: "export function formatDate(d: Date): string {\n  return d.toISOString().split('T')[0];\n}"
})
```

---

## What Gets Captured

Every edit stores:
- **Reasoning** - WHY you made the change
- **Problem** - What issue led to this
- **Approach** - How you solved it
- **Alternatives** - What you considered but rejected
- **File + Diff** - What changed

All searchable, auditable, traceable.

---

## Why This Matters

1. **Future you** will know why code exists
2. **Team members** can understand decisions
3. **AI** can learn from past reasoning
4. **Compliance** gets full audit trail

---

## That's It

No complex workflow. No multiple steps. Just:

**Every edit → reasoning captured.**
