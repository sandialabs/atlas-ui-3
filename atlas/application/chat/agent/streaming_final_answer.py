"""Shared helper for streaming the final answer in agent loops."""

import logging

from atlas.application.chat.utilities.error_handler import classify_llm_error
from atlas.interfaces.events import EventPublisher
from atlas.interfaces.llm import LLMProtocol

logger = logging.getLogger(__name__)


async def stream_final_answer(
    llm: LLMProtocol,
    event_publisher: EventPublisher,
    model: str,
    messages: list,
    temperature: float,
    user_email: str | None,
) -> str:
    """Stream the final answer token-by-token via event_publisher.

    Used by all three agent loops (act, react, think-act) when the loop
    exhausts its steps without a definitive final answer.

    Returns the accumulated response text.
    """
    accumulated = ""
    is_first = True
    try:
        async for token in llm.stream_plain(
            model, messages, temperature=temperature, user_email=user_email,
        ):
            await event_publisher.publish_token_stream(
                token=token, is_first=is_first, is_last=False,
            )
            accumulated += token
            is_first = False

        if accumulated:
            await event_publisher.publish_token_stream(
                token="", is_first=False, is_last=True,
            )
        else:
            accumulated = await llm.call_plain(
                model, messages, temperature=temperature, user_email=user_email,
            )
    except Exception as exc:
        logger.exception("Error streaming final answer")
        await event_publisher.publish_token_stream(
            token="", is_first=False, is_last=True,
        )
        if not accumulated:
            try:
                accumulated = await llm.call_plain(
                    model, messages, temperature=temperature, user_email=user_email,
                )
            except Exception:
                _err_class, user_msg, _log_msg = classify_llm_error(exc)
                accumulated = user_msg
    return accumulated
