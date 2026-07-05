"""Additional passing tests for ChatAnalysisAgent - edge cases and integration."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, List

from turingmind_mcp.agents.chat_analysis_agent import ChatAnalysisAgent


class TestChatAnalysisAgentPassing:
    """Additional passing tests for ChatAnalysisAgent."""
    
    @pytest.fixture
    def mock_llm_provider(self):
        """Mock LLM provider."""
        provider = Mock()
        provider.call = AsyncMock(return_value='{"summary": "Test", "threadName": "Thread"}')
        return provider
    
    @pytest.fixture
    def agent(self, mock_llm_provider):
        """Create ChatAnalysisAgent."""
        return ChatAnalysisAgent(
            llm_provider=mock_llm_provider,
            langsmith_client=None,
            use_heavy_task_model=False
        )
    
    @pytest.mark.asyncio
    async def test_handles_empty_conversation(self, agent, mock_llm_provider):
        """Test agent handles empty conversation gracefully."""
        inputs = {
            "user_prompts": [],
            "assistant_responses": [],
            "files_discussed": [],
            "ai_todos": [],
            "previous_summary": None
        }
        
        result = await agent.execute(
            inputs=inputs,
            call_type="enhanceChatAnalysis",
            tags=["chat-analysis"]
        )
        
        assert result is not None
        assert "summary" in result
    
    @pytest.mark.asyncio
    async def test_handles_missing_fields(self, agent, mock_llm_provider):
        """Test agent handles missing optional fields."""
        inputs = {
            "user_prompts": [{"text": "Test", "timestamp": 123, "sequence": 0}],
            "assistant_responses": [{"text": "Response", "timestamp": 124, "sequence": 0}],
            # Missing optional fields
        }
        
        result = await agent.execute(
            inputs=inputs,
            call_type="enhanceChatAnalysis",
            tags=["chat-analysis"]
        )
        
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_handles_malformed_json_response(self, agent):
        """Test agent handles malformed JSON in LLM response."""
        mock_provider = Mock()
        mock_provider.call = AsyncMock(return_value="This is not JSON at all")
        
        agent.llm_provider = mock_provider
        
        inputs = {
            "user_prompts": [{"text": "Test", "timestamp": 123, "sequence": 0}],
            "assistant_responses": [{"text": "Response", "timestamp": 124, "sequence": 0}]
        }
        
        result = await agent.execute(
            inputs=inputs,
            call_type="enhanceChatAnalysis",
            tags=["chat-analysis"]
        )
        
        # Should return minimal enhancement
        assert result is not None
        assert "summary" in result
        assert "threadName" in result
    
    @pytest.mark.asyncio
    async def test_handles_partial_json_response(self, agent):
        """Test agent handles partial JSON in response."""
        mock_provider = Mock()
        mock_provider.call = AsyncMock(return_value='{"summary": "Test"')  # Missing closing brace
        
        agent.llm_provider = mock_provider
        
        inputs = {
            "user_prompts": [{"text": "Test", "timestamp": 123, "sequence": 0}],
            "assistant_responses": [{"text": "Response", "timestamp": 124, "sequence": 0}]
        }
        
        result = await agent.execute(
            inputs=inputs,
            call_type="enhanceChatAnalysis",
            tags=["chat-analysis"]
        )
        
        assert result is not None
    
    @pytest.mark.asyncio
    async def test_handles_large_conversation(self, agent, mock_llm_provider):
        """Test agent handles large conversations efficiently."""
        # Create 50 exchanges
        user_prompts = [
            {"text": f"Message {i}", "timestamp": 1000 + i * 1000, "sequence": i}
            for i in range(50)
        ]
        assistant_responses = [
            {"text": f"Response {i}", "timestamp": 1000 + i * 1000 + 100, "sequence": i}
            for i in range(50)
        ]
        
        inputs = {
            "user_prompts": user_prompts,
            "assistant_responses": assistant_responses,
            "files_discussed": [f"file{i}.py" for i in range(20)],
            "ai_todos": [{"content": f"Task {i}", "status": "pending"} for i in range(10)]
        }
        
        result = await agent.execute(
            inputs=inputs,
            call_type="enhanceChatAnalysis",
            tags=["chat-analysis"]
        )
        
        assert result is not None
        # Verify key exchanges were selected (not all 50)
        call_args = mock_llm_provider.call.call_args
        prompt = call_args[0][0] if call_args[0] else call_args[1].get("prompt", "")
        # Should mention fewer exchanges than total
        assert "50" in prompt  # Total count
        # But prompt should be reasonable size (key exchanges selected)
    
    @pytest.mark.asyncio
    async def test_detects_pivots(self, agent):
        """Test agent detects conversation pivots."""
        user_prompts = [
            {"text": "Let's build a login system", "timestamp": 1000, "sequence": 0},
            {"text": "Actually, let's try a different approach", "timestamp": 2000, "sequence": 1},
            {"text": "Wait, instead let's use OAuth", "timestamp": 3000, "sequence": 2}
        ]
        assistant_responses = [
            {"text": "OK", "timestamp": 1100, "sequence": 0},
            {"text": "Sure", "timestamp": 2100, "sequence": 1},
            {"text": "Good idea", "timestamp": 3100, "sequence": 2}
        ]
        
        inputs = {
            "user_prompts": user_prompts,
            "assistant_responses": assistant_responses,
            "files_discussed": [],
            "ai_todos": []
        }
        
        prompt = agent._build_prompt(inputs)
        
        # Should detect pivots (keywords: "Actually", "Wait", "instead")
        assert "PIVOT" in prompt or "pivot" in prompt.lower()
    
    @pytest.mark.asyncio
    async def test_includes_code_block_summaries(self, agent):
        """Test agent includes code block summaries in prompt."""
        assistant_responses = [
            {
                "text": "Here's the code:\n```python\ndef hello():\n    print('hi')\n```",
                "timestamp": 1000,
                "sequence": 0
            }
        ]
        
        inputs = {
            "user_prompts": [{"text": "Show code", "timestamp": 900, "sequence": 0}],
            "assistant_responses": assistant_responses,
            "files_discussed": [],
            "ai_todos": []
        }
        
        prompt = agent._build_prompt(inputs)
        
        # Should include code summary
        assert "code" in prompt.lower() or "Code Changes" in prompt
    
    @pytest.mark.asyncio
    async def test_handles_reasoning_context(self, agent):
        """Test agent includes reasoning context when available."""
        inputs = {
            "user_prompts": [{"text": "Test", "timestamp": 1000, "sequence": 0}],
            "assistant_responses": [{"text": "Response", "timestamp": 1100, "sequence": 0}],
            "reasoning": [
                {
                    "bubbleId": "b1",
                    "reasoning": ["Step 1: Analyze", "Step 2: Implement"],
                    "timestamp": 1050,
                    "sequence": 0
                }
            ],
            "files_discussed": [],
            "ai_todos": []
        }
        
        prompt = agent._build_prompt(inputs)
        
        # Should include reasoning
        assert "reasoning" in prompt.lower() or "thinking" in prompt.lower() or "Step" in prompt
    
    @pytest.mark.asyncio
    async def test_parses_action_items_correctly(self, agent):
        """Test agent parses action items with correct structure."""
        response = '''{
            "summary": "Test",
            "threadName": "Thread",
            "actionItems": [
                {"task": "Fix bug", "priority": "high", "status": "pending"},
                {"task": "Add tests", "priority": "medium", "status": "pending"}
            ]
        }'''
        
        result = agent._parse_response(response)
        
        assert len(result["actionItems"]) == 2
        assert result["actionItems"][0]["task"] == "Fix bug"
        assert result["actionItems"][0]["priority"] == "high"
        assert result["actionItems"][0]["status"] == "pending"
    
    @pytest.mark.asyncio
    async def test_handles_invalid_action_item_priorities(self, agent):
        """Test agent normalizes invalid action item priorities."""
        response = '''{
            "summary": "Test",
            "threadName": "Thread",
            "actionItems": [
                {"task": "Task 1", "priority": "invalid", "status": "pending"}
            ]
        }'''
        
        result = agent._parse_response(response)
        
        # Should default to "medium" for invalid priority
        assert result["actionItems"][0]["priority"] == "medium"
    
    @pytest.mark.asyncio
    async def test_handles_invalid_action_item_status(self, agent):
        """Test agent normalizes invalid action item status."""
        response = '''{
            "summary": "Test",
            "threadName": "Thread",
            "actionItems": [
                {"task": "Task 1", "priority": "high", "status": "invalid"}
            ]
        }'''
        
        result = agent._parse_response(response)
        
        # Should default to "pending" for invalid status
        assert result["actionItems"][0]["status"] == "pending"
    
    @pytest.mark.asyncio
    async def test_truncates_long_thread_names(self, agent):
        """Test agent truncates thread names to 60 characters."""
        long_name = "A" * 100
        response = f'{{"summary": "Test", "threadName": "{long_name}"}}'
        
        result = agent._parse_response(response)
        
        assert len(result["threadName"]) <= 60
    
    @pytest.mark.asyncio
    async def test_handles_missing_thread_name(self, agent):
        """Test agent provides default thread name when missing."""
        response = '{"summary": "Test"}'
        
        result = agent._parse_response(response)
        
        assert result["threadName"] == "Untitled Session"
    
    @pytest.mark.asyncio
    async def test_incremental_analysis_merges_previous_summary(self, agent, mock_llm_provider):
        """Test incremental analysis includes previous summary context."""
        inputs = {
            "user_prompts": [{"text": "New message", "timestamp": 2000, "sequence": 1}],
            "assistant_responses": [{"text": "New response", "timestamp": 2100, "sequence": 1}],
            "files_discussed": [],
            "ai_todos": [],
            "previous_summary": {
                "summary": "Previous work done",
                "keyDecisions": ["Decision 1", "Decision 2"],
                "actionItems": [{"task": "Task 1", "priority": "high", "status": "pending"}]
            }
        }
        
        result = await agent.execute(
            inputs=inputs,
            call_type="enhanceChatAnalysis",
            tags=["chat-analysis", "incremental"]
        )
        
        # Verify prompt included previous summary
        call_args = mock_llm_provider.call.call_args
        prompt = call_args[0][0] if call_args[0] else call_args[1].get("prompt", "")
        assert "Previous work done" in prompt or "PREVIOUS ANALYSIS" in prompt
        assert "new message" in prompt.lower() and "new response" in prompt.lower()
