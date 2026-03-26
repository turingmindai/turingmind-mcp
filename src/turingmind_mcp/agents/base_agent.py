"""Base agent class with LangSmith tracing."""

import os
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

try:
    from langsmith import Client as LangSmithClient
except ImportError:
    LangSmithClient = None


class BaseAgent(ABC):
    """Base class for all agents with LangSmith tracing."""
    
    def __init__(
        self,
        llm_provider: Any,  # LLMProvider instance or string
        langsmith_client: Optional[Any] = None,
        use_heavy_task_model: bool = False
    ):
        """
        Initialize base agent.
        
        Args:
            llm_provider: LLM provider instance or provider name string
            langsmith_client: LangSmith client for tracing (optional)
            use_heavy_task_model: Use cheaper model for heavy tasks
        """
        self.llm_provider = llm_provider
        self.langsmith_client = langsmith_client
        self.use_heavy_task_model = use_heavy_task_model
        self._llm_client = None
        
        # Initialize LLM client if provider is a string
        if isinstance(llm_provider, str):
            self._init_llm_client(llm_provider)
    
    def _init_llm_client(self, provider_name: str):
        """Initialize LLM client based on provider name."""
        # This will be implemented when we have provider factory
        pass
    
    async def execute(
        self,
        inputs: Dict[str, Any],
        call_type: str,
        tags: List[str],
        extra_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute agent with LangSmith tracing.
        
        Args:
            inputs: Input data for the agent
            call_type: Type of call (e.g., "enhanceChatAnalysis")
            tags: Tags for LangSmith tracing
            extra_metadata: Additional metadata for tracing
            
        Returns:
            Agent execution result
            
        Raises:
            Exception: If execution fails
        """
        run_id = None
        
        # Build prompt first so we can include it in LangSmith trace
        prompt = self._build_prompt(inputs)
        
        # Start LangSmith trace with actual prompt
        if self.langsmith_client:
            run_id = self._start_trace(inputs, call_type, tags, extra_metadata, prompt=prompt)
        
        try:
            
            # Call LLM
            if isinstance(self.llm_provider, str):
                # If provider is a string, we need to get the actual provider instance
                # For now, raise error - this should be handled by provider factory
                raise ValueError(f"LLM provider must be an instance, not string: {self.llm_provider}")
            
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Calling LLM provider: {type(self.llm_provider).__name__}, prompt length: {len(prompt)}")
            
            response = await self.llm_provider.call(
                prompt=prompt,
                use_heavy_task_model=self.use_heavy_task_model,
                temperature=0.3,
                max_tokens=4000
            )
            
            logger.info(f"LLM response received, length: {len(response) if isinstance(response, str) else 'N/A'}")
            
            # Parse response
            result = self._parse_response(response)
            
            logger.info(f"Parsed LLM response: threadName={result.get('threadName', 'N/A')}, actionItems={len(result.get('actionItems', []))}")
            
            # End trace (success) - fire and forget (non-blocking)
            if run_id and self.langsmith_client:
                try:
                    self._end_trace(run_id, inputs, result)
                except Exception:
                    pass  # Don't block on trace updates
            
            return result
            
        except Exception as e:
            # End trace (error) - fire and forget (non-blocking)
            if run_id and self.langsmith_client:
                try:
                    self._end_trace_error(run_id, inputs, e)
                except Exception:
                    pass  # Don't block on trace updates
            raise
    
    def _start_trace(
        self,
        inputs: Dict[str, Any],
        call_type: str,
        tags: List[str],
        extra_metadata: Optional[Dict[str, Any]] = None,
        prompt: Optional[str] = None
    ) -> Optional[str]:
        """Start LangSmith trace."""
        if not self.langsmith_client:
            return None
        
        run_id = str(uuid.uuid4())
        project_name = os.getenv("LANGSMITH_PROJECT", "turingmind-mcp")
        
        try:
            # Use actual prompt if available, otherwise fall back to inputs preview
            if prompt:
                prompt_preview = prompt[:10000] if len(prompt) > 10000 else prompt
                inputs_dict = {
                    "prompt": prompt_preview,
                    "prompt_length": len(prompt),
                    "inputs_summary": {
                        "user_prompts_count": len(inputs.get("user_prompts", [])),
                        "assistant_responses_count": len(inputs.get("assistant_responses", [])),
                        "files_discussed_count": len(inputs.get("files_discussed", [])),
                        "ai_todos_count": len(inputs.get("ai_todos", [])),
                        "file_diffs_count": len(inputs.get("file_diffs", []))
                    }
                }
            else:
                inputs_dict = {"prompt_preview": str(inputs)[:1000]}
            
            result = self.langsmith_client.create_run(
                id=run_id,
                name=call_type,
                run_type="llm",
                inputs=inputs_dict,
                extra={
                    **(extra_metadata or {}),
                    "tags": tags,
                    "provider": str(self.llm_provider)
                },
                project_name=project_name,
                start_time=datetime.now(timezone.utc)
            )
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"LangSmith trace created: run_id={run_id}, project={project_name}, call_type={call_type}, result={result}")
            return run_id
        except Exception as e:
            # Log LangSmith failures instead of silently failing
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"LangSmith trace creation failed: {e}", exc_info=True)
            return None
    
    def _end_trace(
        self,
        run_id: str,
        inputs: Dict[str, Any],
        result: Dict[str, Any]
    ):
        """End LangSmith trace with success."""
        if not self.langsmith_client or not run_id:
            return
        
        try:
            # LangSmith update_run is typically sync, but handle both
            update_result = self.langsmith_client.update_run(
                run_id,
                outputs={"result": result},
                end_time=datetime.now(timezone.utc)
            )
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"LangSmith trace updated successfully: run_id={run_id}, result={update_result}")
        except Exception as e:
            # Log LangSmith failures instead of silently failing
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"LangSmith trace update failed: {e}", exc_info=True)
    
    def _end_trace_error(
        self,
        run_id: str,
        inputs: Dict[str, Any],
        error: Exception
    ):
        """End LangSmith trace with error."""
        if not self.langsmith_client or not run_id:
            return
        
        try:
            # LangSmith update_run is typically sync, but handle both
            self.langsmith_client.update_run(
                run_id,
                error=str(error),
                end_time=datetime.now(timezone.utc)
            )
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"LangSmith trace updated with error: run_id={run_id}")
        except Exception as e:
            # Log LangSmith failures instead of silently failing
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"LangSmith trace error update failed: {e}")
    
    @abstractmethod
    def _build_prompt(self, inputs: Dict[str, Any]) -> str:
        """Build prompt from inputs. Must be implemented by subclasses."""
        raise NotImplementedError
    
    @abstractmethod
    def _parse_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM response. Must be implemented by subclasses."""
        raise NotImplementedError
