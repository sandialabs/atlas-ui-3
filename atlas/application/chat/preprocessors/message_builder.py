"""Message builder - constructs messages with history and files manifest."""

import logging
from typing import Any, Dict, List, Optional

from atlas.domain.sessions.models import Session
from atlas.modules.prompts.prompt_provider import PromptProvider

from ..utilities import file_processor

logger = logging.getLogger(__name__)


def build_session_context(session: Session) -> Dict[str, Any]:
    """
    Build session context dictionary from session.

    Args:
        session: Chat session

    Returns:
        Session context dictionary
    """
    return {
        "session_id": session.id,
        "user_email": session.user_email,
        "files": session.context.get("files", {}),
        **session.context
    }


class MessageBuilder:
    """
    Service that builds complete message arrays for LLM calls.

    Combines conversation history with files manifest and system prompt.
    """

    def __init__(self, prompt_provider: Optional[PromptProvider] = None):
        """
        Initialize message builder.

        Args:
            prompt_provider: Optional prompt provider for loading system prompt
        """
        self.prompt_provider = prompt_provider

    async def build_messages(
        self,
        session: Session,
        include_files_manifest: bool = True,
        include_system_prompt: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Build messages array from session history and context.

        Args:
            session: Current chat session
            include_files_manifest: Whether to append files manifest
            include_system_prompt: Whether to prepend system prompt

        Returns:
            List of messages ready for LLM call
        """
        messages = []

        # Optionally add system prompt at the beginning
        if include_system_prompt and self.prompt_provider:
            system_prompt = self.prompt_provider.get_system_prompt(
                user_email=session.user_email
            )
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
                logger.debug(f"Added system prompt (len={len(system_prompt)})")

        # Get conversation history from session
        history_messages = session.history.get_messages_for_llm()
        messages.extend(history_messages)

        # Optionally add files manifest
        if include_files_manifest:
            session_context = build_session_context(session)
            files_in_context = session_context.get("files", {})
            logger.debug(f"Session has {len(files_in_context)} files: {list(files_in_context.keys())}")
            files_manifest = file_processor.build_files_manifest(session_context)
            if files_manifest:
                logger.debug(f"Adding files manifest to messages: {files_manifest['content'][:100]}")
                messages.append(files_manifest)
            else:
                logger.warning("No files manifest generated despite include_files_manifest=True")

        return messages
