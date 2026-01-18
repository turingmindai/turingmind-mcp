# TuringMind-MCP Architecture Diagram

## Component Architecture Overview

### Mermaid Diagram

```mermaid
graph TB
    subgraph Platform["🖥️ Platform Integration Layer"]
        ClaudeDesktop["Claude Desktop<br/>(Native MCP)"]
        ClaudeCLI["Claude Code CLI<br/>(MCP Config)"]
        ClaudeSDK["Claude SDK<br/>(Python SDK)"]
        CursorIDE["Cursor IDE/CLI<br/>(Native MCP)"]
    end

    subgraph Interface["🔌 Interface Layer"]
        MCPServer["MCP Server<br/>(server.py)<br/><br/>17 MCP Tools:<br/>• Authentication (3)<br/>• Code Review (3)<br/>• Memory (5)<br/>• Code Indexing (3)<br/>• Additional (3)"]
        UnifiedCLI["Unified CLI<br/>(unified_cli.py)<br/><br/>Commands:<br/>• setup<br/>• validate<br/>• diagnose"]
        MCPClient["MCP Client SDK<br/>(client/*.py)<br/><br/>• Sync Client<br/>• Async Client<br/>• Context Manager<br/>• JSON-RPC Protocol"]
    end

    subgraph Core["⚙️ Core Services Layer"]
        ConfigMgr["Configuration Manager<br/>(config_manager.py)<br/><br/>• Platform Detection<br/>• Config Read/Write<br/>• JSON Merging<br/>• Validation<br/>• Backup Creation"]
        ErrorHandler["Error Handling<br/>(errors.py)<br/><br/>• ConfigError<br/>• ConnectionError<br/>• ToolError<br/>• Troubleshooting"]
    end

    subgraph Data["💾 Data & Memory Layer"]
        MemoryMgr["Memory Manager<br/>(memory_manager.py)<br/><br/>• CRUD Operations<br/>• Conflict Detection<br/>• Auto-learning<br/>• Relevance Scoring"]
        Database["Database<br/>(database.py)<br/><br/>SQLite - 9 Tables:<br/>• memory_entries<br/>• memory_evidence<br/>• memory_conflicts<br/>• memory_usage<br/>• memory_approvals<br/>• code_entities<br/>• relationships<br/>• git_commits<br/>• edit_reasoning"]
    end

    subgraph Analysis["🔍 Code Analysis Layer"]
        EntityIndexer["Entity Indexer<br/>(entity_indexer.py)<br/><br/>• Two-Pass Indexing<br/>• Global Entity Registry<br/>• Cross-File Relationships<br/>• Code Graph Generation"]
        TreeSitter["Tree-Sitter Manager<br/>(tree_sitter_manager.py)<br/><br/>• Grammar Management<br/>• Language Support"]
        PythonParser["Python Parser<br/><br/>• Functions<br/>• Classes<br/>• Imports<br/>• Calls<br/>• Inheritance"]
        JSParser["JavaScript Parser<br/><br/>• Functions<br/>• Classes<br/>• Imports<br/>• Calls<br/>• Inheritance"]
        TSParser["TypeScript Parser<br/><br/>• Functions<br/>• Classes<br/>• Imports<br/>• Calls<br/>• Inheritance"]
    end

    subgraph External["☁️ External Services"]
        CloudAPI["TuringMind Cloud API<br/><br/>• Authentication<br/>• Code Review Upload<br/>• Memory Context<br/>• Feedback Submission"]
    end

    %% Platform to Interface connections
    ClaudeDesktop --> MCPServer
    ClaudeCLI --> MCPServer
    ClaudeSDK --> MCPClient
    CursorIDE --> MCPServer
    MCPClient --> MCPServer

    %% Interface to Core connections
    MCPServer --> ConfigMgr
    MCPServer --> ErrorHandler
    MCPServer --> MemoryMgr
    MCPServer --> EntityIndexer
    UnifiedCLI --> ConfigMgr

    %% Core to Data connections
    MemoryMgr --> Database
    ConfigMgr --> ConfigFiles["Platform Config Files"]

    %% Interface to Analysis connections
    EntityIndexer --> TreeSitter
    TreeSitter --> PythonParser
    TreeSitter --> JSParser
    TreeSitter --> TSParser
    PythonParser --> Database
    JSParser --> Database
    TSParser --> Database

    %% Interface to External connections
    MCPServer --> CloudAPI

    %% Styling
    classDef platformStyle fill:#e1f5ff,stroke:#01579b,stroke-width:2px
    classDef interfaceStyle fill:#f3e5f5,stroke:#4a148c,stroke-width:2px
    classDef coreStyle fill:#fff3e0,stroke:#e65100,stroke-width:2px
    classDef dataStyle fill:#e8f5e9,stroke:#1b5e20,stroke-width:2px
    classDef analysisStyle fill:#fce4ec,stroke:#880e4f,stroke-width:2px
    classDef externalStyle fill:#fff9c4,stroke:#f57f17,stroke-width:2px

    class ClaudeDesktop,ClaudeCLI,ClaudeSDK,CursorIDE platformStyle
    class MCPServer,UnifiedCLI,MCPClient interfaceStyle
    class ConfigMgr,ErrorHandler coreStyle
    class MemoryMgr,Database dataStyle
    class EntityIndexer,TreeSitter,PythonParser,JSParser,TSParser analysisStyle
    class CloudAPI externalStyle
```

