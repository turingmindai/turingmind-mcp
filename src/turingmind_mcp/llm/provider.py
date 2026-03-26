"""Abstract LLM provider interface."""

from abc import ABC, abstractmethod
from typing import Optional


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""
    
    @abstractmethod
    async def call(
        self,
        prompt: str,
        use_heavy_task_model: bool = False,
        temperature: float = 0.3,
        max_tokens: int = 4000
    ) -> str:
        """
        Call LLM with prompt.
        
        Args:
            prompt: The prompt to send
            use_heavy_task_model: If True, use cheaper model for heavy tasks
            temperature: Temperature for generation
            max_tokens: Maximum tokens to generate
            
        Returns:
            LLM response as string
        """
        raise NotImplementedError
