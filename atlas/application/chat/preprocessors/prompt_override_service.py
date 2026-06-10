"""Prompt override service - handles MCP system prompt injection."""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PromptOverrideService:
    """
    Service that handles MCP prompt override injection.

    Retrieves MCP-provided prompts and injects them as system messages,
    applying all selected prompts in order.
    """

    def __init__(self, tool_manager: Optional[Any] = None):
        """
        Initialize the prompt override service.

        Args:
            tool_manager: Optional tool manager with prompt retrieval capability
        """
        self.tool_manager = tool_manager

    async def apply_prompt_override(
        self,
        messages: List[Dict[str, Any]],
        selected_prompts: Optional[List[str]] = None,
        *,
        user_email: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Apply MCP prompt overrides for all selected prompts.

        All valid prompts are applied in selection order, each as a
        separate system message prepended to the conversation.

        Args:
            messages: Current message history
            selected_prompts: List of prompt keys (format: "server_promptname")
            user_email: User's email for per-user HTTP session isolation and meta context
            conversation_id: Optional conversation ID for meta context

        Returns:
            Messages with prompt overrides prepended (if applicable)
        """
        if not selected_prompts or not self.tool_manager:
            return messages

        system_messages: List[Dict[str, Any]] = []

        for key in selected_prompts:
            if not isinstance(key, str) or "_" not in key:
                continue

            server, prompt_name = key.split("_", 1)

            try:
                meta = {}
                if user_email:
                    meta["user_email"] = user_email
                if conversation_id:
                    meta["conversation_id"] = conversation_id

                prompt_obj = await self.tool_manager.get_prompt(
                    server,
                    prompt_name,
                    meta=meta if meta else None,
                    user_email=user_email,
                    conversation_id=conversation_id,
                )
                prompt_text = self._extract_prompt_text(prompt_obj)

                if prompt_text:
                    system_messages.append({"role": "system", "content": prompt_text})
                    logger.info(
                        "Applied MCP prompt '%s' (len=%d)", key, len(prompt_text)
                    )

            except Exception:
                logger.debug("Failed retrieving MCP prompt %s", key, exc_info=True)

        if system_messages:
            messages = system_messages + messages

        return messages

    def _extract_prompt_text(self, prompt_obj: Any) -> Optional[str]:
        """
        Extract text content, concatenating all text content items.

        Args:
            prompt_obj: Prompt object from MCP (could be string or structured object)

        Returns:
            Extracted prompt text, or None if extraction failed
        """
        # Simple string case
        if isinstance(prompt_obj, str):
            return prompt_obj

        # FastMCP PromptMessage-like: may have 'content' list with text entries
        if hasattr(prompt_obj, "content"):
            content_field = getattr(prompt_obj, "content")

            # content could be list of objects with 'text'
            if isinstance(content_field, list) and content_field:
                texts = []
                for item in content_field:
                    if hasattr(item, "text") and isinstance(item.text, str):
                        texts.append(item.text)
                if texts:
                    return "\n".join(texts)

        # Fallback: string dump
        return str(prompt_obj)