### ASCII Diagram (Legacy)

```
╔═════════════════════════════════════════════════════════════════════════════╗
║                    PLATFORM INTEGRATION LAYER                               ║
╠═════════════════════════════════════════════════════════════════════════════╣
║                                                                             ║
║  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌───────┐ ║
║  │  Claude Desktop │  │  Claude Code    │  │   Claude SDK     │  │Cursor │ ║
║  │                 │  │     CLI          │  │                 │  │IDE/CLI│ ║
║  │  (Native MCP)   │  │  (MCP Config)   │  │  (Python SDK)   │  │(Native│ ║
║  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  │  MCP) │ ║
║           │                     │                     │          └───┬───┘ ║
╚═══════════╪═════════════════════╪═════════════════════╪══════════════╪═════╝
            │                     │                     │              │
            └─────────────────────┴─────────────────────┴──────────────┘
                                      │
                                      ▼
╔═════════════════════════════════════════════════════════════════════════════╗
║                         INTERFACE LAYER                                    ║
╠═════════════════════════════════════════════════════════════════════════════╣
║                                                                             ║
║  ┌───────────────────────────────────────────────────────────────────────┐ ║
║  │                      MCP Server (server.py)                           │ ║
║  │  ┌─────────────────────────────────────────────────────────────────┐   │ ║
║  │  │                    17 MCP Tools                               │   │ ║
║  │  │  ┌───────────────────────────────────────────────────────────┐ │   │ ║
║  │  │  │  Authentication (3)                                      │ │   │ ║
║  │  │  │    • initiate_login  • poll_login  • validate_auth       │ │   │ ║
║  │  │  └───────────────────────────────────────────────────────────┘ │   │ ║
║  │  │  ┌───────────────────────────────────────────────────────────┐ │   │ ║
║  │  │  │  Code Review (3)                                        │ │   │ ║
║  │  │  │    • upload_review  • get_context  • submit_feedback    │ │   │ ║
║  │  │  └───────────────────────────────────────────────────────────┘ │   │ ║
║  │  │  ┌───────────────────────────────────────────────────────────┐ │   │ ║
║  │  │  │  Memory Management (5)                                    │ │   │ ║
║  │  │  │    • list  • get  • create  • update  • delete          │ │   │ ║
║  │  │  └───────────────────────────────────────────────────────────┘ │   │ ║
║  │  │  ┌───────────────────────────────────────────────────────────┐ │   │ ║
║  │  │  │  Code Indexing (3)                                        │ │   │ ║
║  │  │  │    • index_codebase  • get_related_code  • get_structure  │ │   │ ║
║  │  │  └───────────────────────────────────────────────────────────┘ │   │ ║
║  │  │  ┌───────────────────────────────────────────────────────────┐ │   │ ║
║  │  │  │  Additional (3)                                           │ │   │ ║
║  │  │  │    • edit_reasoning  • store_reasoning  • auto_review     │ │   │ ║
║  │  │  └───────────────────────────────────────────────────────────┘ │   │ ║
║  │  └─────────────────────────────────────────────────────────────────┘   │ ║
║  └───────────────────────────────────────────────────────────────────────┘ ║
║                                                                             ║
║  ┌──────────────────────────────┐    ┌──────────────────────────────┐     ║
║  │      Unified CLI              │    │    MCP Client SDK            │     ║
║  │   (unified_cli.py)           │    │    (client/*.py)             │     ║
║  │                              │    │                              │     ║
║  │  Commands:                   │    │  Features:                   │     ║
║  │    • setup                   │    │    • Sync Client             │     ║
║  │    • validate                │    │    • Async Client            │     ║
║  │    • diagnose                │    │    • Context Manager          │     ║
║  │                              │    │    • JSON-RPC Protocol       │     ║
║  └──────────────────────────────┘    └──────────────────────────────┘     ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
                                      │
                                      ▼
╔═════════════════════════════════════════════════════════════════════════════╗
║                       CORE SERVICES LAYER                                  ║
╠═════════════════════════════════════════════════════════════════════════════╣
║                                                                             ║
║  ┌──────────────────────────────────┐    ┌──────────────────────────────┐  ║
║  │   Configuration Manager          │    │    Error Handling            │  ║
║  │   (config_manager.py)            │    │    (errors.py)               │  ║
║  │                                  │    │                              │  ║
║  │  Features:                       │    │  Exception Types:            │  ║
║  │    • Platform Detection          │    │    • ConfigError             │  ║
║  │    • Config Read/Write           │    │    • ConnectionError          │  ║
║  │    • Safe JSON Merging           │    │    • ToolError                │  ║
║  │    • Validation                  │    │    • Troubleshooting Guide   │  ║
║  │    • Backup Creation             │    │                              │  ║
║  └──────────────────────────────────┘    └──────────────────────────────┘  ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
                                      │
                                      ▼
╔═════════════════════════════════════════════════════════════════════════════╗
║                      DATA & MEMORY LAYER                                    ║
╠═════════════════════════════════════════════════════════════════════════════╣
║                                                                             ║
║  ┌───────────────────────────────────────────────────────────────────────┐ ║
║  │                  Memory Manager (memory_manager.py)                   │ ║
║  │                                                                       │ ║
║  │  Features:                                                            │ ║
║  │    • CRUD Operations                                                 │ ║
║  │    • Conflict Detection & Resolution                                 │ ║
║  │    • Auto-learning from Feedback                                     │ ║
║  │    • Relevance Scoring                                              │ ║
║  └───────────────────────────────────────────────────────────────────────┘ ║
║                                      │                                      ║
║                                      ▼                                      ║
║  ┌───────────────────────────────────────────────────────────────────────┐ ║
║  │                    Database (database.py)                              │ ║
║  │  ┌─────────────────────────────────────────────────────────────────┐ │ ║
║  │  │              SQLite Database Tables (9)                          │ │ ║
║  │  │                                                                 │ │ ║
║  │  │  Memory Tables:                                                  │ │ ║
║  │  │    • memory_entries      → Memory storage                       │ │ ║
║  │  │    • memory_evidence     → Evidence for memory                 │ │ ║
║  │  │    • memory_conflicts    → Conflict tracking                   │ │ ║
║  │  │    • memory_usage        → Usage statistics                    │ │ ║
║  │  │    • memory_approvals    → Approval tracking                   │ │ ║
║  │  │                                                                 │ │ ║
║  │  │  Code Tables:                                                   │ │ ║
║  │  │    • code_entities       → Functions, classes, files           │ │ ║
║  │  │    • relationships        → Code relationships                 │ │ ║
║  │  │    • git_commits         → Git commit tracking                 │ │ ║
║  │  │    • edit_reasoning      → Per-file edit reasoning            │ │ ║
║  │  └─────────────────────────────────────────────────────────────────┘ │ ║
║  └───────────────────────────────────────────────────────────────────────┘ ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
                                      │
                                      ▼
╔═════════════════════════════════════════════════════════════════════════════╗
║                      CODE ANALYSIS LAYER                                    ║
╠═════════════════════════════════════════════════════════════════════════════╣
║                                                                             ║
║  ┌───────────────────────────────────────────────────────────────────────┐ ║
║  │              Entity Indexer (entity_indexer.py)                        │ ║
║  │                                                                       │ ║
║  │  Features:                                                            │ ║
║  │    • Two-Pass Indexing (Registry + Relationship Resolution)         │ ║
║  │    • Global Entity Registry                                         │ ║
║  │    • Cross-File Relationship Detection                              │ ║
║  │    • Code Graph Generation                                           │ ║
║  └───────────────────────────────────────────────────────────────────────┘ ║
║                                      │                                      ║
║                                      ▼                                      ║
║  ┌───────────────────────────────────────────────────────────────────────┐ ║
║  │                        Parser Layer                                     │ ║
║  │                                                                       │ ║
║  │  ┌─────────────────────────────────────────────────────────────────┐ │ ║
║  │  │      Tree-Sitter Manager (tree_sitter_manager.py)               │ │ ║
║  │  │        • Grammar Management                                     │ │ ║
║  │  │        • Language Support: Python, JavaScript, TypeScript       │ │ ║
║  │  └─────────────────────────────────────────────────────────────────┘ │ ║
║  │                                                                       │ ║
║  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                │ ║
║  │  │   Python     │  │ JavaScript  │  │ TypeScript   │                │ ║
║  │  │   Parser     │  │   Parser    │  │   Parser     │                │ ║
║  │  │              │  │             │  │             │                │ ║
║  │  │ • Functions  │  │ • Functions │  │ • Functions │                │ ║
║  │  │ • Classes    │  │ • Classes   │  │ • Classes   │                │ ║
║  │  │ • Imports    │  │ • Imports   │  │ • Imports   │                │ ║
║  │  │ • Calls      │  │ • Calls     │  │ • Calls     │                │ ║
║  │  │ • Inheritance│  │ • Inheritance│ │ • Inheritance│                │ ║
║  │  └──────────────┘  └──────────────┘  └──────────────┘                │ ║
║  └───────────────────────────────────────────────────────────────────────┘ ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
                                      │
                                      ▼
╔═════════════════════════════════════════════════════════════════════════════╗
║                      EXTERNAL SERVICES                                      ║
╠═════════════════════════════════════════════════════════════════════════════╣
║                                                                             ║
║  ┌───────────────────────────────────────────────────────────────────────┐ ║
║  │              TuringMind Cloud API                                      │ ║
║  │                                                                       │ ║
║  │  Services:                                                            │ ║
║  │    • Authentication (Device Code Flow)                               │ ║
║  │    • Code Review Upload                                              │ ║
║  │    • Memory Context Retrieval                                         │ ║
║  │    • Feedback Submission                                             │ ║
║  └───────────────────────────────────────────────────────────────────────┘ ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
```

