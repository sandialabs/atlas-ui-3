"""Client for OpenAI-compatible audio endpoints (issue #1251).

The ``openai_compatible`` provider, and the default. Speaks exactly two
requests: ``POST {base_url}/audio/transcriptions`` (multipart upload, JSON
reply) and ``POST {base_url}/audio/speech`` (JSON request, binary reply). Any
server or gateway that implements those two routes works, including Speaches
and a LiteLLM proxy.

The optional ``AUDIO_TTS_INSTRUCTIONS`` and ``AUDIO_TTS_SPEED`` settings are
added to the speech request only when they are set (issue #1255), so a
deployment that sets neither sends exactly the request it always did.

HTTP handling and its guarantees live in :mod:`atlas.modules.audio.audio_http`.
"""

from __future__ import annotations

import json
from typing import Optional, Tuple

from atlas.modules.audio import audio_http
from atlas.modules.audio.audio_config import (
    TTS_FORMAT_MEDIA_TYPES,
    AudioCapabilityConfig,
    SttConfig,
    TtsConfig,
)

# Re-exported for callers written against #1251.
AudioUpstreamError = audio_http.AudioUpstreamError
MAX_TRANSCRIPTION_RESPONSE_BYTES = audio_http.MAX_TRANSCRIPTION_RESPONSE_BYTES


def _headers(config: AudioCapabilityConfig) -> dict:
    return {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}


async def transcribe(
    config: SttConfig,
    *,
    content: bytes,
    filename: str,
    content_type: str,
    language: Optional[str] = None,
) -> str:
    """Send one recording for transcription and return the text."""
    data = {"model": config.model, "response_format": "json"}
    if language:
        data["language"] = language
    body, _ = await audio_http.post(
        config.name,
        f"{config.base_url}/audio/transcriptions",
        timeout_seconds=config.timeout_seconds,
        response_limit=MAX_TRANSCRIPTION_RESPONSE_BYTES,
        headers=_headers(config),
        data=data,
        files={"file": (filename, content, content_type)},
    )
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        raise audio_http.bad_response("The speech service returned a reply that is not a transcript.")
    return text


async def synthesize(config: TtsConfig, text: str) -> Tuple[bytes, str]:
    """Synthesize speech for ``text``; returns the audio and its media type."""
    request = {
        "model": config.model,
        "input": text,
        "voice": config.voice,
        "response_format": config.response_format,
    }
    if config.instructions:
        request["instructions"] = config.instructions
    if config.speed is not None:
        request["speed"] = config.speed
    body, headers = await audio_http.post(
        config.name,
        f"{config.base_url}/audio/speech",
        timeout_seconds=config.timeout_seconds,
        response_limit=config.max_audio_bytes,
        headers=_headers(config),
        json=request,
    )
    media_type = audio_http.audio_media_type(headers, TTS_FORMAT_MEDIA_TYPES[config.response_format])
    if not body:
        raise audio_http.bad_response("The speech service returned no audio.")
    return body, media_type
