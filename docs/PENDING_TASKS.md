# Pending Tasks - Agent Implementation

## ✅ Completed

### Phase 1: Foundation
- ✅ BaseAgent with LangSmith tracing
- ✅ LLM providers (Azure OpenAI)
- ✅ ChatAnalysisAgent implementation
- ✅ LangSmith integration in MCP server
- ✅ Environment variable loading (.env support)
- ✅ All tests passing (37 tests)

## 🔴 Critical Pending Items

### 1. MCP Tool Wrapper for ChatAnalysisAgent ⚠️ **HIGH PRIORITY**

**Status:** Agent exists but no MCP tool to call it

**What's needed:**
- Create `turingmind_enhance_chat_analysis` MCP tool in `server.py`
- Register tool in `list_tools()`
- Implement handler in `call_tool()` that:
  - Accepts chat data (user_prompts, assistant_responses, files_discussed, etc.)
  - Calls `get_chat_analysis_agent().execute()`
  - Returns enhancement result

**Location:** `src/turingmind_mcp/server.py`

**Current state:**
- ✅ `get_chat_analysis_agent()` function exists (line 289)
- ❌ No MCP tool registered
- ❌ No handler in `call_tool()`

**Impact:** The agent cannot be used by the VS Code extension or any MCP client until this is done.

---

### 2. Extension Integration ⚠️ **HIGH PRIORITY**

**Status:** Extension still calls LLM directly

**What's needed:**
- Update `src/extension.ts` to call MCP tool instead of `llmService.enhanceChatAnalysis()`
- Replace direct LLM calls with MCP tool calls
- Handle MCP connection/errors gracefully

**Current state:**
- Extension calls `llmService.enhanceChatAnalysis()` directly (lines 2196, 3156, 3786)
- No MCP tool integration yet

**Files to update:**
- `turingmind-vscode/src/extension.ts`

**Impact:** Extension won't benefit from agent architecture, LangSmith tracing, or faster iteration until this is done.

---

## 🟡 Phase 2: Core Agents (Pending)

### KanbanBatchAnalysisAgent
- Analyze multiple Kanban items in batch
- Determine optimal phases, complexity, dependencies
- **Status:** Not started

### SDDPlanAgent
- Generate SDD plans from chat analysis
- Create structured development plans
- **Status:** Not started

### ThreadNameAgent
- Generate thread names from chat conversations
- Short, descriptive titles
- **Status:** Not started

---

## 🟡 Phase 3: Supporting Agents (Pending)

### DuplicateDetectionAgent
- Detect duplicate features/tasks
- Semantic similarity matching
- **Status:** Not started

### FeatureSuggestionAgent
- Suggest features from conversations
- Extract feature requirements
- **Status:** Not started

### FeatureDeduplicationAgent
- Deduplicate feature suggestions
- Merge similar features
- **Status:** Not started

### FeatureExtractionAgent
- Extract features from task lists
- Parse feature descriptions
- **Status:** Not started

### BatchDeduplicationAgent
- Batch deduplication operations
- Efficient processing
- **Status:** Not started

---

## 🟡 Phase 4: Workflow Orchestration (Pending)

### Primary Workflow
- ChatAnalysis → KanbanBatch → SDDPlan
- Sequential agent execution
- **Status:** Not started

### Feature Management Workflow
- Feature extraction → Deduplication → Suggestion
- **Status:** Not started

### Deduplication Workflow
- Batch deduplication with conflict resolution
- **Status:** Not started

---

## 📋 Implementation Priority

1. **🔴 CRITICAL:** MCP Tool Wrapper for ChatAnalysisAgent
   - Blocks all agent usage
   - Required for extension integration
   - Estimated: 1-2 hours

2. **🔴 CRITICAL:** Extension Integration
   - Enables actual usage of agents
   - Required for user-facing features
   - Estimated: 2-3 hours

3. **🟡 HIGH:** Remaining Core Agents (Phase 2)
   - KanbanBatchAnalysisAgent
   - SDDPlanAgent
   - ThreadNameAgent
   - Estimated: 1-2 days each

4. **🟡 MEDIUM:** Supporting Agents (Phase 3)
   - Various deduplication and feature agents
   - Estimated: 1 day each

5. **🟢 LOW:** Workflow Orchestration (Phase 4)
   - Agent sequencing and coordination
   - Estimated: 2-3 days

---

## 🎯 Next Immediate Steps

1. **Create MCP tool wrapper** (`turingmind_enhance_chat_analysis`)
   - Add tool definition to `list_tools()`
   - Add handler to `call_tool()`
   - Test with MCP client

2. **Update extension** to call MCP tool
   - Replace `llmService.enhanceChatAnalysis()` calls
   - Add MCP client connection handling
   - Test end-to-end

3. **Verify LangSmith tracing** works end-to-end
   - Check traces appear in LangSmith
   - Verify metadata is captured

---

## 📝 Notes

- All Phase 1 foundation is complete and tested
- Configuration is set up (`.env` file, LangSmith, Azure OpenAI)
- Agents are ready to use once MCP tool wrapper is created
- Extension integration is straightforward once MCP tool exists