## Component Relationships

### Mermaid Data Flow Diagram

```mermaid
flowchart TD
    Start([Platform Clients]) --> MCPServer[MCP Server<br/>JSON-RPC]
    Start --> UnifiedCLI[Unified CLI]
    Start --> SDK[MCP Client SDK]
    
    MCPServer --> ConfigMgr[Configuration Manager]
    MCPServer --> MemoryMgr[Memory Manager]
    MCPServer --> EntityIndexer[Entity Indexer]
    MCPServer --> ErrorHandler[Error Handler]
    
    ConfigMgr --> ConfigFiles[Platform Config Files]
    MemoryMgr --> Database[(SQLite Database)]
    EntityIndexer --> Parsers[Parsers]
    Parsers --> TreeSitter[Tree-Sitter]
    Parsers --> Database
    ErrorHandler --> Messages[User-Friendly Messages]
    
    SDK --> MCPServer
    
    style Start fill:#e1f5ff
    style MCPServer fill:#f3e5f5
    style Database fill:#e8f5e9
    style ConfigFiles fill:#fff3e0
    style Messages fill:#fce4ec
```

### Mermaid Dependency Graph

```mermaid
graph TD
    MCPServer[MCP Server<br/>server.py] --> ConfigMgr[config_manager.py]
    MCPServer --> MemoryMgr[memory_manager.py]
    MCPServer --> EntityIndexer[entity_indexer.py]
    MCPServer --> Errors[errors.py]
    MCPServer --> AutoReview[auto_review_service.py]
    
    ConfigMgr --> ConfigFiles[Platform Config Files]
    MemoryMgr --> Database[database.py]
    EntityIndexer --> Parsers[parsers/*.py]
    Parsers --> TreeSitter[tree_sitter_manager.py]
    Parsers --> Database
    
    Database --> SQLite[(SQLite DB)]
    
    style MCPServer fill:#f3e5f5,stroke:#4a148c,stroke-width:3px
    style Database fill:#e8f5e9,stroke:#1b5e20,stroke-width:2px
    style SQLite fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
```

