"""Tests for ChatAnalysisAgent - should fail until implementation."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, List

# These imports should fail until we implement ChatAnalysisAgent
# conftest.py adds src to path
from turingmind_mcp.agents.chat_analysis_agent import ChatAnalysisAgent


class TestChatAnalysisAgent:
    """Test ChatAnalysisAgent - TDD: These tests should fail first."""
    
    @pytest.fixture
    def mock_llm_provider(self):
        """Mock LLM provider."""
        provider = Mock()
        provider.call = AsyncMock(return_value='{"summary": "Test summary", "threadName": "Test Thread", "keyDecisions": [], "actionItems": []}')
        return provider
    
    @pytest.fixture
    def agent(self, mock_llm_provider):
        """Create ChatAnalysisAgent - should fail until implemented."""
        return ChatAnalysisAgent(
            llm_provider=mock_llm_provider,
            langsmith_client=None,
            use_heavy_task_model=False
        )
    
    @pytest.fixture
    def sample_chat_data(self):
        """Sample chat data for testing."""
        return {
            "user_prompts": [
                {"text": "Fix the authentication bug", "timestamp": 1234567890, "sequence": 0}
            ],
            "assistant_responses": [
                {"text": "I'll help you fix the authentication bug...", "timestamp": 1234567900, "sequence": 0}
            ],
            "files_discussed": ["src/auth.py"],
            "ai_todos": [],
            "reasoning": None,
            "previous_summary": None
        }
    
    @pytest.mark.asyncio
    async def test_agent_initialization(self, agent):
        """Test ChatAnalysisAgent can be initialized."""
        assert agent is not None
    
    @pytest.mark.asyncio
    async def test_enhance_chat_analysis_full(self, agent, sample_chat_data, mock_llm_provider):
        """Test full chat analysis (not incremental)."""
        result = await agent.execute(
            inputs=sample_chat_data,
            call_type="enhanceChatAnalysis",
            tags=["chat-analysis", "enhancement"],
            extra_metadata={"is_incremental": False}
        )
        
        assert result is not None
        assert "summary" in result
        assert "threadName" in result
        assert "keyDecisions" in result
        assert "actionItems" in result
        assert mock_llm_provider.call.called
    
    @pytest.mark.asyncio
    async def test_enhance_chat_analysis_incremental(self, agent, mock_llm_provider):
        """Test incremental chat analysis."""
        inputs = {
            "user_prompts": [
                {"text": "New message", "timestamp": 1234568000, "sequence": 1}
            ],
            "assistant_responses": [
                {"text": "New response", "timestamp": 1234568100, "sequence": 1}
            ],
            "files_discussed": [],
            "ai_todos": [],
            "previous_summary": {
                "summary": "Previous summary",
                "keyDecisions": ["Decision 1"],
                "actionItems": [{"task": "Task 1", "priority": "high", "status": "pending"}]
            }
        }
        
        result = await agent.execute(
            inputs=inputs,
            call_type="enhanceChatAnalysis",
            tags=["chat-analysis", "enhancement"],
            extra_metadata={"is_incremental": True}
        )
        
        assert result is not None
        # Verify prompt includes previous summary
        call_args = mock_llm_provider.call.call_args
        prompt = call_args[0][0] if call_args[0] else call_args[1].get("prompt", "")
        assert "Previous summary" in prompt or "PREVIOUS ANALYSIS" in prompt
    
    @pytest.mark.asyncio
    async def test_parse_enhancement_response(self, agent):
        """Test parsing enhancement response."""
        response = '{"summary": "Test", "threadName": "Thread", "keyDecisions": ["D1"], "actionItems": [{"task": "T1", "priority": "high", "status": "pending"}]}'
        result = agent._parse_response(response)
        
        assert result["summary"] == "Test"
        assert result["threadName"] == "Thread"
        assert len(result["keyDecisions"]) == 1
        assert len(result["actionItems"]) == 1
    
    @pytest.mark.asyncio
    async def test_parse_enhancement_response_with_markdown(self, agent):
        """Test parsing response wrapped in markdown code blocks."""
        response = '```json\n{"summary": "Test", "threadName": "Thread"}\n```'
        result = agent._parse_response(response)
        
        assert result["summary"] == "Test"
        assert result["threadName"] == "Thread"
    
    @pytest.mark.asyncio
    async def test_build_prompt_includes_exchanges(self, agent, sample_chat_data):
        """Test prompt includes conversation exchanges."""
        prompt = agent._build_prompt(sample_chat_data)
        
        assert "Fix the authentication bug" in prompt
        assert "I'll help you fix" in prompt or "authentication bug" in prompt
    
    @pytest.mark.asyncio
    async def test_build_prompt_includes_files(self, agent, sample_chat_data):
        """Test prompt includes files discussed."""
        prompt = agent._build_prompt(sample_chat_data)
        
        assert "src/auth.py" in prompt or "files" in prompt.lower()
    
    @pytest.mark.asyncio
    async def test_build_prompt_incremental_mode(self, agent):
        """Test prompt for incremental mode includes previous summary."""
        inputs = {
            "user_prompts": [{"text": "New", "timestamp": 1, "sequence": 0}],
            "assistant_responses": [{"text": "Response", "timestamp": 2, "sequence": 0}],
            "files_discussed": [],
            "ai_todos": [],
            "previous_summary": {
                "summary": "Previous",
                "keyDecisions": ["D1"],
                "actionItems": []
            }
        }
        
        prompt = agent._build_prompt(inputs)
        
        assert "Previous" in prompt or "PREVIOUS ANALYSIS" in prompt
        assert "NEW MESSAGES" in prompt or "new messages" in prompt.lower()
