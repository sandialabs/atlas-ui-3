"""Prompt override service - handles MCP system prompt injection."""

import logging
from typing import Any, Dict, List, Optional, Tuple

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
            prompt_key_parts = self._split_prompt_key(key)
            if not prompt_key_parts:
                continue

            server, prompt_name = prompt_key_parts

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

    def _split_prompt_key(self, key: Any) -> Optional[Tuple[str, str]]:
        """Split a stored prompt key into server and prompt names."""
        if not isinstance(key, str) or "_" not in key:
            return None

        known_server_names = set()
        for attr in ("available_prompts", "servers_config"):
            value = getattr(self.tool_manager, attr, None)
            if isinstance(value, dict):
                known_server_names.update(
                    server for server in value.keys() if isinstance(server, str)
                )

        for server in sorted(known_server_names, key=len, reverse=True):
            prefix = f"{server}_"
            if key.startswith(prefix) and len(key) > len(prefix):
                return server, key[len(prefix):]

        return tuple(key.split("_", 1))

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