### ASCII Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Platform Clients                                 │
│  (Claude Desktop, Claude CLI, Claude SDK, Cursor IDE/CLI)              │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                ┌────────────┼────────────┐
                │            │            │
                ▼            ▼            ▼
        ┌──────────────┐ ┌──────────┐ ┌──────────────┐
        │  MCP Server  │ │ Unified  │ │ MCP Client  │
        │  (JSON-RPC)  │ │   CLI    │ │     SDK     │
        └──────┬───────┘ └──────────┘ └──────┬──────┘
               │                              │
               │                              │
    ┌──────────┼──────────┬──────────┬───────┴──────────┐
    │          │          │          │                  │
    ▼          ▼          ▼          ▼                  ▼
┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────────┐
│ Config  │ │ Memory  │ │ Entity  │ │ Error   │ │ Auto Review │
│ Manager │ │ Manager │ │ Indexer │ │ Handler │ │  Service    │
└────┬────┘ └────┬────┘ └────┬────┘ └─────────┘ └──────────────┘
     │           │           │
     │           │           │
     ▼           ▼           ▼
┌─────────┐ ┌─────────┐ ┌─────────┐
│ Config  │ │Database  │ │ Parsers │
│ Files   │ │(SQLite)  │ │(Tree-   │
│         │ │          │ │ Sitter) │
└─────────┘ └─────────┘ └────┬────┘
                              │
                              ▼
                         ┌─────────┐
                         │Database │
                         │(Entities)│
                         └─────────┘
