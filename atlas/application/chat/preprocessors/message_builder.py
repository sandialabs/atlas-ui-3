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


def _build_vision_user_message(content: str, image_files: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build a multimodal user message with inline image content blocks.

    Uses the OpenAI image_url format (data URI), which LiteLLM translates
    to the correct provider-specific format for each backend.

    Args:
        content: Text content of the message
        image_files: List of dicts with keys "image_b64" and "image_mime_type"

    Returns:
        Message dict with a content list containing text and image blocks
    """
    content_blocks: List[Dict[str, Any]] = [{"type": "text", "text": content}]
    for img in image_files:
        b64 = img.get("image_b64", "")
        mime = img.get("image_mime_type", "image/png")
        content_blocks.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime};base64,{b64}",
            },
        })
    return {"role": "user", "content": content_blocks}


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
        model_supports_vision: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Build messages array from session history and context.

        When *model_supports_vision* is True, any image files stored in the
        session context (via ``handle_session_files``) are attached as inline
        image content blocks on the last user message instead of being listed
        in the plain-text files manifest.

        Args:
            session: Current chat session
            include_files_manifest: Whether to append files manifest for non-image files
            include_system_prompt: Whether to prepend system prompt
            model_supports_vision: Whether the selected model supports vision input

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

        # When the model supports vision, find image files in the session context
        # and attach them as inline content blocks on the last user message.
        if model_supports_vision:
            session_context = build_session_context(session)
            files_ctx = session_context.get("files", {})
            image_files = [
                info for info in files_ctx.values()
                if info.get("image_b64") and info.get("image_mime_type")
            ]
            if image_files:
                # Replace the last user message with a multimodal version
                last_user_idx = None
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i].get("role") == "user":
                        last_user_idx = i
                        break
                if last_user_idx is not None:
                    original = messages[last_user_idx]
                    text_content = original.get("content") or ""
                    # Only convert if the content is still a plain string
                    if isinstance(text_content, str):
                        messages[last_user_idx] = _build_vision_user_message(
                            text_content, image_files
                        )
                        logger.debug(
                            "Attached %d image(s) to last user message for vision model",
                            len(image_files),
                        )

        # Optionally add files manifest (non-image files, or all files when not vision)
        if include_files_manifest:
            session_context = build_session_context(session)
            files_in_context = session_context.get("files", {})
            logger.debug(f"Session has {len(files_in_context)} files: {list(files_in_context.keys())}")
            files_manifest = file_processor.build_files_manifest(
                session_context, exclude_vision_images=model_supports_vision
            )
            if files_manifest:
                logger.debug(f"Adding files manifest to messages: {files_manifest['content'][:100]}")
                messages.append(files_manifest)

        return messages
