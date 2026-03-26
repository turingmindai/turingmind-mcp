"""Chat Analysis Agent - Analyzes Cursor chat conversations."""

import json
import re
import time
from typing import Dict, List, Any, Optional
from datetime import datetime

from .base_agent import BaseAgent


class ChatAnalysisAgent(BaseAgent):
    """Agent for analyzing chat conversations with LangSmith tracing."""
    
    def _build_prompt(self, inputs: Dict[str, Any]) -> str:
        """
        Build enhancement prompt from chat data.
        
        Migrated from extension's buildEnhancementPrompt logic.
        """
        import logging
        logger = logging.getLogger(__name__)
        
        user_prompts = inputs.get("user_prompts", [])
        assistant_responses = inputs.get("assistant_responses", [])
        files_discussed = inputs.get("files_discussed", [])
        ai_todos = inputs.get("ai_todos", [])
        reasoning = inputs.get("reasoning")
        previous_summary = inputs.get("previous_summary")
        file_diffs = inputs.get("file_diffs", [])  # List of {path, diff, size}
        
        # NEW: Rolling context from recent sessions
        rolling_context = inputs.get("rolling_context", [])
        
        # NEW: Active tasks from task lifecycle tracking
        active_tasks = inputs.get("active_tasks", [])
        
        # Log what we received
        logger.info(f"Building prompt with: {len(user_prompts)} prompts, {len(assistant_responses)} responses, "
                   f"{len(files_discussed)} files, {len(ai_todos)} todos, {len(reasoning) if reasoning else 0} reasoning blocks, "
                   f"{len(file_diffs)} file diffs, {len(rolling_context)} rolling context sessions, "
                   f"{len(active_tasks)} active tasks")
        
        # Build conversation exchanges
        all_exchanges = self._build_conversation_exchanges(
            user_prompts, assistant_responses, reasoning
        )
        
        logger.info(f"Built {len(all_exchanges)} exchanges from {len(user_prompts)} prompts and {len(assistant_responses)} responses")
        
        # Include key exchanges: first, last, and pivots for better context
        exchanges_to_include = []
        if all_exchanges:
            # Always include first exchange (initial problem)
            if len(all_exchanges) > 0:
                exchanges_to_include.append(all_exchanges[0])
            
            # Include pivots (direction changes)
            for exchange in all_exchanges[1:-1] if len(all_exchanges) > 2 else []:
                if exchange.get("is_pivot", False) and exchange not in exchanges_to_include:
                    exchanges_to_include.append(exchange)
            
            # Always include last exchange (current state)
            if len(all_exchanges) > 1 and all_exchanges[-1] not in exchanges_to_include:
                exchanges_to_include.append(all_exchanges[-1])
            elif len(all_exchanges) == 1:
                # Only one exchange, already added
                pass
        
        # If we have too many, prioritize: first, last, then pivots
        if len(exchanges_to_include) > 5:
            exchanges_to_include = [exchanges_to_include[0]] + exchanges_to_include[-1:] + [
                e for e in exchanges_to_include[1:-1] if e.get("is_pivot", False)
            ][:3]
        
        logger.info(f"Including {len(exchanges_to_include)} key exchanges (first, pivots, last) out of {len(all_exchanges)} total")
        
        # Log summary of what will be included
        total_response_chars = sum(len(e.get("assistant_response", "")) for e in exchanges_to_include)
        total_reasoning_blocks = sum(len(e.get("reasoning", [])) for e in exchanges_to_include)
        total_code_summaries = sum(len(e.get("code_block_summaries", [])) for e in exchanges_to_include)
        logger.info(f"Prompt will include: {total_response_chars} response chars, {total_reasoning_blocks} reasoning blocks, "
                   f"{total_code_summaries} code summaries, {len(files_discussed)} files, {len(ai_todos)} todos, "
                   f"{len(file_diffs)} file diffs")
        
        # Count pivots
        pivot_count = sum(1 for e in all_exchanges if e.get("is_pivot", False))
        
        # Calculate duration
        duration = "0m"
        if all_exchanges:
            start_time = all_exchanges[0].get("timestamp", 0)
            end_time = all_exchanges[-1].get("timestamp", 0)
            if start_time > 0 and end_time > 0:
                duration_ms = end_time - start_time
                minutes = duration_ms // 60000
                hours = minutes // 60
                remaining_minutes = minutes % 60
                if hours > 0:
                    duration = f"+{hours}h {remaining_minutes}m"
                else:
                    duration = f"+{minutes}m"
        
        # Add previous summary context if available
        previous_context = ""
        if previous_summary:
            prev_summary_text = previous_summary.get("summary", "")
            prev_decisions = previous_summary.get("keyDecisions", [])
            prev_action_items = previous_summary.get("actionItems", [])
            
            if prev_summary_text or prev_decisions or prev_action_items:
                previous_context = "\n## PREVIOUS CONTEXT (Same Session)\n"
                if prev_summary_text:
                    previous_context += f"**Previous Summary:** {prev_summary_text[:300]}{'...' if len(prev_summary_text) > 300 else ''}\n"
                if prev_decisions:
                    previous_context += f"**Key Decisions:** {', '.join(prev_decisions[:3])}{'...' if len(prev_decisions) > 3 else ''}\n"
                if prev_action_items:
                    pending = [a.get("task", "") for a in prev_action_items if a.get("status") != "done"]
                    if pending:
                        previous_context += f"**Pending Items:** {', '.join(pending[:3])}{'...' if len(pending) > 3 else ''}\n"
                previous_context += "\n"
        
        # Add rolling context from recent sessions
        rolling_context_section = ""
        if rolling_context:
            rolling_context_section = "\n## RECENT SESSION CONTEXT (Other Sessions)\n"
            for i, session in enumerate(rolling_context[:3]):  # Limit to 3 sessions
                created_at = session.get("created_at", 0)
                # Format time ago
                if created_at > 0:
                    import time
                    time_ago_ms = int(time.time() * 1000) - created_at
                    hours_ago = time_ago_ms // (60 * 60 * 1000)
                    if hours_ago < 1:
                        time_ago = "< 1 hour ago"
                    elif hours_ago < 24:
                        time_ago = f"{hours_ago}h ago"
                    else:
                        days_ago = hours_ago // 24
                        time_ago = f"{days_ago}d ago"
                else:
                    time_ago = "unknown"
                
                summary = session.get("one_line_summary", "No summary")
                decisions = session.get("key_decisions", [])
                pending = session.get("pending_items", [])
                
                rolling_context_section += f"""
### Session from {time_ago}
- **Summary:** {summary[:150]}{'...' if len(summary) > 150 else ''}
"""
                if decisions:
                    rolling_context_section += f"- **Decisions:** {', '.join(decisions[:2])}{'...' if len(decisions) > 2 else ''}\n"
                if pending:
                    rolling_context_section += f"- **Pending:** {', '.join(pending[:2])}{'...' if len(pending) > 2 else ''}\n"
            rolling_context_section += "\n"
        
        # Add hierarchical context (repo → folder → file)
        hierarchical_context_section = ""
        hierarchical_context = inputs.get("hierarchical_context", {})
        if hierarchical_context:
            has_content = False
            
            # Repo-level context
            repo_ctx = hierarchical_context.get("repo")
            if repo_ctx:
                has_content = True
                hierarchical_context_section += "\n## PROJECT CONTEXT\n"
                hierarchical_context_section += f"**Project:** {repo_ctx.get('summary', '')[:150]}\n"
                facts = repo_ctx.get("key_facts", [])
                if facts:
                    hierarchical_context_section += f"**Key Facts:** {', '.join(facts[:3])}\n"
                patterns = repo_ctx.get("patterns", [])
                if patterns:
                    hierarchical_context_section += f"**Patterns:** {', '.join(patterns[:3])}\n"
            
            # Folder-level context
            folder_ctxs = hierarchical_context.get("folders", [])
            if folder_ctxs:
                has_content = True
                hierarchical_context_section += "\n## FOLDER CONTEXT\n"
                for fctx in folder_ctxs[:3]:  # Limit to 3 folders
                    path = fctx.get("scope_path", "")
                    summary = fctx.get("summary", "")
                    facts = fctx.get("key_facts", [])
                    hierarchical_context_section += f"**{path}:** {summary[:100]}"
                    if facts:
                        hierarchical_context_section += f" [{', '.join(facts[:2])}]"
                    hierarchical_context_section += "\n"
            
            # File-level context
            file_ctxs = hierarchical_context.get("files", [])
            if file_ctxs:
                has_content = True
                hierarchical_context_section += "\n## FILE CONTEXT\n"
                for fctx in file_ctxs[:5]:  # Limit to 5 files
                    path = fctx.get("scope_path", "")
                    summary = fctx.get("summary", "")
                    hierarchical_context_section += f"**{path}:** {summary[:150]}\n"
            
            if has_content:
                hierarchical_context_section += "\n"
        
        # Add active tasks for tracking
        active_tasks_section = ""
        if active_tasks:
            active_tasks_section = "\n## ACTIVE TASKS (Phase Tracking)\n"
            active_tasks_section += "These tasks are being tracked across sessions. Update their status based on this conversation:\n\n"
            
            # Group by phase
            tasks_by_phase = {}
            for task in active_tasks:
                phase = task.get("current_phase", "mentioned")
                if phase not in tasks_by_phase:
                    tasks_by_phase[phase] = []
                tasks_by_phase[phase].append(task)
            
            phase_icons = {
                "mentioned": "💡",
                "planned": "📋",
                "in_progress": "🔨",
                "implemented": "✏️",
                "tested": "🧪",
                "blocked": "🔴"
            }
            
            for phase in ["in_progress", "planned", "mentioned", "implemented", "tested", "blocked"]:
                if phase in tasks_by_phase:
                    icon = phase_icons.get(phase, "•")
                    active_tasks_section += f"**{icon} {phase.upper()}:**\n"
                    for task in tasks_by_phase[phase][:5]:  # Limit per phase
                        task_id = task.get("id", "")
                        description = task.get("description", "")
                        files = task.get("related_files", [])
                        files_str = f" ({', '.join(files[:2])})" if files else ""
                        active_tasks_section += f"- [{task_id}] {description[:100]}{files_str}\n"
                    active_tasks_section += "\n"
            
            active_tasks_section += """
For each active task, determine if this conversation provides evidence for a phase transition:
- mentioned → planned: User expressed intent to work on it
- planned → in_progress: Work has started (code written, files modified)
- in_progress → implemented: Core functionality complete
- implemented → tested: Tests added/passed
- Any → blocked: Explicit blocker mentioned
- Any → done: User confirmed completion

"""
        
        # Build prompt - CODE REVIEW PERSPECTIVE with actionable reminders
        prompt = f"""Analyze this AI coding assistant conversation like a CODE REVIEW. Extract:

1. **ACTIONABLE REMINDERS** - What the developer needs to remember/follow up on
2. **STRUCTURED KANBAN ITEMS** - Features, specs, and todos for project tracking
3. **TASK STATUS UPDATES** - Phase transitions for tracked tasks

Focus on what the developer needs to REMEMBER and FOLLOW UP ON, not just what happened.

{previous_context}{rolling_context_section}{hierarchical_context_section}{active_tasks_section}## CONVERSATION OVERVIEW
- Total exchanges: {len(all_exchanges)}
- Duration: {duration}
- Direction changes (pivots): {pivot_count}
- Files touched: {len(files_discussed)}
- Tasks created: {len(ai_todos)}

## KEY EXCHANGES ({len(exchanges_to_include)} of {len(all_exchanges)} total)
"""
        
        # Add only the latest exchange
        for idx, exchange in enumerate(exchanges_to_include):
            is_key = idx == 0 or idx == len(all_exchanges) - 1 or exchange.get("is_pivot", False)
            pivot_marker = " 🔄 PIVOT" if exchange.get("is_pivot", False) else ""
            time_marker = exchange.get("relative_time", "+0m")
            
            prompt += f"""
### [{time_marker}]{pivot_marker} Exchange #{exchange.get('sequence', 0) + 1}{' (KEY)' if is_key else ''}
**User:** {exchange.get('user_prompt', '')[:500]}{'...' if len(exchange.get('user_prompt', '')) > 500 else ''}
"""
            
            # Add reasoning if available (show all)
            if exchange.get("reasoning"):
                reasoning_list = exchange["reasoning"]
                if isinstance(reasoning_list, list) and len(reasoning_list) > 0:
                    reasoning_text = " | ".join([
                        str(r)[:200] for r in reasoning_list  # Show all reasoning blocks
                    ])
                    prompt += f"**AI Thinking:** {reasoning_text}\n"
            
            # Add response with code context (summarized, not removed)
            response = exchange.get("assistant_response", "")
            if response:
                # Extract code blocks for summary
                code_blocks = re.findall(r'```(\w+)?\n?([\s\S]*?)```', response)
                response_without_code = re.sub(r'```[\s\S]*?```', '', response)
                response_text = response_without_code.strip()
                
                if response_text:
                    # Include full response text (up to 3000 chars)
                    max_chars = 3000
                    prompt += f"**AI Response:** {response_text[:max_chars]}{'...' if len(response_text) > max_chars else ''}\n"
                
                # Add code block summaries (what code was generated)
                if code_blocks:
                    code_summaries_list = []
                    for lang, code in code_blocks[:5]:  # Limit to 5 code blocks
                        summary = self._summarize_code_block(f"```{lang or ''}\n{code}\n```")
                        code_summaries_list.append(summary)
                    if code_summaries_list:
                        prompt += f"**Code Generated:** {', '.join(code_summaries_list)}\n"
                elif not response_text:
                    # Response was only code blocks (already summarized above)
                    prompt += f"**AI Response:** [Code-only response]\n"
            else:
                prompt += f"**AI Response:** [No response]\n"
            
            # Add code block summaries (show all)
            code_summaries = exchange.get("code_block_summaries", [])
            if code_summaries:
                summaries_text = ', '.join([str(s) for s in code_summaries])
                prompt += f"**Code Changes:** {summaries_text}\n"
            else:
                # Check if response has code blocks but summaries weren't extracted
                response_text = exchange.get("assistant_response", "")
                if response_text and "```" in response_text:
                    code_block_count = len(re.findall(r'```[\s\S]*?```', response_text))
                    if code_block_count > 0:
                        prompt += f"**Code Changes:** [{code_block_count} code block(s)]\n"
        
        # Add files context (always show, even if empty)
        prompt += f"""
## FILES MODIFIED ({len(files_discussed)} total)
"""
        if files_discussed:
            prompt += f"{chr(10).join(files_discussed[:15])}{chr(10) + f'... and {len(files_discussed) - 15} more' if len(files_discussed) > 15 else ''}\n"
        else:
            prompt += "(No files modified)\n"
        
        # Add todos context (always show, even if empty)
        completed = sum(1 for t in ai_todos if t.get("status") in ["completed", "done"]) if ai_todos else 0
        prompt += f"""
## TASK STATUS ({completed}/{len(ai_todos)} completed)
"""
        if ai_todos:
            todo_lines = [f"- [{t.get('status', 'pending')}] {t.get('content', '')}" for t in ai_todos[:8]]
            prompt += f"{chr(10).join(todo_lines)}\n"
        else:
            prompt += "(No tasks created)\n"
        
        # Add file diffs (with token management) - always show section
        prompt += "\n## CODE CHANGES (Diffs)\n\n"
        if file_diffs:
            selected_diffs = self._select_important_diffs(file_diffs, files_discussed, max_tokens=2000)
            if selected_diffs:
                logger.info(f"Including {len(selected_diffs)} of {len(file_diffs)} file diffs in prompt")
                
                for diff_info in selected_diffs:
                    path = diff_info.get("path", "")
                    diff = diff_info.get("diff", "")
                    
                    # Truncate very large diffs
                    if len(diff) > 8000:  # ~2000 tokens max per file
                        diff = self._summarize_diff(diff, max_lines=100)
                    
                    prompt += f"### {path}\n```diff\n{diff}\n```\n\n"
                
                if len(selected_diffs) < len(file_diffs):
                    remaining = len(file_diffs) - len(selected_diffs)
                    prompt += f"... and {remaining} more file diffs (truncated due to token limit)\n\n"
            else:
                prompt += "(No diffs selected - all were filtered out)\n\n"
        else:
            prompt += "(No file diffs provided)\n\n"
        
        # Request structured output with actionable reminders and kanban items
        prompt += """
---

## EXTRACTION PRIORITIES:

### 1. INCOMPLETE WORK
Extract what was started but not finished:
- Code generated but not tested/committed
- Tasks mentioned but not completed
- "I'll do X later" statements
- Edge cases discussed but not handled

### 2. DECISIONS MADE
Extract choices and their rationale:
- Architecture/design decisions
- Technology choices
- Trade-offs discussed
- WHY decisions were made (for future reference)

### 3. FOLLOW-UPS NEEDED
Extract what should happen next:
- Testing that should be done
- Code review items
- Documentation to write
- Refactoring mentioned
- Error handling to add

### 4. CONTEXT FOR FUTURE
Extract what will help later:
- What problem was being solved
- What approach worked/didn't work
- Related files/features
- Patterns established

### 5. STRUCTURED KANBAN ITEMS
From the above, extract trackable items:

**FEATURES** - User-facing functionality being built
- Look for: "I want to add X", "Let's implement Y", "We need Z functionality"
- Each should be specific and standalone

**SPECS** - Requirements/design docs that need writing
- Look for: "We should document X", "The API should work like Y", design decisions
- Each should be something that needs to be written/tracked

**TODOS** - Action items that need doing
- Look for: "I'll do X later", "Need to add Y", "Should test Z", explicit todos
- Each should be specific and actionable

IMPORTANT for kanban items:
- Be specific and standalone (not vague like "fix bugs")
- Link to files/exchanges when possible
- Include priority (high/medium/low)
- Generate unique hash for deduplication (use: title + first file path)

Based on this conversation, respond with ONLY valid JSON:
{
  "problemStatement": "What specific problem was the user trying to solve?",
  "summary": "2-3 sentence summary of WHAT was accomplished and WHY",
  "threadName": "Short descriptive title (max 50 chars)",
  "approaches": [
    {"attempt": "First approach tried", "outcome": "worked|failed|partial", "reason": "Why it did/didn't work"}
  ],
  "finalSolution": "What ultimately solved the problem (if resolved)",
  "keyDecisions": [
    {
      "decision": "Decision text with reasoning",
      "reasoning": "Why this decision was made",
      "context": "Exchange #X or file reference"
    }
  ],
  "actionItems": [
    {
      "task": "Specific actionable task",
      "priority": "high|medium|low",
      "status": "pending|done",
      "type": "testing|documentation|refactoring|code_quality|feature",
      "file": "path/to/file.py",
      "exchange": 2
    }
  ],
  "codeChangesSummary": "Brief summary of code changes and their purpose",
  "intentEvolution": "Initial intent → [pivots if any] → final outcome",
  "openIssues": ["Any unresolved issues or questions"],
  "incompleteWork": [
    {
      "item": "What was started but not finished",
      "type": "code_quality|testing|documentation|feature",
      "mentionedAt": "exchange #X",
      "priority": "high|medium|low",
      "file": "path/to/file.py",
      "reason": "Why it's incomplete"
    }
  ],
  "decisions": [
    {
      "decision": "Decision text",
      "reasoning": "Why this decision was made",
      "context": "Exchange #X or file reference",
      "filesAffected": ["path/to/file.py"]
    }
  ],
  "followUps": [
    {
      "action": "What should happen next",
      "type": "testing|code_review|documentation|refactoring|error_handling",
      "priority": "high|medium|low",
      "reason": "Why this follow-up is needed",
      "file": "path/to/file.py",
      "exchange": 2
    }
  ],
  "contextForFuture": {
    "whatWorked": ["Approach/pattern that worked"],
    "whatDidntWork": ["Approach that didn't work"],
    "relatedFiles": ["path/to/file.py"],
    "patternsEstablished": ["Pattern or convention established"]
  },
  "kanbanItems": {
    "features": [
      {
        "title": "Feature name (specific and standalone)",
        "description": "What the feature does",
        "status": "in_progress|discussed|completed",
        "files": ["path/to/file.py"],
        "exchange": 2,
        "priority": "high|medium|low",
        "hash": "feature-title-firstfile-hash"
      }
    ],
    "specs": [
      {
        "title": "Spec name (what needs to be documented)",
        "description": "What the spec should cover",
        "type": "api_spec|design_doc|requirements",
        "files": ["docs/spec.md"],
        "exchange": 2,
        "priority": "high|medium|low",
        "hash": "spec-title-hash"
      }
    ],
    "todos": [
      {
        "title": "Todo title (specific and actionable)",
        "description": "What needs to be done",
        "type": "code_quality|testing|documentation|refactoring|feature",
        "files": ["path/to/file.py"],
        "exchange": 3,
        "priority": "high|medium|low",
        "status": "pending",
        "hash": "todo-title-file-hash"
      }
    ]
  },
  "taskUpdates": [
    {
      "taskId": "task_abc123 (from ACTIVE TASKS section)",
      "transition": {
        "from": "current_phase",
        "to": "new_phase (mentioned|planned|in_progress|implemented|tested|done|blocked|abandoned)"
      },
      "evidence": "What in the conversation triggered this transition",
      "confidence": 0.8
    }
  ],
  "newTasks": [
    {
      "description": "Specific task extracted from conversation",
      "initialPhase": "mentioned|planned|in_progress",
      "relatedFiles": ["path/to/file.py"],
      "priority": "high|medium|low",
      "confidence": 0.7,
      "extractedFrom": "exchange #X or quote"
    }
  ]
}"""
        
        return prompt
    
    def _build_conversation_exchanges(
        self,
        user_prompts: List[Dict],
        assistant_responses: List[Dict],
        reasoning: Optional[List[Dict]] = None
    ) -> List[Dict]:
        """Build conversation exchanges from prompts and responses.
        
        Handles Cursor's streaming model where multiple assistant responses
        can correspond to a single user prompt. Groups responses by
        matching them to the nearest user prompt by timestamp.
        """
        import logging
        logger = logging.getLogger(__name__)
        
        # Handle case with no user prompts (incremental update with only responses)
        if not user_prompts:
            logger.info("No user_prompts provided, creating exchanges from responses only")
            if not assistant_responses:
                return []
            
            # Create synthetic exchanges from responses
            exchanges = []
            start_time = assistant_responses[0].get("timestamp", 0) if assistant_responses else 0
            
            for i, response in enumerate(assistant_responses):
                response_text = response.get("text", "")
                if not response_text or not response_text.strip():
                    continue
                    
                # Calculate relative time
                relative_time = "+0m"
                resp_time = response.get("timestamp", 0)
                if start_time > 0 and resp_time > 0:
                    duration_ms = resp_time - start_time
                    minutes = duration_ms // 60000
                    hours = minutes // 60
                    remaining_minutes = minutes % 60
                    if hours > 0:
                        relative_time = f"+{hours}h {remaining_minutes}m"
                    else:
                        relative_time = f"+{minutes}m"
                
                exchanges.append({
                    "sequence": response.get("sequence", i),
                    "timestamp": resp_time,
                    "relative_time": relative_time,
                    "user_prompt": "(continuing previous conversation)",
                    "assistant_response": response_text,
                    "reasoning": [],
                    "is_pivot": False,
                    "code_block_summaries": []
                })
            
            logger.info(f"Created {len(exchanges)} exchanges from {len(assistant_responses)} responses (no user prompts)")
            return exchanges
        
        exchanges = []
        start_time = user_prompts[0].get("timestamp", 0) if user_prompts else 0
        
        import logging
        logger = logging.getLogger(__name__)
        logger.debug(f"Building exchanges: {len(user_prompts)} prompts, {len(assistant_responses)} responses")
        
        # Group responses by user prompt
        # Cursor can have multiple responses per prompt (streaming chunks)
        # Strategy: Match responses to prompts by sequence number first, then by timestamp
        response_idx = 0
        
        for i, prompt in enumerate(user_prompts):
            prompt_timestamp = prompt.get("timestamp", 0)
            prompt_sequence = prompt.get("sequence", i)
            
            # Collect all responses that belong to this prompt
            collected_responses = []
            
            # Find responses for this prompt
            # If there's a next prompt, responses belong to current if before next prompt
            # If this is the last prompt, all remaining responses belong to it
            next_prompt_timestamp = user_prompts[i + 1].get("timestamp", float('inf')) if i + 1 < len(user_prompts) else float('inf')
            
            # First, try to match by sequence number
            for j in range(response_idx, len(assistant_responses)):
                response = assistant_responses[j]
                resp_sequence = response.get("sequence", -1)
                
                if resp_sequence == prompt_sequence:
                    collected_responses.append(response)
                    if j == response_idx:
                        response_idx = j + 1
            # If no sequence match, match by timestamp range
            if not collected_responses:
                while response_idx < len(assistant_responses):
                    response = assistant_responses[response_idx]
                    resp_timestamp = response.get("timestamp", 0)
                    
                    # Response belongs to this prompt if it's after the prompt but before the next prompt
                    if resp_timestamp >= prompt_timestamp and resp_timestamp < next_prompt_timestamp:
                        collected_responses.append(response)
                        response_idx += 1
                    elif resp_timestamp >= next_prompt_timestamp:
                        # This response belongs to a future prompt
                        break
                    else:
                        # Response is before current prompt (shouldn't happen, but skip it)
                        response_idx += 1
            
            logger.debug(f"Prompt {i} (seq={prompt_sequence}): collected {len(collected_responses)} responses")
            
            # Merge multiple responses into one (for streaming chunks)
            if collected_responses:
                # Use the last (most complete) response, or merge all
                if len(collected_responses) == 1:
                    response = collected_responses[0]
                else:
                    # Merge responses - use the longest/fullest response (usually the last one)
                    # For streaming, later responses typically contain earlier content plus more
                    latest_response = max(collected_responses, key=lambda r: (r.get("timestamp", 0), len(r.get("text", ""))))
                    response_text = latest_response.get("text", "")
                    
                    # If latest is empty, try merging all non-empty responses
                    if not response_text or not response_text.strip():
                        response_text = " ".join([r.get("text", "") for r in collected_responses if r.get("text", "").strip()])
                    
                    response = {
                        "text": response_text,
                        "timestamp": latest_response.get("timestamp", 0),
                        "sequence": latest_response.get("sequence", prompt_sequence),
                        "hasReasoning": any(r.get("hasReasoning", False) for r in collected_responses)
                    }
                    
                    logger.debug(f"  Merged {len(collected_responses)} responses into one (length: {len(response_text)})")
            else:
                # No response found for this prompt
                response = {}
                logger.debug(f"  No responses found for prompt {i}")
            
            # Find reasoning for this exchange
            exchange_reasoning = []
            if reasoning:
                prompt_timestamp = prompt.get("timestamp", 0)
                for r in reasoning:
                    if (r.get("sequence") == prompt.get("sequence") or
                        abs(r.get("timestamp", 0) - prompt_timestamp) < 5000):
                        exchange_reasoning = r.get("reasoning", [])
                        break
            
            # Extract code block summaries
            response_text = response.get("text", "")
            code_blocks = re.findall(r'```[\s\S]*?```', response_text)
            code_block_summaries = [self._summarize_code_block(cb) for cb in code_blocks]
            
            # Detect pivot (simplified)
            is_pivot = False
            if i > 0:
                prev_prompt = user_prompts[i - 1].get("text", "").lower()
                curr_prompt = prompt.get("text", "").lower()
                pivot_phrases = ["actually", "wait", "instead", "different approach", "let's try"]
                is_pivot = any(phrase in curr_prompt for phrase in pivot_phrases)
            
            # Calculate relative time
            relative_time = "+0m"
            if start_time > 0:
                prompt_time = prompt.get("timestamp", 0)
                if prompt_time > 0:
                    duration_ms = prompt_time - start_time
                    minutes = duration_ms // 60000
                    hours = minutes // 60
                    remaining_minutes = minutes % 60
                    if hours > 0:
                        relative_time = f"+{hours}h {remaining_minutes}m"
                    else:
                        relative_time = f"+{minutes}m"
            
            exchange_data = {
                "sequence": prompt.get("sequence", i),
                "timestamp": prompt.get("timestamp", 0),
                "relative_time": relative_time,
                "user_prompt": prompt.get("text", ""),
                "assistant_response": response.get("text", ""),
                "reasoning": exchange_reasoning,
                "is_pivot": is_pivot,
                "code_block_summaries": code_block_summaries
            }
            
            # Log exchange details for debugging
            response_len = len(exchange_data["assistant_response"])
            logger.debug(f"Exchange {i}: user_prompt={len(exchange_data['user_prompt'])} chars, "
                        f"assistant_response={response_len} chars, reasoning={len(exchange_reasoning)} blocks, "
                        f"code_summaries={len(code_block_summaries)}")
            
            exchanges.append(exchange_data)
        
        return exchanges
    
    def _select_key_exchanges(
        self,
        exchanges: List[Dict],
        max_tokens: int = 4000
    ) -> List[Dict]:
        """Select key exchanges using adaptive token budgeting."""
        if len(exchanges) <= 3:
            return exchanges
        
        selected = []
        
        # Always include first
        if exchanges:
            selected.append(exchanges[0])
        
        # Always include last
        if len(exchanges) > 1 and exchanges[-1] not in selected:
            selected.append(exchanges[-1])
        
        # Include pivots
        for exchange in exchanges[1:-1]:
            if exchange.get("is_pivot", False) and exchange not in selected:
                selected.append(exchange)
        
        # Fill remaining with evenly spaced exchanges
        remaining = [e for e in exchanges if e not in selected]
        if remaining:
            step = max(1, len(remaining) // 3)
            for i in range(0, len(remaining), step):
                if remaining[i] not in selected:
                    selected.append(remaining[i])
        
        # Sort by sequence
        selected.sort(key=lambda e: e.get("sequence", 0))
        
        return selected
    
    def _summarize_code_block(self, code: str) -> str:
        """Summarize a code block."""
        lines = len([l for l in code.split('\n') if l.strip()])
        
        # Try to extract filename
        file_match = re.search(r'(?://|#|/\*)\s*(?:file:?\s*)?([^\s]+\.\w{1,4})', code, re.IGNORECASE)
        
        # Try to extract function/class names
        func_match = re.search(r'(?:function|def|async\s+function|const|let|var)\s+(\w+)\s*[=(]', code)
        class_match = re.search(r'(?:class|interface|type|struct)\s+(\w+)', code)
        
        parts = [f"{lines} lines"]
        if file_match:
            parts.append(file_match.group(1))
        if func_match:
            parts.append(f"fn:{func_match.group(1)}")
        elif class_match:
            parts.append(f"class:{class_match.group(1)}")
        
        return f"[code: {', '.join(parts)}]"
    
    def _select_important_diffs(
        self,
        file_diffs: List[Dict[str, Any]],
        files_discussed: List[str],
        max_tokens: int = 2000
    ) -> List[Dict[str, Any]]:
        """Select most important diffs within token budget."""
        if not file_diffs:
            return []
        
        # Score each diff by importance
        scored_diffs = []
        for diff_info in file_diffs:
            path = diff_info.get("path", "")
            diff = diff_info.get("diff", "")
            size = diff_info.get("size", len(diff))
            
            score = 0
            
            # Higher priority for files mentioned in conversation
            if path in files_discussed:
                score += 10
            
            # Higher priority for smaller diffs (more focused changes)
            if size < 1000:
                score += 5
            elif size < 5000:
                score += 2
            
            # Higher priority for code files
            code_extensions = ['.py', '.ts', '.js', '.tsx', '.jsx', '.java', '.go', '.rs', '.cpp', '.c']
            if any(path.endswith(ext) for ext in code_extensions):
                score += 3
            
            # Penalize very large diffs
            if size > 20000:
                score -= 5
            
            scored_diffs.append({
                **diff_info,
                "score": score
            })
        
        # Sort by score (highest first)
        scored_diffs.sort(key=lambda x: x["score"], reverse=True)
        
        # Select within token budget (rough estimate: 1 token ≈ 4 chars)
        selected = []
        total_tokens = 0
        
        for diff_info in scored_diffs:
            diff_tokens = diff_info.get("size", 0) // 4
            if total_tokens + diff_tokens <= max_tokens:
                selected.append(diff_info)
                total_tokens += diff_tokens
            else:
                # Try to fit a truncated version
                remaining_tokens = max_tokens - total_tokens
                if remaining_tokens > 100:  # Only if we have meaningful space
                    truncated_diff = diff_info.copy()
                    truncated_diff["diff"] = diff_info.get("diff", "")[:remaining_tokens * 4]
                    truncated_diff["size"] = len(truncated_diff["diff"])
                    selected.append(truncated_diff)
                break
        
        return selected
    
    def _summarize_diff(self, diff: str, max_lines: int = 100) -> str:
        """Summarize a diff by showing key changes."""
        lines = diff.split('\n')
        
        if len(lines) <= max_lines:
            return diff
        
        # Extract key sections: file headers, hunks, important changes
        summary_lines = []
        in_hunk = False
        hunk_count = 0
        max_hunks = 5  # Limit number of hunks
        
        for i, line in enumerate(lines):
            # Always include file headers
            if line.startswith('diff --git') or line.startswith('---') or line.startswith('+++'):
                summary_lines.append(line)
            elif line.startswith('@@'):
                if hunk_count < max_hunks:
                    summary_lines.append(line)
                    in_hunk = True
                    hunk_count += 1
                else:
                    break
            elif in_hunk and (line.startswith('+') or line.startswith('-')):
                # Include actual changes
                summary_lines.append(line)
                if len(summary_lines) >= max_lines - 5:
                    break
        
        if len(summary_lines) < len(lines):
            summary_lines.append(f"\n... ({len(lines) - len(summary_lines)} more lines truncated)")
        
        return '\n'.join(summary_lines)
    
    def _parse_response(self, response: str) -> Dict[str, Any]:
        """
        Parse enhancement response from LLM.
        
        Migrated from extension's parseEnhancementResponse logic.
        """
        # Extract JSON from response (handle markdown code blocks)
        json_str = response
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response)
        if json_match:
            json_str = json_match.group(1)
        
        # Also try to find JSON object directly
        object_match = re.search(r'\{[\s\S]*\}', json_str)
        if object_match:
            json_str = object_match[0]
        
        try:
            parsed = json.loads(json_str.strip())
            
            # Build ChatEnhancement structure with new fields
            result = {
                "summary": parsed.get("summary", "No summary available"),
                "threadName": (parsed.get("threadName") or "Untitled Session")[:60],
                "keyDecisions": parsed.get("keyDecisions", []),
                "actionItems": [
                    {
                        "task": item.get("task", ""),
                        "priority": item.get("priority", "medium") if item.get("priority") in ["high", "medium", "low"] else "medium",
                        "status": item.get("status", "pending") if item.get("status") in ["pending", "done"] else "pending",
                        "type": item.get("type", "feature"),
                        "file": item.get("file"),
                        "exchange": item.get("exchange")
                    }
                    for item in parsed.get("actionItems", [])
                ],
                "codeChangesSummary": parsed.get("codeChangesSummary"),
                "intentEvolution": parsed.get("intentEvolution"),
                "problemStatement": parsed.get("problemStatement"),
                "approaches": [
                    {
                        "attempt": a.get("attempt", ""),
                        "outcome": a.get("outcome", "partial") if a.get("outcome") in ["worked", "failed", "partial"] else "partial",
                        "reason": a.get("reason")
                    }
                    for a in parsed.get("approaches", [])
                ],
                "finalSolution": parsed.get("finalSolution"),
                "openIssues": parsed.get("openIssues", [])
            }
            
            # Add new actionable reminder fields
            if "incompleteWork" in parsed:
                result["incompleteWork"] = parsed.get("incompleteWork", [])
            
            if "decisions" in parsed:
                result["decisions"] = parsed.get("decisions", [])
            elif "keyDecisions" in parsed:
                # Convert old format to new format
                result["decisions"] = [
                    {
                        "decision": d if isinstance(d, str) else d.get("decision", str(d)),
                        "reasoning": d.get("reasoning", "") if isinstance(d, dict) else "",
                        "context": d.get("context", "") if isinstance(d, dict) else "",
                        "filesAffected": d.get("filesAffected", []) if isinstance(d, dict) else []
                    }
                    for d in parsed.get("keyDecisions", [])
                ]
            
            if "followUps" in parsed:
                result["followUps"] = parsed.get("followUps", [])
            
            if "contextForFuture" in parsed:
                result["contextForFuture"] = parsed.get("contextForFuture", {})
            
            # Add kanban items
            if "kanbanItems" in parsed:
                result["kanbanItems"] = parsed.get("kanbanItems", {
                    "features": [],
                    "specs": [],
                    "todos": []
                })
            else:
                # Initialize empty kanban items structure
                result["kanbanItems"] = {
                    "features": [],
                    "specs": [],
                    "todos": []
                }
            
            # Add task lifecycle updates (NEW)
            valid_phases = ["mentioned", "planned", "in_progress", "implemented", "tested", "done", "blocked", "abandoned"]
            
            if "taskUpdates" in parsed:
                result["taskUpdates"] = [
                    {
                        "taskId": update.get("taskId", ""),
                        "transition": {
                            "from": update.get("transition", {}).get("from", ""),
                            "to": update.get("transition", {}).get("to", "")
                        } if update.get("transition") else None,
                        "evidence": update.get("evidence", ""),
                        "confidence": min(1.0, max(0.0, float(update.get("confidence", 0.5))))
                    }
                    for update in parsed.get("taskUpdates", [])
                    if update.get("taskId") and update.get("transition", {}).get("to") in valid_phases
                ]
            else:
                result["taskUpdates"] = []
            
            if "newTasks" in parsed:
                result["newTasks"] = [
                    {
                        "description": task.get("description", ""),
                        "initialPhase": task.get("initialPhase", "mentioned") if task.get("initialPhase") in valid_phases else "mentioned",
                        "relatedFiles": task.get("relatedFiles", []),
                        "priority": task.get("priority", "medium") if task.get("priority") in ["high", "medium", "low"] else "medium",
                        "confidence": min(1.0, max(0.0, float(task.get("confidence", 0.5)))),
                        "extractedFrom": task.get("extractedFrom", "")
                    }
                    for task in parsed.get("newTasks", [])
                    if task.get("description")
                ]
            else:
                result["newTasks"] = []
            
            return result
        except (json.JSONDecodeError, Exception) as e:
            # Return minimal enhancement from raw response
            return {
                "summary": response[:500].replace('```', '').strip(),
                "threadName": "Chat Session",
                "keyDecisions": [],
                "actionItems": [],
                "codeChangesSummary": None,
                "intentEvolution": None,
                "problemStatement": None,
                "approaches": None,
                "finalSolution": None,
                "openIssues": None,
                "taskUpdates": [],
                "newTasks": []
            }