```

### Component Dependency Graph

```
                    ┌─────────────────────┐
                    │   MCP Server        │
                    │   (server.py)       │
                    └──────────┬──────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                        │
        ▼                      ▼                        ▼
┌───────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ Config Manager│    │  Memory Manager  │    │ Entity Indexer   │
│               │    │                  │    │                  │
│ • Platform    │    │ • CRUD Ops       │    │ • Two-Pass       │
│   Detection   │    │ • Conflicts       │    │ • Registry       │
│ • Validation  │    │ • Auto-learning   │    │ • Relationships │
└───────┬───────┘    └────────┬──────────┘    └────────┬─────────┘
        │                     │                        │
        ▼                     ▼                        ▼
┌───────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ Config Files  │    │    Database      │    │     Parsers      │
│               │    │  (database.py)   │    │                  │
│ • claude_*    │    │                  │    │ • Python Parser  │
│ • cursor/*    │    │ • 9 Tables       │    │ • JS Parser      │
│ • mcp.json    │    │ • SQLite         │    │ • TS Parser      │
└───────────────┘    └──────────────────┘    └────────┬─────────┘
                                                       │
                                                       ▼
                                              ┌──────────────────┐
                                              │ Tree-Sitter      │
                                              │ Manager          │
                                              │                  │
                                              │ • Grammars       │
                                              │ • Languages      │
                                              └──────────────────┘
```

## Component Details

### 1. Platform Integration Layer
- **Claude Desktop**: Native MCP integration via config file
- **Claude Code CLI**: MCP config or Skills integration
- **Claude SDK**: Python client wrapper for programmatic access
- **Cursor IDE/CLI**: Native MCP integration via `.cursor/mcp.json`

### 2. Interface Layer
- **MCP Server**: Main server with 17 tools, handles JSON-RPC protocol
- **Unified CLI**: Single command interface (`turingmind setup/validate/diagnose`)
- **MCP Client SDK**: Synchronous and asynchronous clients for SDK usage

### 3. Core Services
- **Configuration Manager**: Multi-platform config management with validation
- **Error Handling**: Platform-specific error messages and troubleshooting

### 4. Data & Memory Layer
- **Memory Manager**: High-level memory operations, conflict resolution
- **Database**: SQLite database with 9 tables for memory, entities, relationships

### 5. Code Analysis Layer
- **Entity Indexer**: Two-pass indexing with global entity registry
- **Parsers**: Language-specific AST parsers (Python, JavaScript, TypeScript)
- **Tree-Sitter Manager**: Grammar management for AST parsing

### 6. External Services
- **TuringMind Cloud API**: Authentication, review upload, context retrieval

## Key Features

### Mermaid: Cross-File Relationship Detection Flow

```mermaid
flowchart TD
    Start([Entity Indexer]) --> FirstPass[First Pass:<br/>Build Global Entity Registry]
    Start --> SecondPass[Second Pass:<br/>Resolve Relationships]
    
    FirstPass --> Registry[(name, type) → entities]
    
    SecondPass --> FunctionCalls[Function Calls<br/>calls]
    SecondPass --> Imports[Imports<br/>IMPORTS]
    SecondPass --> Inheritance[Inheritance<br/>EXTENDS_CLASS]
    
    Registry --> SecondPass
    
    style Start fill:#f3e5f5
    style FirstPass fill:#e1f5ff
    style SecondPass fill:#e1f5ff
    style Registry fill:#e8f5e9
    style FunctionCalls,Imports,Inheritance fill:#fff3e0
```

### Mermaid: Memory Management Flow

```mermaid
flowchart TD
    UserFeedback([User Feedback]) --> MemoryMgr[Memory Manager]
    
    MemoryMgr --> Conflict[Conflict Detection]
    MemoryMgr --> AutoLearn[Auto-Learning<br/>• Patterns<br/>• Rules<br/>• Facts]
    MemoryMgr --> Storage[Database Storage]
    
    Conflict --> Resolve[Resolve Conflicts]
    Storage --> Context[Context Retrieval<br/>for Reviews]
    
    style UserFeedback fill:#e1f5ff
    style MemoryMgr fill:#f3e5f5
    style Conflict fill:#fff3e0
    style AutoLearn fill:#e8f5e9
    style Storage fill:#e8f5e9
    style Context fill:#fce4ec
```

### ASCII: Cross-File Relationship Detection Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    Entity Indexer                              │
│                  (Two-Pass Process)                             │
└───────────────────────────┬─────────────────────────────────────┘
                            │
        ┌───────────────────┴───────────────────┐
        │                                       │
        ▼                                       ▼
┌───────────────────┐              ┌──────────────────────┐
│   First Pass      │              │   Second Pass         │
│                   │              │                       │
│ Build Global      │              │ Resolve Relationships │
│ Entity Registry   │              │                       │
│                   │              │                       │
│ (name, type) ───►│              │ • Function Calls      │
│   [entities]      │              │   (calls)             │
└───────────────────┘              │ • Imports             │
                                    │   (IMPORTS)           │
                                    │ • Inheritance         │
                                    │   (EXTENDS_CLASS)     │
                                    └──────────────────────┘
```

### Memory Management Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                      User Feedback                                │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
                ┌───────────────────────┐
                │   Memory Manager        │
                └───────────┬─────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                     │
        ▼                   ▼                     ▼
┌──────────────┐  ┌──────────────┐      ┌─────────────────┐
│   Conflict   │  │ Auto-Learning│      │  Database       │
│  Detection   │  │              │      │  Storage        │
│              │  │ • Patterns   │      │                 │
│ • Compare    │  │ • Rules      │      │ • Persistence    │
│ • Resolve    │  │ • Facts      │      │ • Query         │
└──────────────┘  └──────────────┘      └────────┬────────┘
                                                  │
                                                  ▼
                                         ┌─────────────────┐
                                         │ Context          │
                                         │ Retrieval        │
                                         │ (for Reviews)    │
                                         └─────────────────┘
```

## Statistics

- **Total Components**: 15+ core modules
- **MCP Tools**: 17 tools
- **Platforms Supported**: 5 (Claude Desktop, Claude CLI, Claude SDK, Cursor IDE, Cursor CLI)
- **Languages Parsed**: 3 (Python, JavaScript, TypeScript)
- **Database Tables**: 9 tables
- **Test Coverage**: 44 test cases
