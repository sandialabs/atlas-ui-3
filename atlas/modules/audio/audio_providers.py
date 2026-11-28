"""The audio providers an operator can select, and what each one needs (issue #1255).

``AUDIO_STT_PROVIDER`` and ``AUDIO_TTS_PROVIDER`` pick one of these names
independently. ``openai_compatible`` is the default and is the #1251
behavior unchanged.

This module only describes the providers; ``audio_config`` uses the
descriptions to decide whether a capability is completely configured, and
``audio_provider_dispatch`` routes a request to the adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Optional, Tuple

OPENAI_COMPATIBLE = "openai_compatible"
LITELLM = "litellm"
GEMINI = "gemini"
ELEVENLABS = "elevenlabs"
OPENAI_CHAT_AUDIO = "openai_chat_audio"

DEFAULT_PROVIDER = OPENAI_COMPATIBLE

ALL_TTS_FORMATS: FrozenSet[str] = frozenset({"mp3", "wav", "opus", "aac", "flac"})


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    # Base URL used when AUDIO_*_BASE_URL is empty; None means the setting is
    # required (or, with base_url_optional, that the adapter needs none).
    default_base_url: Optional[str] = None
    # The litellm adapter routes by model prefix; a base URL only overrides
    # the provider's own endpoint.
    base_url_optional: bool = False
    # Whether a request without a credential is pointless (a hosted API).
    needs_key: bool = False
    # Environment variables read for the credential when AUDIO_*_API_KEY is
    # empty, in order. The provider's conventional name, so an installation
    # that already sets GOOGLE_API_KEY for its chat models need not repeat it.
    fallback_env_names: Tuple[str, ...] = ()
    tts_voice_required: bool = True
    # Formats AUDIO_TTS_RESPONSE_FORMAT may name. None: the provider decides
    # the format and the setting is not read (Gemini always yields WAV).
    tts_formats: Optional[FrozenSet[str]] = ALL_TTS_FORMATS
    # Whether the adapter sends AUDIO_TTS_SPEED upstream. When it does not,
    # the setting is not read, so a bad value cannot switch read-aloud off.
    tts_speed: bool = True
    # Canonical upload media types the provider accepts for transcription.
    # None: everything ``audio_formats`` accepts. The list is published to the
    # browser, which hides the microphone when it cannot record any of them.
    stt_media_types: Optional[FrozenSet[str]] = None


PROVIDERS = {
    OPENAI_COMPATIBLE: ProviderSpec(OPENAI_COMPATIBLE),
    LITELLM: ProviderSpec(LITELLM, base_url_optional=True, tts_voice_required=False),
    GEMINI: ProviderSpec(
        GEMINI,
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        needs_key=True,
        fallback_env_names=("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        tts_voice_required=False,
        tts_formats=None,
        tts_speed=False,
    ),
    ELEVENLABS: ProviderSpec(
        ELEVENLABS,
        default_base_url="https://api.elevenlabs.io/v1",
        needs_key=True,
        fallback_env_names=("ELEVENLABS_API_KEY",),
        tts_formats=frozenset({"mp3", "wav", "opus"}),
    ),
    OPENAI_CHAT_AUDIO: ProviderSpec(
        OPENAI_CHAT_AUDIO,
        tts_formats=frozenset({"mp3", "wav", "opus", "flac"}),
        tts_speed=False,
        # Chat Completions ``input_audio`` takes only WAV and MP3.
        stt_media_types=frozenset({"audio/wav", "audio/mpeg"}),
    ),
}

# Accepted range for AUDIO_TTS_SPEED. OpenAI's speech API documents 0.25 to
# 4.0; a provider with a narrower range (ElevenLabs) rejects the request
# itself.
TTS_SPEED_RANGE = (0.25, 4.0)


def provider_spec(name: str) -> Optional[ProviderSpec]:
    return PROVIDERS.get(name)
