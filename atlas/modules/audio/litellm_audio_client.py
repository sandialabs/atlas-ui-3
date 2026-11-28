"""Speech through the LiteLLM library, in process (issue #1255; provider ``litellm``).

Calls ``litellm.atranscription()`` and ``litellm.aspeech()`` directly -- no
LiteLLM proxy server. LiteLLM picks the upstream from the model prefix, so
one adapter covers OpenAI (``openai/...``), ElevenLabs (``elevenlabs/...``),
Groq (``groq/...``), Deepgram (``deepgram/...``), Mistral, Azure, Vertex AI
(``vertex_ai/...``) and the other audio providers LiteLLM supports.

* ``AUDIO_*_BASE_URL`` is optional and passed as ``api_base`` when set.
* ``AUDIO_*_API_KEY`` is passed as ``api_key`` when set; otherwise LiteLLM
  reads the provider's own environment variable (``OPENAI_API_KEY``,
  ``ELEVENLABS_API_KEY``, ``GROQ_API_KEY``, ...).

Guarantees kept from the direct adapters:

* the configured timeout is a ceiling on the whole call, and LiteLLM's own
  retries are off so it cannot stretch past it;
* LiteLLM's logging callbacks are switched off for these calls
  (``no-log``), so no configured callback receives the audio or text;
* a failure is reported by category and upstream status only; LiteLLM's
  exception text (which can quote the provider's error body) is neither
  logged nor forwarded;
* the reply size cap applies. LiteLLM buffers a reply before returning it, so
  the speech cap is checked after the reply arrives rather than while it is
  read; the timeout still bounds the exchange.

Redirect handling and connection pooling are LiteLLM's (it uses its own HTTP
clients), which is the one way this adapter differs from the direct ones.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Dict, Optional, Tuple

from atlas.modules.audio import audio_http
from atlas.modules.audio.audio_config import (
    TTS_FORMAT_MEDIA_TYPES,
    AudioCapabilityConfig,
    SttConfig,
    TtsConfig,
)

logger = logging.getLogger(__name__)


def _litellm():
    # Imported on first use: LiteLLM is a heavy import and the default
    # provider never needs it here.
    import litellm

    return litellm


def _common_kwargs(config: AudioCapabilityConfig) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "model": config.model,
        "timeout": config.timeout_seconds,
        "max_retries": 0,
        "no-log": True,
    }
    if config.api_key:
        kwargs["api_key"] = config.api_key
    if config.base_url:
        kwargs["api_base"] = config.base_url
    return kwargs


def _error_for_exception(exc: Exception) -> audio_http.AudioUpstreamError:
    if isinstance(exc, asyncio.TimeoutError):
        return audio_http.timed_out()
    exceptions = getattr(sys.modules.get("litellm"), "exceptions", None)
    if exceptions is not None:
        # Looked up, not imported: when the import itself is what failed,
        # mapping that failure must not raise again.
        if isinstance(exc, getattr(exceptions, "Timeout", ())):
            return audio_http.timed_out()
        if isinstance(exc, getattr(exceptions, "APIConnectionError", ())):
            return audio_http.unreachable()
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 400 <= status < 600:
        return audio_http.error_for_status(status)
    return audio_http.AudioUpstreamError(502, "The speech service returned an error.", "upstream_error")


async def _call(config: AudioCapabilityConfig, function_name: str, **kwargs: Any) -> Any:
    try:
        # Inside the mapping: a missing or broken LiteLLM install is a 502
        # like any other upstream failure, not an unhandled 500.
        function = getattr(_litellm(), function_name)
        return await asyncio.wait_for(function(**_common_kwargs(config), **kwargs), timeout=config.timeout_seconds)
    except audio_http.AudioUpstreamError:
        raise
    except Exception as exc:  # noqa: BLE001 -- every LiteLLM failure is mapped, none escapes with its text
        error = _error_for_exception(exc)
        # Only the type: the message can carry the provider's error body,
        # which can repeat the text or transcript.
        logger.warning("Audio %s request through LiteLLM failed: %s", config.name, type(exc).__name__)
        raise error from None


async def transcribe(
    config: SttConfig,
    *,
    content: bytes,
    filename: str,
    content_type: str,
    language: Optional[str] = None,
) -> str:
    extra: Dict[str, Any] = {"file": (filename, content, content_type)}
    if language:
        extra["language"] = language
    response = await _call(config, "atranscription", **extra)
    text = getattr(response, "text", None)
    if text is None and isinstance(response, dict):
        text = response.get("text")
    if not isinstance(text, str):
        raise audio_http.bad_response("The speech service returned a reply that is not a transcript.")
    if len(text.encode("utf-8")) > audio_http.MAX_TRANSCRIPTION_RESPONSE_BYTES:
        raise audio_http.response_too_large()
    return text


async def synthesize(config: TtsConfig, text: str) -> Tuple[bytes, str]:
    extra: Dict[str, Any] = {"input": text, "response_format": config.response_format}
    if config.voice:
        extra["voice"] = config.voice
    if config.instructions:
        extra["instructions"] = config.instructions
    if config.speed is not None:
        extra["speed"] = config.speed
    response = await _call(config, "aspeech", **extra)
    audio = getattr(response, "content", None)
    if not isinstance(audio, (bytes, bytearray)) or not audio:
        raise audio_http.bad_response("The speech service returned no audio.")
    if len(audio) > config.max_audio_bytes:
        raise audio_http.response_too_large()
    raw_response = getattr(response, "response", None)
    headers = getattr(raw_response, "headers", None) or {}
    media_type = audio_http.audio_media_type(headers, TTS_FORMAT_MEDIA_TYPES[config.response_format])
    return bytes(audio), media_type
