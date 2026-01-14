"""
Sampling manager for handling LLM sampling requests from MCP servers during tool execution.

This manager coordinates between MCP servers requesting LLM sampling (via ctx.sample())
and the backend's LLM service. It provides a synchronization mechanism where tool execution
pauses until the LLM sampling completes.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class SamplingRequest:
    """Represents a pending sampling request awaiting LLM response."""
    sampling_id: str
    tool_call_id: str
    tool_name: str
    messages: List[Dict[str, Any]]
    system_prompt: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = 512
    model_preferences: Optional[List[str]] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_event_loop().create_future())
    
    async def wait_for_response(self, timeout: float = 300.0) -> Dict[str, Any]:
        """
        Wait for the LLM to respond to the sampling request.
        
        Args:
            timeout: Maximum time to wait in seconds (default 5 minutes)
            
        Returns:
            Dict with 'text' key containing the LLM response
            
        Raises:
            asyncio.TimeoutError: If timeout is reached
        """
        return await asyncio.wait_for(self.future, timeout=timeout)


class SamplingManager:
    """
    Manages sampling requests and responses.
    
    Provides synchronization between:
    - MCP servers requesting LLM sampling via ctx.sample()
    - Backend LLM service processing sampling requests
    """
    
    def __init__(self):
        """Initialize the sampling manager."""
        self._pending_requests: Dict[str, SamplingRequest] = {}
        self._lock = asyncio.Lock()
    
    def create_sampling_request(
        self,
        sampling_id: str,
        tool_call_id: str,
        tool_name: str,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = 512,
        model_preferences: Optional[List[str]] = None
    ) -> SamplingRequest:
        """
        Create a new sampling request.
        
        Args:
            sampling_id: Unique identifier for this sampling request
            tool_call_id: ID of the tool call that requested sampling
            tool_name: Name of the tool requesting sampling
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt for the LLM
            temperature: Optional temperature parameter
            max_tokens: Maximum tokens to generate (default: 512)
            model_preferences: Optional list of preferred model names
            
        Returns:
            SamplingRequest object that can be awaited for response
        """
        request = SamplingRequest(
            sampling_id=sampling_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            model_preferences=model_preferences
        )
        self._pending_requests[sampling_id] = request
        logger.info(
            f"Created sampling request: id={sampling_id}, "
            f"tool={tool_name}, messages={len(messages)}, "
            f"temperature={temperature}, max_tokens={max_tokens}"
        )
        return request
    
    def handle_sampling_response(
        self,
        sampling_id: str,
        text: str,
        error: Optional[str] = None
    ) -> bool:
        """
        Handle a sampling response from the LLM.
        
        Args:
            sampling_id: ID of the sampling request being responded to
            text: The generated text from the LLM
            error: Optional error message if sampling failed
            
        Returns:
            True if response was handled, False if request not found
        """
        request = self._pending_requests.get(sampling_id)
        if not request:
            logger.warning(f"Received response for unknown sampling request: {sampling_id}")
            return False
        
        response = {
            "text": text,
            "error": error
        }
        
        if not request.future.done():
            if error:
                request.future.set_exception(Exception(error))
                logger.error(
                    f"Sampling request failed: id={sampling_id}, error={error}"
                )
            else:
                request.future.set_result(response)
                logger.info(
                    f"Sampling response received: id={sampling_id}, "
                    f"text_length={len(text) if text else 0}"
                )
        else:
            logger.warning(
                f"Sampling response ignored (already resolved): {sampling_id}"
            )
        
        return True
    
    def cleanup_request(self, sampling_id: str) -> None:
        """
        Clean up a completed sampling request.
        
        Args:
            sampling_id: ID of the request to clean up
        """
        if sampling_id in self._pending_requests:
            del self._pending_requests[sampling_id]
            logger.debug(f"Cleaned up sampling request: {sampling_id}")
    
    def get_pending_request(self, sampling_id: str) -> Optional[SamplingRequest]:
        """
        Get a pending sampling request by ID.
        
        Args:
            sampling_id: ID of the request to retrieve
            
        Returns:
            SamplingRequest if found, None otherwise
        """
        return self._pending_requests.get(sampling_id)
    
    def get_all_pending_requests(self) -> Dict[str, SamplingRequest]:
        """
        Get all pending sampling requests.
        
        Returns:
            Dictionary mapping sampling IDs to requests
        """
        return dict(self._pending_requests)
    
    def cancel_all_requests(self) -> None:
        """Cancel all pending sampling requests."""
        for request in self._pending_requests.values():
            if not request.future.done():
                request.future.set_exception(
                    asyncio.CancelledError("Sampling cancelled")
                )
        self._pending_requests.clear()
        logger.info("Cancelled all pending sampling requests")


# Global singleton instance
_sampling_manager: Optional[SamplingManager] = None


def get_sampling_manager() -> SamplingManager:
    """
    Get the global sampling manager singleton.
    
    Returns:
        Global SamplingManager instance
    """
    global _sampling_manager
    if _sampling_manager is None:
        _sampling_manager = SamplingManager()
    return _sampling_manager
