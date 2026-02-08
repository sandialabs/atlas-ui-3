"""Prompt override service - handles MCP system prompt injection."""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PromptOverrideService:
    """
    Service that handles MCP prompt override injection.

    Retrieves MCP-provided prompts and injects them as system messages,
    applying only the first valid prompt found.
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
    ) -> List[Dict[str, Any]]:
        """
        Apply MCP prompt override if selected prompts are provided.

        Only the first valid prompt is applied, prepended as a system message.

        Args:
            messages: Current message history
            selected_prompts: List of prompt keys (format: "server_promptname")

        Returns:
            Messages with prompt override prepended (if applicable)
        """
        if not selected_prompts or not self.tool_manager:
            return messages

        try:
            # Iterate in order; when found, fetch prompt content and inject
            for key in selected_prompts:
                if not isinstance(key, str) or "_" not in key:
                    continue

                server, prompt_name = key.split("_", 1)

                # Retrieve prompt from MCP
                try:
                    prompt_obj = await self.tool_manager.get_prompt(server, prompt_name)
                    prompt_text = self._extract_prompt_text(prompt_obj)

                    if prompt_text:
                        # Prepend as system message override
                        messages = [{"role": "system", "content": prompt_text}] + messages
                        logger.info(
                            "Applied MCP system prompt override (len=%d)",
                            len(prompt_text),
                        )
                        break  # apply only one

                except Exception:
                    logger.debug("Failed retrieving MCP prompt %s", key, exc_info=True)

        except Exception:
            logger.debug(
                "Prompt override injection skipped due to non-fatal error",
                exc_info=True
            )

        return messages

    def _extract_prompt_text(self, prompt_obj: Any) -> Optional[str]:
        """
        Extract text content from various MCP prompt object formats.

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
                first = content_field[0]
                if hasattr(first, "text") and isinstance(first.text, str):
                    return first.text

        # Fallback: string dump
        return str(prompt_obj)
