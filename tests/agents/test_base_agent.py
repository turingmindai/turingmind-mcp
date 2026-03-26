"""Tests for BaseAgent - should fail until implementation."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, List, Optional
from datetime import datetime

# These imports should fail until we implement BaseAgent
# conftest.py adds src to path
from turingmind_mcp.agents.base_agent import BaseAgent

try:
    from langsmith import Client as LangSmithClient
except ImportError:
    LangSmithClient = None


class TestBaseAgent:
    """Test BaseAgent functionality - TDD: These tests should fail first."""
    
    @pytest.fixture
    def mock_langsmith_client(self):
        """Mock LangSmith client."""
        client = Mock(spec=LangSmithClient)
        client.create_run = Mock(return_value=Mock(id="test-run-id"))
        client.update_run = AsyncMock()
        return client
    
    @pytest.fixture
    def base_agent(self, mock_langsmith_client):
        """Create BaseAgent instance - should fail until implemented."""
        # Create a concrete test agent class
        class TestAgent(BaseAgent):
            def _build_prompt(self, inputs):
                return "test prompt"
            
            def _parse_response(self, response):
                return {"result": response}
        
        return TestAgent(
            llm_provider=Mock(),  # Mock LLM provider
            langsmith_client=mock_langsmith_client,
            use_heavy_task_model=False
        )
    
    def test_base_agent_initialization(self, base_agent):
        """Test BaseAgent can be initialized."""
        assert base_agent is not None
        assert base_agent.llm_provider is not None  # Should be Mock or provider instance
        assert base_agent.langsmith_client is not None
        assert base_agent.use_heavy_task_model is False
    
    def test_base_agent_initialization_with_heavy_task(self):
        """Test BaseAgent with heavy task model."""
        class TestAgent(BaseAgent):
            def _build_prompt(self, inputs):
                return "test"
            def _parse_response(self, response):
                return {}
        
        agent = TestAgent(
            llm_provider=Mock(),
            langsmith_client=None,
            use_heavy_task_model=True
        )
        assert agent.use_heavy_task_model is True
    
    @pytest.mark.asyncio
    async def test_execute_creates_langsmith_trace(self, base_agent, mock_langsmith_client):
        """Test execute() creates LangSmith trace."""
        # Mock LLM provider call
        base_agent.llm_provider.call = AsyncMock(return_value='{"result": "test"}')
        
        inputs = {"test": "data"}
        result = await base_agent.execute(
            inputs=inputs,
            call_type="test_call",
            tags=["test"],
            extra_metadata={"key": "value"}
        )
        
        # Verify LangSmith trace was created
        assert mock_langsmith_client.create_run.called
        call_args = mock_langsmith_client.create_run.call_args
        assert call_args[1]["name"] == "test_call"
        assert call_args[1]["run_type"] == "llm"
        assert "test" in call_args[1]["extra"]["tags"]
    
    @pytest.mark.asyncio
    async def test_execute_updates_trace_on_success(self, base_agent, mock_langsmith_client):
        """Test execute() updates trace on success."""
        base_agent.llm_provider.call = AsyncMock(return_value='{"result": "success"}')
        base_agent._parse_response = lambda r: {"result": "success"}
        
        await base_agent.execute(
            inputs={"test": "data"},
            call_type="test_call",
            tags=["test"]
        )
        
        # Verify trace was updated with success
        assert mock_langsmith_client.update_run.called
        call_args = mock_langsmith_client.update_run.call_args
        assert "outputs" in call_args[1]
        assert call_args[1]["outputs"]["result"]["result"] == "success"
    
    @pytest.mark.asyncio
    async def test_execute_updates_trace_on_error(self, base_agent, mock_langsmith_client):
        """Test execute() updates trace on error."""
        base_agent.llm_provider.call = AsyncMock(side_effect=Exception("Test error"))
        
        with pytest.raises(Exception, match="Test error"):
            await base_agent.execute(
                inputs={"test": "data"},
                call_type="test_call",
                tags=["test"]
            )
        
        # Verify trace was updated with error
        assert mock_langsmith_client.update_run.called
        call_args = mock_langsmith_client.update_run.call_args
        assert "error" in call_args[1]
        assert "Test error" in call_args[1]["error"]
    
    @pytest.mark.asyncio
    async def test_execute_without_langsmith(self):
        """Test execute() works without LangSmith client."""
        class TestAgent(BaseAgent):
            def _build_prompt(self, inputs):
                return "test prompt"
            def _parse_response(self, response):
                return {"result": response}
        
        mock_provider = Mock()
        mock_provider.call = AsyncMock(return_value='{"result": "test"}')
        
        agent = TestAgent(
            llm_provider=mock_provider,
            langsmith_client=None,
            use_heavy_task_model=False
        )
        
        result = await agent.execute(
            inputs={"test": "data"},
            call_type="test_call",
            tags=["test"]
        )
        
        assert result == {"result": '{"result": "test"}'}
        assert mock_provider.call.called
    
    def test_build_prompt_raises_not_implemented(self):
        """Test _build_prompt raises NotImplementedError on abstract class."""
        # Create agent without implementing abstract methods
        class IncompleteAgent(BaseAgent):
            pass
        
        with pytest.raises(TypeError):
            IncompleteAgent(llm_provider=Mock(), langsmith_client=None)
    
    def test_parse_response_raises_not_implemented(self):
        """Test _parse_response raises NotImplementedError on abstract class."""
        # Test that BaseAgent itself raises NotImplementedError
        # We can't instantiate BaseAgent directly, so test via a subclass that doesn't implement it
        class IncompleteAgent(BaseAgent):
            def _build_prompt(self, inputs):
                return "test"
            # _parse_response intentionally not implemented - will raise when called
        
        # This should fail at instantiation time (abstract class)
        with pytest.raises(TypeError, match="abstract method"):
            IncompleteAgent(llm_provider=Mock(), langsmith_client=None)
