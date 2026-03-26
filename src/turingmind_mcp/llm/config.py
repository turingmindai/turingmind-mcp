"""LLM configuration and provider factory."""

import os
from typing import Optional, Any

try:
    from langsmith import Client as LangSmithClient
except ImportError:
    LangSmithClient = None

from .azure_openai import AzureOpenAIProvider
from .provider import LLMProvider


def get_langsmith_client() -> Optional[Any]:
    """
    Initialize LangSmith client from environment variables.
    
    Returns:
        LangSmithClient instance if API key is available, None otherwise
    """
    if LangSmithClient is None:
        return None
    
    api_key = os.getenv("LANGSMITH_API_KEY")
    if not api_key:
        return None
    
    api_url = os.getenv("LANGSMITH_API_URL", "https://api.smith.langchain.com")
    
    try:
        return LangSmithClient(
            api_key=api_key,
            api_url=api_url
        )
    except Exception:
        return None


def get_llm_provider(provider_name: str = "azure") -> Optional[LLMProvider]:
    """
    Get LLM provider instance based on configuration.
    
    Args:
        provider_name: Provider name ("azure" or "openai")
        
    Returns:
        LLMProvider instance or None if not configured
    """
    if provider_name == "azure":
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        api_key = os.getenv("AZURE_OPENAI_KEY")
        deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
        use_responses = os.getenv("AZURE_OPENAI_USE_RESPONSES_ENDPOINT", "false").lower() == "true"
        heavy_task_deployment = os.getenv("AZURE_OPENAI_HEAVY_TASK_DEPLOYMENT_NAME")
        
        if not endpoint or not api_key or not deployment_name:
            return None
        
        return AzureOpenAIProvider(
            endpoint=endpoint,
            api_key=api_key,
            deployment_name=deployment_name,
            api_version=api_version,
            use_responses_endpoint=use_responses,
            heavy_task_deployment_name=heavy_task_deployment
        )
    
    # TODO: Add OpenAI provider when needed
    return None
