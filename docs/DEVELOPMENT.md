# Development Documentation

## Overview

This document consolidates all development and implementation information for TuringMind-MCP, including architecture, implementation history, and development guidelines.

## Table of Contents

1. [Architecture](#architecture)
2. [Implementation History](#implementation-history)
3. [Component Details](#component-details)
4. [Development Guidelines](#development-guidelines)
5. [Testing](#testing)

## Architecture

### Core Components

#### MCP Server
- **File**: `src/turingmind_mcp/server.py`
- **Purpose**: Main MCP server implementation with 17 tools
- **Features**: Authentication, code review, memory management, code indexing

#### Configuration Manager
- **File**: `src/turingmind_mcp/config_manager.py`
- **Purpose**: Multi-platform configuration management
- **Features**: 
  - Platform detection (macOS, Windows, Linux)
  - Safe JSON merging
  - Config validation
  - Backup creation

#### MCP Client
- **Files**: `src/turingmind_mcp/client/client.py`, `src/turingmind_mcp/client/async_client.py`
- **Purpose**: SDK client for programmatic access
- **Features**: Synchronous and asynchronous clients with context managers

#### Error Handling
- **File**: `src/turingmind_mcp/errors.py`
- **Purpose**: Platform-specific error messages and troubleshooting
- **Features**: Custom exceptions with automatic troubleshooting guidance

#### Unified CLI
- **File**: `src/turingmind_mcp/unified_cli.py`
- **Purpose**: Single command interface for all platforms
- **Commands**: `setup`, `validate`, `diagnose`

### Database & Memory

#### Memory Database
- **File**: `src/turingmind_mcp/database.py`
- **Purpose**: SQLite database for memory management
- **Features**: 
  - Memory entries (Repo Facts, Learned Patterns, Explicit Rules, Session Context)
  - Code entities and relationships
  - Git commits and edit reasoning
  - Conflict detection

#### Memory Manager
- **File**: `src/turingmind_mcp/memory_manager.py`
- **Purpose**: High-level memory operations
- **Features**: 
  - Memory CRUD operations
  - Conflict detection and resolution
  - Auto-learning from feedback
  - Relevance scoring

#### Entity Indexer
- **File**: `src/turingmind_mcp/entity_indexer.py`
- **Purpose**: Code entity extraction and indexing
- **Features**: 
  - AST parsing (Python, JavaScript, TypeScript)
  - Relationship extraction
  - Code graph generation

### Parsers

#### Tree-Sitter Manager
- **File**: `src/turingmind_mcp/parsers/tree_sitter_manager.py`
- **Purpose**: Tree-sitter grammar management
- **Languages**: Python, JavaScript, TypeScript

#### Language Parsers
- **Files**: 
  - `src/turingmind_mcp/parsers/python_parser.py`
  - `src/turingmind_mcp/parsers/javascript_parser.py`
  - `src/turingmind_mcp/parsers/typescript_parser.py`
- **Purpose**: Language-specific AST parsing

## Implementation History

### Phase 1: Assessment and Preparation (Week 1)

**Deliverables:**
- Integration assessment document
- Gap analysis
- Requirements matrix
- Implementation priorities

**Key Findings:**
- Claude Desktop: ✅ Working
- Claude Code CLI (Skills): ✅ Working
- Claude Code CLI (MCP Config): ⚠️ Needed documentation
- Claude SDK: ❌ Missing client wrapper
- Cursor IDE: ❌ Missing integration
- Cursor CLI: ❌ Missing integration

### Phase 2: Core Infrastructure (Weeks 2-3)

**Components Implemented:**
1. **Configuration Management System**
   - Multi-platform config support
   - Config validation
   - Safe JSON merging
   - Backup creation

2. **MCP Client Wrapper**
   - Synchronous client
   - Asynchronous client
   - Context manager support
   - JSON-RPC protocol

3. **Enhanced Error Handling**
   - Platform-specific messages
   - Custom exception classes
   - Troubleshooting guidance

### Phase 3: Platform Integrations (Weeks 4-5)

**Platform Guides Created:**
- Claude Desktop
- Claude Code CLI (MCP config + Skills)
- Claude SDK
- Cursor IDE
- Cursor CLI

**Templates Created:**
- Claude Desktop config template
- Claude CLI config template
- Cursor config template

### Phase 4: Developer Experience (Weeks 6-7)

**Automation Scripts:**
- `setup-claude-desktop.sh`
- `setup-claude-cli.sh`
- `setup-cursor.sh`

**Unified CLI:**
- `turingmind setup <platform>`
- `turingmind validate <platform>`
- `turingmind diagnose`

### Phase 5: Testing and Validation (Weeks 8-10)

**Test Suites:**
- Unit tests (36 tests)
- Integration tests (5 tests)
- Performance tests (3 tests)
- **Total**: 44 test cases

**Documentation:**
- Compatibility matrix
- Testing guide
- Test results

## Component Details

### MCP Tools (17 Total)

#### Authentication (3)
- `turingmind_initiate_login` - Start device code flow
- `turingmind_poll_login` - Poll for auth completion
- `turingmind_validate_auth` - Validate API key

#### Code Review (3)
- `turingmind_upload_review` - Upload review results
- `turingmind_get_context` - Get memory context
- `turingmind_submit_feedback` - Submit feedback

#### Memory Management (5)
- `turingmind_list_memory` - List memory entries
- `turingmind_get_memory` - Get specific memory
- `turingmind_create_memory` - Create memory entry
- `turingmind_update_memory` - Update memory entry
- `turingmind_delete_memory` - Delete memory entry

#### Code Indexing (3)
- `turingmind_index_codebase` - Index codebase
- `turingmind_get_related_code` - Get related code
- `turingmind_get_project_structure` - Get project structure

#### Additional (3)
- `turingmind_get_edit_reasoning` - Get edit reasoning
- `turingmind_store_reasoning` - Store reasoning
- `turingmind_start_auto_review` - Start auto-review service

### Database Schema

#### Tables
- `memory_entries` - Memory storage
- `memory_evidence` - Evidence for memory
- `memory_conflicts` - Conflict tracking
- `memory_usage` - Usage statistics
- `memory_approvals` - Approval tracking
- `code_entities` - Code entities (functions, classes)
- `relationships` - Code relationships
- `git_commits` - Git commit tracking
- `edit_reasoning` - Per-file edit reasoning

## Development Guidelines

### Code Style

- **Python Version**: 3.10+
- **Formatting**: Black (line length 100)
- **Linting**: Ruff
- **Type Checking**: mypy

### Testing

- **Framework**: pytest
- **Coverage Target**: 85%+
- **Test Location**: `tests/`
- **Run Tests**: `pytest tests/`

### Documentation

- **User Docs**: `docs/platforms/`
- **API Docs**: Inline docstrings
- **Architecture**: This document

### Git Workflow

1. Create feature branch
2. Implement changes
3. Add tests
4. Update documentation
5. Submit PR

## Testing

See [TESTING_GUIDE.md](TESTING_GUIDE.md) for detailed testing information.

### Test Structure

```
tests/
├── conftest.py              # Test configuration
├── test_config_manager.py   # Config manager tests (15)
├── test_client.py           # MCP client tests (8)
├── test_errors.py           # Error handling tests (7)
├── test_unified_cli.py      # CLI tests (6)
├── test_integration.py       # Integration tests (5)
└── test_performance.py       # Performance tests (3)
```

### Running Tests

```bash
# All tests
pytest tests/

# With coverage
pytest tests/ --cov=turingmind_mcp

# Specific suite
pytest tests/test_config_manager.py
```

## File Structure

```
turingmind-mcp/
├── src/turingmind_mcp/
│   ├── server.py            # MCP server
│   ├── config_manager.py    # Config management
│   ├── errors.py            # Error handling
│   ├── unified_cli.py       # Unified CLI
│   ├── database.py          # Database
│   ├── memory_manager.py    # Memory management
│   ├── entity_indexer.py    # Code indexing
│   ├── client/              # MCP client SDK
│   └── parsers/             # AST parsers
├── tests/                   # Test suite
├── docs/                    # Documentation
├── scripts/                 # Setup scripts
└── templates/               # Config templates
```

## Statistics

- **Python Files**: ~2,000 lines (core) + ~2,500 lines (tests)
- **Documentation**: 20+ files
- **Test Coverage**: 44 test cases
- **Platforms Supported**: 5
- **MCP Tools**: 17

## Parser Architecture & Improvements

### Overview

The code indexing system uses tree-sitter for AST parsing to extract code entities and relationships. Recent improvements enable cross-file relationship detection.

### Global Entity Registry

**Problem**: Parsers only searched for entities within the current file, missing cross-file relationships.

**Solution**: Added a global entity registry in `EntityIndexer` that:
- Builds a registry of all entities as files are indexed
- Maps `(name, entity_type)` → list of entities
- Passes registry to parsers for cross-file lookups

**Implementation**:
```python
entity_registry: Dict[tuple, List[Dict[str, Any]]]
# Maps (name, entity_type) -> list of entities
# Example: ("get_api_url", "function_definition") -> [entity1, entity2, ...]
```

### Two-Pass Indexing

1. **First Pass**: Index all files, build entity registry
2. **Second Pass**: Resolve relationships:
   - For `calls`: Search for function/method entities
   - For `IMPORTS`: Search for function/class entities
   - For `EXTENDS_CLASS`: Search for class entities

### Parser Improvements

#### Function Call Resolution
- Updated `find_entity_by_name()` to search registry
- Detects function calls between files
- Tries multiple entity types (function, function_definition, method_definition)

#### Import Relationship Tracking
- Links imports to actual imported entities
- Searches registry for imported functions/classes
- Creates relationships even if entity not found (for external imports)

#### Class Inheritance Tracking
- Searches global registry for base classes
- Links inheritance relationships across files
- Uses consistent text extraction helpers

### Parser Signature

All parsers now accept optional `entity_registry`:

```python
def parse_python(
    ast_root_node: Any, 
    file_content: str,
    entity_registry: Optional[Dict[tuple, List[Dict[str, Any]]]] = None
) -> Dict[str, Any]:
```

### Benefits

1. **Cross-File Relationships**: Detects function calls between files
2. **Import Tracking**: Links imports to actual imported entities
3. **Inheritance Tracking**: Finds base classes across files
4. **Better Graph**: More complete relationship graph for code review

### Files Modified

- `src/turingmind_mcp/entity_indexer.py` - Added registry and two-pass resolution
- `src/turingmind_mcp/parsers/python_parser.py` - Updated to use registry
- `src/turingmind_mcp/parsers/javascript_parser.py` - Updated to use registry
- `src/turingmind_mcp/parsers/typescript_parser.py` - Updated to pass registry

### Development Setup with Local Source

For development, you can use the local source code with the MCP server:

1. **Configuration**: `.cursor/mcp.json` uses installed `turingmind-mcp` with `PYTHONPATH` override
2. **Benefits**:
   - Dependencies available (from pipx installation)
   - Local source code used (from repo)
   - Changes take effect immediately after restarting Cursor

3. **Testing**:
   - Restart Cursor after changes
   - Verify MCP server is running in Settings → Tools & Integrations → MCP
   - Test tools via Cursor chat interface

## References

- [Testing Guide](TESTING_GUIDE.md)
- [Compatibility Matrix](COMPATIBILITY_MATRIX.md)
- [Features Documentation](FEATURES.md)