"""Azure OpenAI LLM provider implementation."""

import os
from typing import Optional
import httpx
from .provider import LLMProvider


class AzureOpenAIProvider(LLMProvider):
    """Azure OpenAI provider implementation."""
    
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        deployment_name: str,
        api_version: str = "2024-02-15-preview",
        use_responses_endpoint: bool = False,
        heavy_task_deployment_name: Optional[str] = None
    ):
        """
        Initialize Azure OpenAI provider.
        
        Args:
            endpoint: Azure OpenAI endpoint URL
            api_key: Azure OpenAI API key
            deployment_name: Primary deployment name
            api_version: API version
            use_responses_endpoint: Use new responses endpoint
            heavy_task_deployment_name: Deployment name for heavy tasks (cheaper model)
        """
        self.endpoint = endpoint.rstrip('/')
        self.api_key = api_key
        self.deployment_name = deployment_name
        self.api_version = api_version
        self.use_responses_endpoint = use_responses_endpoint
        self.heavy_task_deployment_name = heavy_task_deployment_name
    
    async def call(
        self,
        prompt: str,
        use_heavy_task_model: bool = False,
        temperature: float = 0.3,
        max_tokens: int = 4000
    ) -> str:
        """
        Call Azure OpenAI API.
        
        Args:
            prompt: The prompt to send
            use_heavy_task_model: Use cheaper deployment if configured
            temperature: Temperature for generation
            max_tokens: Maximum tokens to generate
            
        Returns:
            LLM response content
            
        Raises:
            Exception: If API call fails
        """
        # Select deployment
        actual_deployment = (
            self.heavy_task_deployment_name
            if use_heavy_task_model and self.heavy_task_deployment_name
            else self.deployment_name
        )
        
        # Build URL
        if self.use_responses_endpoint:
            url = f"{self.endpoint}/openai/responses?api-version={self.api_version}"
            request_body = {
                "model": actual_deployment,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a helpful assistant that analyzes coding conversations. Always respond with valid JSON only, no markdown formatting."
                    },
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": max_tokens,
                "temperature": temperature
            }
        else:
            url = f"{self.endpoint}/openai/deployments/{actual_deployment}/chat/completions?api-version={self.api_version}"
            request_body = {
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a helpful assistant that analyzes coding conversations. Always respond with valid JSON only, no markdown formatting."
                    },
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": max_tokens,
                "temperature": temperature
            }
        
        # Make API call
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "api-key": self.api_key
                },
                json=request_body,
                timeout=60.0
            )
            
            if not response.is_success:
                error_text = response.text
                raise Exception(f"Azure OpenAI API error: {response.status_code} - {error_text}")
            
            data = response.json()
            
            # Handle different response formats
            if self.use_responses_endpoint:
                if data.get("choices") and data["choices"][0].get("message"):
                    return data["choices"][0]["message"]["content"]
                elif data.get("content"):
                    return data["content"]
                elif isinstance(data, str):
                    return data
                else:
                    # Fallback: return JSON string
                    import json
                    return json.dumps(data)
            else:
                # Traditional chat/completions format
                return data["choices"][0]["message"]["content"]
