"""Route a dictation or read-aloud request to the configured adapter (issue #1255).

The routes call :func:`transcribe` and :func:`synthesize` here and never an
adapter directly, so every adapter sits behind the same policy in
``audio_routes`` (authentication, groups, data classification, upload and
text limits, concurrency) and returns the same normalized result: the text
for speech-to-text, ``(audio_bytes, media_type)`` for text-to-speech, or an
``AudioUpstreamError`` whose detail is safe to show.
"""

from __future__ import annotations

from types import ModuleType
from typing import Optional, Tuple

from atlas.modules.audio import (
    elevenlabs_audio_client,
    gemini_audio_client,
    litellm_audio_client,
    openai_audio_client,
    openai_chat_audio_client,
)
from atlas.modules.audio.audio_config import SttConfig, TtsConfig
from atlas.modules.audio.audio_http import AudioUpstreamError
from atlas.modules.audio.audio_providers import (
    ELEVENLABS,
    GEMINI,
    LITELLM,
    OPENAI_CHAT_AUDIO,
    OPENAI_COMPATIBLE,
    PROVIDERS,
    provider_spec,
)

_ADAPTERS = {
    OPENAI_COMPATIBLE: openai_audio_client,
    LITELLM: litellm_audio_client,
    GEMINI: gemini_audio_client,
    ELEVENLABS: elevenlabs_audio_client,
    OPENAI_CHAT_AUDIO: openai_chat_audio_client,
}

# Every provider an operator can select has an adapter, and nothing else
# does: a name in only one table would be reported available and then fail
# on the first request.
if set(_ADAPTERS) != set(PROVIDERS):  # pragma: no cover -- a programming error, caught at import
    raise RuntimeError("audio provider registry and adapter table disagree")


def _adapter(provider: str) -> ModuleType:
    adapter = _ADAPTERS.get(provider)
    if adapter is None:
        # resolve_*_config marks an unknown provider unavailable, so the
        # routes answer 404 before getting here; this is a guard.
        raise AudioUpstreamError(502, "The speech service is not configured correctly.", "unknown_provider")
    return adapter


def stt_accepts(config: SttConfig, media_type: str) -> bool:
    """Whether the configured provider takes uploads of this canonical type."""
    spec = provider_spec(config.provider)
    return spec is not None and (spec.stt_media_types is None or media_type in spec.stt_media_types)


async def transcribe(
    config: SttConfig,
    *,
    content: bytes,
    filename: str,
    content_type: str,
    language: Optional[str] = None,
) -> str:
    return await _adapter(config.provider).transcribe(
        config, content=content, filename=filename, content_type=content_type, language=language
    )


async def synthesize(config: TtsConfig, text: str) -> Tuple[bytes, str]:
    return await _adapter(config.provider).synthesize(config, text)
