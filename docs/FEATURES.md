# Features Documentation

## Overview

This document describes all features available in TuringMind-MCP, including MCP tools, user flows, and implementation details.

## Table of Contents

1. [MCP Tools](#mcp-tools)
2. [User Flows](#user-flows)
3. [Developer Intent Capture](#developer-intent-capture)
4. [Memory Management](#memory-management)
5. [Code Indexing](#code-indexing)

## MCP Tools

TuringMind-MCP provides **17 MCP tools** organized into categories:

### Authentication (3 Tools)

#### 1. `turingmind_initiate_login`
**Status**: ✅ Working

Starts device code authentication flow.

**Features**:
- Returns verification URL and user code
- No API key required
- Device code flow for secure authentication

**Usage**: Called automatically when user says "Log me into TuringMind"

#### 2. `turingmind_poll_login`
**Status**: ✅ Working

Polls for authentication completion.

**Features**:
- Automatically saves API key to `~/.turingmind/config`
- Returns API key on success
- Handles timeout and errors

**Usage**: Called after user enters device code

#### 3. `turingmind_validate_auth`
**Status**: ✅ Working

Validates existing API key.

**Features**:
- Returns account status and permissions
- Checks if key is still valid
- Verifies API key format

**Usage**: Validates authentication before operations

---

### Code Review (3 Tools)

#### 4. `turingmind_upload_review`
**Status**: ✅ Working

Uploads code review results to TuringMind cloud.

**Features**:
- Supports multiple issues with severity levels
- Includes file paths, line numbers, categories
- Returns review ID for tracking
- Supports CWE codes and confidence scores

**Example**:
```json
{
  "repo": "owner/repo",
  "branch": "main",
  "commit_hash": "abc123",
  "issues": [
    {
      "file": "src/file.py",
      "line": 42,
      "severity": "high",
      "category": "security",
      "title": "SQL Injection",
      "description": "User input directly in SQL query",
      "cwe": "CWE-89",
      "confidence": 95
    }
  ],
  "summary": {
    "critical": 0,
    "high": 1,
    "medium": 0,
    "low": 0
  }
}
```

#### 5. `turingmind_get_context`
**Status**: ✅ Working

Retrieves memory context for a repository.

**Features**:
- Returns relevant memory entries
- Filters by file paths
- Includes evidence and confidence scores
- Supports all memory categories

**Usage**: Called before code review to load context

#### 6. `turingmind_submit_feedback`
**Status**: ✅ Working

Submits feedback on review results.

**Features**:
- Marks issues as false positives
- Learns from feedback
- Updates memory entries
- Tracks feedback patterns

**Usage**: User provides feedback on review results

---

### Memory Management (5 Tools)

#### 7. `turingmind_list_memory`
**Status**: ✅ Working

Lists memory entries for a repository.

**Features**:
- Filters by category
- Supports pagination
- Returns summary information
- Sorted by relevance

#### 8. `turingmind_get_memory`
**Status**: ✅ Working

Gets specific memory entry details.

**Features**:
- Returns full memory entry
- Includes evidence
- Shows conflict status
- Returns usage statistics

#### 9. `turingmind_create_memory`
**Status**: ✅ Working

Creates new memory entry.

**Features**:
- Supports all categories
- Validates input
- Auto-detects conflicts
- Returns created entry

#### 10. `turingmind_update_memory`
**Status**: ✅ Working

Updates existing memory entry.

**Features**:
- Updates content and metadata
- Preserves evidence
- Handles conflicts
- Updates timestamps

#### 11. `turingmind_delete_memory`
**Status**: ✅ Working

Deletes memory entry.

**Features**:
- Soft delete option
- Cascade deletes evidence
- Updates related entries
- Returns confirmation

---

### Code Indexing (3 Tools)

#### 12. `turingmind_index_codebase`
**Status**: ✅ Working

Indexes codebase and extracts entities.

**Features**:
- Multi-language support (Python, JavaScript, TypeScript)
- AST-based parsing
- Entity extraction (functions, classes)
- Relationship extraction (calls, imports)
- Returns indexing statistics

#### 13. `turingmind_get_related_code`
**Status**: ✅ Working

Gets code related to specific entities.

**Features**:
- Finds related functions/classes
- Follows relationships
- Returns code snippets
- Supports depth limiting

#### 14. `turingmind_get_project_structure`
**Status**: ✅ Working

Gets project structure overview.

**Features**:
- File tree
- Entity counts
- Relationship statistics
- Language distribution

---

### Additional Tools (3 Tools)

#### 15. `turingmind_get_edit_reasoning`
**Status**: ✅ Working

Gets reasoning for file edits.

**Features**:
- Retrieves per-file reasoning
- Supports commit-based lookup
- Returns developer intent
- Includes context

#### 16. `turingmind_store_reasoning`
**Status**: ✅ Working

Stores reasoning for file edits.

**Features**:
- Stores per-file reasoning
- Links to commits
- Supports batch storage
- Validates input

#### 17. `turingmind_start_auto_review`
**Status**: ✅ Working

Starts auto-review polling service.

**Features**:
- Monitors Git repository
- Triggers reviews on new commits
- Background service
- Configurable polling interval

---

## User Flows

### Initial Setup Flow

1. **Install & Configure**
   ```bash
   pip install turingmind-mcp
   turingmind setup claude_desktop
   ```

2. **Login to TuringMind**
   - User says: "Log me into TuringMind"
   - MCP tools triggered:
     - `turingmind_initiate_login` → Returns device code
     - User opens URL, enters code
     - `turingmind_poll_login` → Completes auth
     - `turingmind_validate_auth` → Verifies key

3. **Result**: API key saved to `~/.turingmind/config`

---

### Daily Development Workflow

#### Pre-Commit Review (Automatic)

**Trigger**: User runs `git commit`

**Flow**:
1. Pre-commit hook captures staged files
2. Extracts developer intent (per-file reasoning)
3. Calls `/tmind:review` on staged files
4. Stores reasoning in database
5. Blocks commit if Critical issues found

**MCP Tools Used**:
- `turingmind_get_context` - Load memory
- `turingmind_upload_review` - Upload results
- `turingmind_store_reasoning` - Store intent

#### Pre-Push Review (Automatic)

**Trigger**: User runs `git push`

**Flow**:
1. Pre-push hook extracts commit messages
2. Analyzes impact with memory context
3. Calls `/tmind:review` on changed files
4. Stores per-commit reasoning
5. Blocks push if Critical issues found

**MCP Tools Used**:
- `turingmind_get_context` - Load memory
- `turingmind_get_edit_reasoning` - Get existing reasoning
- `turingmind_upload_review` - Upload results

#### Manual Review (On-Demand)

**Trigger**: User says "Review my code" or `/tmind:review`

**Flow**:
1. User selects files or uses current changes
2. System loads memory context
3. Runs code review
4. Displays results
5. User can submit feedback

**MCP Tools Used**:
- `turingmind_get_context` - Load memory
- `turingmind_index_codebase` - Index if needed
- `turingmind_upload_review` - Upload results
- `turingmind_submit_feedback` - User feedback

---

### Memory Learning Flow

**Trigger**: User submits feedback on review

**Flow**:
1. User marks issue as false positive
2. `turingmind_submit_feedback` called
3. System extracts pattern
4. Creates/updates Learned Pattern memory
5. Future reviews use this pattern

**MCP Tools Used**:
- `turingmind_submit_feedback` - Submit feedback
- `turingmind_create_memory` - Create pattern
- `turingmind_update_memory` - Update existing

---

## Developer Intent Capture

### Per-File Reasoning

**Purpose**: Capture why each file was edited

**Storage**:
- Database table: `edit_reasoning`
- Fields: `file_path`, `reasoning`, `commit_hash`, `created_at`
- Linked to git commits

**Capture Methods**:

1. **Git Hooks** (Automatic)
   - Pre-commit hook captures reasoning
   - Stored before commit
   - Linked to commit hash

2. **Commit Messages** (Automatic)
   - Pre-push hook extracts from messages
   - Parses structured format
   - Links to commits

3. **Manual Entry** (On-Demand)
   - User provides reasoning
   - Stored via `turingmind_store_reasoning`
   - Can be updated later

**Usage**:
- Loaded during code review
- Used to understand context
- Helps reduce false positives
- Improves review accuracy

### Overall Reasoning

**Purpose**: Capture overall change intent

**Storage**:
- Git config: `tmind.reasoning`
- Commit messages
- Database: linked to commits

**Example**:
```bash
git config --local tmind.reasoning "Refactoring authentication module"
```

---

## Memory Management

### Memory Categories

#### 1. Repo Facts (Auto-Extracted)
- Framework detection
- Monorepo detection
- Architecture patterns
- Read-only, auto-updated

#### 2. Learned Patterns (Auto-Learned)
- False positive patterns
- Common code patterns
- Team conventions
- Confidence-based

#### 3. Explicit Rules (User-Defined)
- Developer-defined rules
- YAML definitions
- Security tags
- Manual creation

#### 4. Session Context (Ephemeral)
- Conversation-based
- Temporary memory
- Auto-expires

### Conflict Detection

**Purpose**: Detect conflicting memory entries

**Process**:
1. New memory entry created
2. System checks for conflicts
3. Detects overlapping patterns
4. Flags conflicts
5. User resolves or auto-resolves

**Resolution**:
- Manual: User chooses
- Automatic: Higher confidence wins
- Merge: Combine evidence

---

## Code Indexing

### Entity Extraction

**Languages Supported**:
- Python (tree-sitter)
- JavaScript (tree-sitter)
- TypeScript (tree-sitter)

**Entities Extracted**:
- Functions
- Classes
- Methods
- Variables
- Imports

### Relationship Extraction

**Relationships Tracked**:
- Function calls
- Class inheritance
- Imports
- References

**Storage**:
- Database table: `relationships`
- Graph structure
- Queryable via MCP tools

### Usage

**Indexing**:
```bash
# Via MCP tool
turingmind_index_codebase

# Returns:
{
  "entities_indexed": 1234,
  "relationships_indexed": 5678,
  "files_processed": 89,
  "failed_files": []
}
```

**Querying**:
```bash
# Get related code
turingmind_get_related_code

# Returns related entities and code snippets
```

---

## References

- [Development Documentation](DEVELOPMENT.md)
- [User Flow Details](USER_FLOW.md) (if kept separate)
- [Platform Guides](platforms/)
