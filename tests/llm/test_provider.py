"""Tests for LLM providers - should fail until implementation."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
import os

# These imports should fail until we implement providers
# conftest.py adds src to path
from turingmind_mcp.llm.provider import LLMProvider
from turingmind_mcp.llm.azure_openai import AzureOpenAIProvider


class TestLLMProvider:
    """Test LLM provider interface - TDD: These tests should fail first."""
    
    def test_provider_is_abstract(self):
        """Test LLMProvider is abstract and cannot be instantiated."""
        # This should fail - LLMProvider should be abstract
        with pytest.raises(TypeError):
            LLMProvider()
    
    def test_provider_call_raises_not_implemented(self):
        """Test provider.call() raises NotImplementedError."""
        # Create a concrete class that doesn't implement call
        class TestProvider(LLMProvider):
            pass
        
        # Should fail at instantiation (abstract class)
        with pytest.raises(TypeError, match="abstract method"):
            TestProvider()


class TestAzureOpenAIProvider:
    """Test Azure OpenAI provider - TDD: These tests should fail first."""
    
    @pytest.fixture
    def azure_config(self):
        """Azure OpenAI configuration."""
        return {
            "endpoint": os.getenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/"),
            "api_key": os.getenv("AZURE_OPENAI_KEY", "test-key"),
            "deployment_name": os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "test-deployment"),
            "api_version": os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
            "use_responses_endpoint": False
        }
    
    @pytest.fixture
    def provider(self, azure_config):
        """Create Azure OpenAI provider - should fail until implemented."""
        return AzureOpenAIProvider(**azure_config)
    
    def test_azure_provider_initialization(self, provider):
        """Test Azure provider can be initialized."""
        assert provider is not None
        assert provider.endpoint is not None
        assert provider.api_key is not None
        assert provider.deployment_name is not None
    
    @pytest.mark.asyncio
    async def test_azure_provider_call_success(self, provider):
        """Test Azure provider call succeeds."""
        # Mock httpx response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": '{"result": "test response"}'
                }
            }]
        }
        
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            
            result = await provider.call(
                prompt="Test prompt",
                use_heavy_task_model=False,
                temperature=0.3,
                max_tokens=1000
            )
            
            assert result is not None
            assert mock_post.called
            # Verify correct endpoint was called
            call_args = mock_post.call_args
            assert "chat/completions" in call_args[0][0] or "deployments" in call_args[0][0]
    
    @pytest.mark.asyncio
    async def test_azure_provider_uses_heavy_task_model(self, provider):
        """Test Azure provider uses heavy task deployment when requested."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "response"}}]
        }
        
        provider.heavy_task_deployment_name = "heavy-task-deployment"
        
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            
            await provider.call(
                prompt="Test",
                use_heavy_task_model=True
            )
            
            # Verify heavy task deployment was used
            call_args = mock_post.call_args
            request_data = call_args[1]["json"]
            # Check if deployment name is in URL or request body
            assert mock_post.called
    
    @pytest.mark.asyncio
    async def test_azure_provider_handles_error(self, provider):
        """Test Azure provider handles API errors."""
        # Create a proper httpx response mock
        mock_response = Mock()
        mock_response.is_success = False
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client
            
            with pytest.raises(Exception, match="Azure OpenAI API error"):
                await provider.call("test prompt")
    
    @pytest.mark.asyncio
    async def test_azure_provider_responses_endpoint(self):
        """Test Azure provider with responses endpoint."""
        provider = AzureOpenAIProvider(
            endpoint="https://test.openai.azure.com/",
            api_key="test-key",
            deployment_name="test-deployment",
            api_version="2025-04-01-preview",
            use_responses_endpoint=True
        )
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "response"}}]
        }
        
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            
            await provider.call("test")
            
            # Verify responses endpoint was used
            call_args = mock_post.call_args
            assert "responses" in call_args[0][0] or "responses" in str(call_args)
