# LLM Input Data Analysis

## Overview

This document details all data that is sent to the LLM for chat analysis via the `ChatAnalysisAgent`.

## Data Flow

```
Extension → MCP Tool → ChatAnalysisAgent → LLM Provider → Azure OpenAI
```

## 1. Extension Input Data

The extension calls `enhanceChatAnalysisViaMCP()` with:

### Required Parameters:
- **`userPrompts`**: `Array<{text: string; timestamp: number; sequence: number}>`
  - Full user prompt text
  - Timestamp (milliseconds)
  - Sequence number (0-based index)

- **`assistantResponses`**: `Array<{text: string; timestamp: number; sequence: number}>`
  - Full assistant response text
  - Timestamp (milliseconds)
  - Sequence number (0-based index)

### Optional Parameters:
- **`filesDiscussed`**: `string[]`
  - Array of file paths mentioned/discussed in conversation
  - Example: `["src/auth.py", "src/login.py"]`

- **`aiTodos`**: `Array<{content: string; status: string}>`
  - AI-generated todos/tasks
  - Content and status (e.g., "pending", "completed", "done")

- **`reasoning`**: `Array<{bubbleId: string; reasoning: string[]; timestamp: number; sequence?: number}>`
  - AI reasoning blocks (thinking process)
  - Bubble ID, array of reasoning strings, timestamp, optional sequence

- **`previousSummary`**: `{summary?: string; keyDecisions?: string[]; actionItems?: Array<{task: string; priority: string; status: string}>}`
  - Previous analysis summary (for incremental processing)
  - Summary text, key decisions array, action items array

## 2. Agent Processing

The `ChatAnalysisAgent` processes this data:

### Step 1: Build Conversation Exchanges
- Interleaves user prompts and assistant responses by sequence
- Matches reasoning blocks to exchanges (by sequence or timestamp proximity)
- Extracts code block summaries from responses
- Detects conversation pivots (topic changes)
- Calculates relative timestamps

### Step 2: Select Key Exchanges
- Always includes: first exchange, last exchange, pivot exchanges
- Fills remaining with evenly spaced exchanges
- Max token budget: 4000 tokens
- Sorts by sequence

### Step 3: Build Prompt

The prompt includes:

#### A. Conversation Overview (Full Mode)
```
- Total exchanges: {count}
- Duration: {hours}h {minutes}m
- Direction changes (pivots): {count}
- Files touched: {count}
- Tasks created: {count}
```

#### B. Previous Analysis (Incremental Mode)
```
## PREVIOUS ANALYSIS (for context)
Summary: {previous_summary.summary}
Key Decisions: {decision1}; {decision2}; ...
Previous Action Items: {task1}; {task2}; ...
```

#### C. Conversation Exchanges
For each key exchange:
```
### [{relative_time}][🔄 PIVOT] Exchange #{sequence} [(KEY)]
**User:** {user_prompt[:250]}...
**AI Thinking:** {reasoning[:100]} | {reasoning[:100]}  (if available)
**AI Response:** {response_without_code[:2000 or 500]}...  (truncated based on importance)
**Code Changes:** {code_summary1}, {code_summary2}, ...  (up to 10 for key exchanges, 3 for others)
```

**Exchange Selection Logic:**
- **Key exchanges** (first, last, pivots): Up to 2000 chars for response
- **Other exchanges**: Up to 500 chars for response
- **Code blocks**: Removed from response text, summarized separately
- **Reasoning**: Up to 2 reasoning strings, each truncated to 100 chars

#### D. Files Context
```
## FILES MODIFIED ({count} total)
{file1}
{file2}
...
{file15}
... and {remaining} more  (if > 15 files)
```

#### E. Task Status
```
## TASK STATUS ({completed}/{total} completed)
- [{status}] {task_content}
- [{status}] {task_content}
...  (up to 8 tasks)
```

#### F. JSON Output Schema Request
```
Based on this conversation, respond with ONLY valid JSON capturing the problem → solution arc:
{
  "problemStatement": "...",
  "summary": "...",
  "threadName": "...",
  "approaches": [...],
  "finalSolution": "...",
  "keyDecisions": [...],
  "actionItems": [...],
  "codeChangesSummary": "...",
  "intentEvolution": "...",
  "openIssues": [...]
}
```

## 3. Data Truncation & Optimization

