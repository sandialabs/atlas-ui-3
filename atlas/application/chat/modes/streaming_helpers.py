"""Shared streaming helpers for mode runners.

Provides the common iterate-accumulate-publish pattern used by
PlainModeRunner, RagModeRunner, and ToolsModeRunner when streaming
LLM responses token-by-token.
"""

import logging
from typing import AsyncGenerator

from atlas.interfaces.events import EventPublisher

from ..utilities.error_handler import classify_llm_error

logger = logging.getLogger(__name__)


async def stream_and_accumulate(
    token_generator: AsyncGenerator[str, None],
    event_publisher: EventPublisher,
    fallback_fn=None,
    context_label: str = "LLM",
) -> str:
    """Consume a token async generator, publishing each chunk and accumulating the result.

    Args:
        token_generator: Async generator yielding string tokens.
        event_publisher: Publisher for token_stream events.
        fallback_fn: Optional async callable returning str if the stream yields nothing.
        context_label: Label for log messages (e.g. "plain", "RAG").

    Returns:
        The accumulated response text.
    """
    accumulated = ""
    is_first = True

    try:
        token_count = 0
        async for token in token_generator:
            token_count += 1
            if token_count <= 2:
                logger.debug(
                    "%s stream token #%d: %r (len=%d)",
                    context_label, token_count, token[:50] if token else "", len(token) if token else 0,
                )
            await event_publisher.publish_token_stream(
                token=token, is_first=is_first, is_last=False,
            )
            accumulated += token
            is_first = False

        if not accumulated and fallback_fn:
            logger.info(
                "%s stream yielded %d tokens but no content, using fallback",
                context_label, token_count,
            )
            accumulated = await fallback_fn()
            await event_publisher.publish_chat_response(
                message=accumulated, has_pending_tools=False,
            )
        elif accumulated:
            await event_publisher.publish_token_stream(
                token="", is_first=False, is_last=True,
            )
    except Exception as exc:
        logger.error("%s streaming error, sending partial content: %s", context_label, exc)
        await event_publisher.publish_token_stream(
            token="", is_first=False, is_last=True,
        )
        if not accumulated:
            if fallback_fn:
                try:
                    accumulated = await fallback_fn()
                except Exception:
                    _err_class, user_msg, _log_msg = classify_llm_error(exc)
                    accumulated = user_msg
            else:
                _err_class, user_msg, _log_msg = classify_llm_error(exc)
                accumulated = user_msg
            await event_publisher.publish_chat_response(
                message=accumulated, has_pending_tools=False,
            )

    return accumulated