### Token Management:
- **Key exchanges**: Full context (2000 chars response)
- **Other exchanges**: Reduced context (500 chars response)
- **Code blocks**: Removed, replaced with summaries
- **Reasoning**: Limited to 2 blocks, 100 chars each
- **Files**: Limited to 15, with "... and X more" indicator
- **Tasks**: Limited to 8 items

### Code Block Summarization:
Code blocks are summarized instead of included:
- Line count
- Language detection
- Filename extraction (from comments)
- Function/class name extraction
- Example: `[code: 25 lines, Python, src/auth.py, fn:authenticate]`

### Pivot Detection:
Detects conversation direction changes:
- Keywords: "actually", "wait", "instead", "different approach", "let's try"
- Pivot exchanges are marked and given full context

## 4. Incremental Processing

When `previous_summary` is provided:
- Only **new messages** are sent (after `lastProcessedIndex`)
- Previous summary is included for context
- LLM merges new analysis with previous
- Reduces token usage significantly

## 5. Actual LLM Call

The final prompt is sent to Azure OpenAI with:
- **Model**: `41-mini` (or `41-nano` for heavy tasks)
- **Temperature**: `0.3`
- **Max Tokens**: `4000`
- **System Message**: "You are a helpful assistant that analyzes coding conversations. Always respond with valid JSON only, no markdown formatting."

## 6. Data Volume Examples

### Small Conversation (5 exchanges):
- ~2,000-3,000 tokens
- All exchanges included
- Full context for each

### Medium Conversation (20 exchanges):
- ~4,000-6,000 tokens
- ~8-10 key exchanges selected
- Full context for key exchanges, reduced for others

### Large Conversation (50+ exchanges):
- ~6,000-8,000 tokens
- ~15-20 key exchanges selected
- Adaptive truncation based on importance

## 7. Data Privacy & Security

### What IS Sent:
- ✅ User prompts (full text)
- ✅ Assistant responses (truncated, code blocks removed)
- ✅ File paths (names only, not content)
- ✅ Task descriptions
- ✅ Reasoning blocks (truncated)
- ✅ Previous summary (if incremental)

### What is NOT Sent:
- ❌ Actual file contents
- ❌ Full code blocks (only summaries)
- ❌ Database queries/results
- ❌ Sensitive environment variables
- ❌ API keys or secrets

## 8. Optimization Strategies

1. **Incremental Processing**: Only new messages after checkpoint
2. **Key Exchange Selection**: Prioritizes first, last, pivots
3. **Code Block Summarization**: Removes large code blocks
4. **Response Truncation**: 2000 chars for key, 500 for others
5. **Reasoning Limitation**: Max 2 blocks, 100 chars each
6. **File/Task Limiting**: Caps at 15 files, 8 tasks

## 9. Example Prompt Structure

```
Analyze this AI coding assistant conversation and extract the PROBLEM → SOLUTION arc.

## CONVERSATION OVERVIEW
- Total exchanges: 12
- Duration: +45m
- Direction changes (pivots): 2
- Files touched: 5
- Tasks created: 3

## CONVERSATION FLOW (8 key exchanges shown)

### [+0m] Exchange #1 (KEY)
**User:** Fix the authentication bug in the login page
**AI Response:** I'll help you fix the authentication bug. Let me check the login code...
**Code Changes:** [code: 15 lines, Python, src/auth.py, fn:login]

### [+5m] 🔄 PIVOT Exchange #3
**User:** Actually, let's use OAuth instead
**AI Thinking:** User wants to pivot from bug fix to OAuth implementation | Need to update authentication strategy
**AI Response:** Good idea! OAuth is more secure. Let me implement that...
**Code Changes:** [code: 45 lines, Python, src/auth.py, fn:oauth_login], [code: 12 lines, Python, src/config.py]

...

## FILES MODIFIED (5 total)
src/auth.py
src/login.py
src/config.py
src/tests/test_auth.py
src/utils/oauth.py

## TASK STATUS (1/3 completed)
- [pending] Implement OAuth flow
- [pending] Update tests
- [completed] Review authentication code

---

Based on this conversation, respond with ONLY valid JSON...
```

## 10. Monitoring & Debugging

To see what's actually sent:
1. Check LangSmith traces (if enabled)
2. Check extension output channel: `[AUTO-CAPTURE] Including X prompts + Y responses...`
3. Check MCP server logs for agent execution
4. Review prompt in `ChatAnalysisAgent._build_prompt()` method
