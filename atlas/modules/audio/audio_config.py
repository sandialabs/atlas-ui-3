"""Resolved speech-to-text and text-to-speech configuration (issue #1251).

The two capabilities are configured independently through ``AppSettings``
(``FEATURE_AUDIO_STT_ENABLED`` / ``AUDIO_STT_*`` and
``FEATURE_AUDIO_TTS_ENABLED`` / ``AUDIO_TTS_*``). Neither depends on the chat
models: each names its own provider adapter (issue #1255; default
``openai_compatible``, see ``audio_providers``), base URL, model and optional
credential.

A capability is *available* only when it is switched on and completely
configured. One that is switched on with a missing or malformed setting is
treated as off everywhere -- the config endpoints hide it and the routes
refuse it -- and the reason is logged once for the operator.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from atlas.modules.audio.audio_providers import (
    DEFAULT_PROVIDER,
    PROVIDERS,
    TTS_SPEED_RANGE,
    provider_spec,
)

logger = logging.getLogger(__name__)

# Formats the OpenAI speech API defines, with the media type each is served as.
TTS_FORMAT_MEDIA_TYPES: Dict[str, str] = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "flac": "audio/flac",
}

_warned: set = set()


def _split_csv(raw: Any) -> Tuple[str, ...]:
    if not raw or not isinstance(raw, str):
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _base_url_problem(base_url: str) -> Optional[str]:
    if not base_url:
        return "no base URL is set"
    if any(ord(char) <= 0x20 or ord(char) == 0x7F for char in base_url):
        return "the base URL contains whitespace or a control character"
    # urlparse raises ValueError for some malformed URLs (an unclosed IPv6
    # bracket) and so does reading ``.port`` (a port that is not a number).
    # A typo in an audio setting must switch that capability off, not take
    # down the config endpoints that report it.
    try:
        parsed = urlparse(base_url)
        hostname, _port = parsed.hostname, parsed.port
        has_credentials = bool(parsed.username or parsed.password)
    except ValueError:
        return "the base URL could not be parsed"
    if parsed.scheme not in ("http", "https") or not hostname:
        return "the base URL is not an http(s) URL"
    if has_credentials:
        return "the base URL must not carry credentials; use the API key setting"
    return None


@dataclass(frozen=True)
class AudioCapabilityConfig:
    """One capability's settings, as the routes and config endpoints read them."""

    name: str  # "stt" or "tts"
    enabled: bool = False
    provider: str = DEFAULT_PROVIDER
    show_button_by_default: bool = True
    base_url: str = ""
    model: str = ""
    api_key: Optional[str] = field(default=None, repr=False)
    groups: Tuple[str, ...] = ()
    allowed_data_classifications: Tuple[str, ...] = ()
    timeout_seconds: float = 60.0
    # Why an enabled capability is unusable; None when it is usable or off.
    config_error: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.enabled and self.config_error is None


@dataclass(frozen=True)
class SttConfig(AudioCapabilityConfig):
    max_audio_bytes: int = 25 * 1024 * 1024
    max_duration_seconds: int = 300


@dataclass(frozen=True)
class TtsConfig(AudioCapabilityConfig):
    voice: str = ""
    response_format: str = "mp3"
    max_input_chars: int = 4096
    max_audio_bytes: int = 25 * 1024 * 1024
    instructions: str = field(default="", repr=False)
    speed: Optional[float] = None


def _warn_once(capability: str, problem: str) -> None:
    key = (capability, problem)
    if key in _warned:
        return
    _warned.add(key)
    flag = f"FEATURE_AUDIO_{capability.upper()}_ENABLED"
    logger.warning(
        "%s is true but the capability stays off: %s", flag, problem
    )


def _common(settings: Any, prefix: str) -> Dict[str, Any]:
    def get(name: str, default: Any = None) -> Any:
        return getattr(settings, f"audio_{prefix}_{name}", default)

    provider = str(get("provider", DEFAULT_PROVIDER) or DEFAULT_PROVIDER).strip().lower()
    spec = provider_spec(provider)
    api_key = get("api_key")
    api_key = (str(api_key).strip() or None) if api_key else None
    if api_key is None and spec is not None:
        for env_name in spec.fallback_env_names:
            api_key = os.environ.get(env_name, "").strip() or None
            if api_key:
                break
    base_url = str(get("base_url", "") or "").strip().rstrip("/")
    if not base_url and spec is not None and spec.default_base_url:
        base_url = spec.default_base_url
    return {
        "name": prefix,
        "enabled": bool(getattr(settings, f"feature_audio_{prefix}_enabled", False)),
        "provider": provider,
        "show_button_by_default": bool(get("show_button_by_default", True)),
        "base_url": base_url,
        "model": str(get("model", "") or "").strip(),
        "api_key": api_key,
        "groups": _split_csv(get("groups", "")),
        "allowed_data_classifications": _split_csv(get("allowed_data_classifications", "")),
        "timeout_seconds": float(get("timeout_seconds", 60.0)),
    }


def _provider_problem(values: Dict[str, Any]) -> Optional[str]:
    """What stops the common settings from working with the chosen provider.

    Reads only whether a credential is present, never its value, so nothing
    derived from it can reach the warning this message is logged in.
    """
    return _settings_problem(
        values["name"].upper(), values["provider"], values["base_url"], values["model"], values["api_key"] is not None
    )


def _settings_problem(prefix: str, provider: str, base_url: str, model: str, key_set: bool) -> Optional[str]:
    spec = provider_spec(provider)
    if spec is None:
        return f"AUDIO_{prefix}_PROVIDER must be one of " + ", ".join(sorted(PROVIDERS))
    if base_url or not spec.base_url_optional:
        problem = _base_url_problem(base_url)
        if problem:
            return problem
    if not model:
        return f"AUDIO_{prefix}_MODEL is not set"
    if spec.needs_key and not key_set:
        names = " or ".join((f"AUDIO_{prefix}_API_KEY",) + spec.fallback_env_names)
        return f"the {spec.name} provider needs a credential; set {names}"
    return None


def resolve_stt_config(settings: Any) -> SttConfig:
    """The speech-to-text settings, with completeness checked."""
    values = _common(settings, "stt")
    problem = None
    if values["enabled"]:
        problem = _provider_problem(values)
        if problem:
            _warn_once("stt", problem)
    return SttConfig(
        **values,
        config_error=problem,
        max_audio_bytes=int(getattr(settings, "audio_stt_max_audio_bytes", 25 * 1024 * 1024)),
        max_duration_seconds=int(getattr(settings, "audio_stt_max_duration_seconds", 300)),
    )


def _parse_speed(raw: Any) -> Tuple[Optional[float], Optional[str]]:
    """AUDIO_TTS_SPEED as a number, or None when unset, and any problem with it."""
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return None, None
    low, high = TTS_SPEED_RANGE
    problem = f"AUDIO_TTS_SPEED must be a number between {low} and {high}"
    try:
        speed = float(text)
    except ValueError:
        return None, problem
    if not (math.isfinite(speed) and low <= speed <= high):
        return None, problem
    return speed, None


def resolve_tts_config(settings: Any) -> TtsConfig:
    """The text-to-speech settings, with completeness checked."""
    values = _common(settings, "tts")
    voice = str(getattr(settings, "audio_tts_voice", "") or "").strip()
    response_format = str(getattr(settings, "audio_tts_response_format", "mp3") or "mp3").strip().lower()
    instructions = str(getattr(settings, "audio_tts_instructions", "") or "").strip()
    speed, speed_problem = _parse_speed(getattr(settings, "audio_tts_speed", ""))
    spec = provider_spec(values["provider"])
    if spec is not None and spec.tts_formats is None:
        # The provider decides the format (Gemini returns PCM, framed as WAV).
        response_format = "wav"
    problem = None
    if values["enabled"]:
        problem = _provider_problem(values)
        if problem is None and spec.tts_voice_required and not voice:
            problem = "AUDIO_TTS_VOICE is not set"
        if problem is None and response_format not in TTS_FORMAT_MEDIA_TYPES:
            problem = "AUDIO_TTS_RESPONSE_FORMAT must be one of " + ", ".join(sorted(TTS_FORMAT_MEDIA_TYPES))
        if problem is None and spec.tts_formats is not None and response_format not in spec.tts_formats:
            problem = (
                f"the {spec.name} provider supports AUDIO_TTS_RESPONSE_FORMAT "
                + ", ".join(sorted(spec.tts_formats))
            )
        if problem is None:
            problem = speed_problem
        if problem:
            _warn_once("tts", problem)
    return TtsConfig(
        **values,
        config_error=problem,
        voice=voice,
        response_format=response_format,
        max_input_chars=int(getattr(settings, "audio_tts_max_input_chars", 4096)),
        max_audio_bytes=int(getattr(settings, "audio_tts_max_audio_bytes", 25 * 1024 * 1024)),
        instructions=instructions,
        speed=speed,
    )


async def user_may_use(config: AudioCapabilityConfig, user_email: str) -> bool:
    """Whether the capability is available to this user.

    Follows the ``groups`` convention models and MCP servers use: no groups
    means every authenticated user, otherwise membership in at least one.
    """
    if not config.available:
        return False
    if not config.groups:
        return True
    from atlas.core.auth import is_user_in_group

    # Asked together: with an external authorizer each lookup is a round trip,
    # and this runs on the page-load config fetch.
    memberships = await asyncio.gather(*(is_user_in_group(user_email, group) for group in config.groups))
    return any(memberships)


def _classifications_for_client(config: AudioCapabilityConfig, compliance_enabled: bool) -> Optional[List[str]]:
    if not compliance_enabled:
        return None
    return list(config.allowed_data_classifications)


async def public_audio_config(settings: Any, user_email: str) -> Dict[str, Any]:
    """What the browser may know: availability, defaults and limits.

    Never includes the provider, endpoint URLs, model or voice identifiers,
    instructions, or credentials. ``enabled`` is already per-user (it folds in ``groups``), so
    the client needs no second check to decide whether to draw a control.
    """
    stt = resolve_stt_config(settings)
    tts = resolve_tts_config(settings)
    compliance_enabled = bool(getattr(settings, "feature_compliance_levels_enabled", False))
    stt_enabled, tts_enabled = await asyncio.gather(user_may_use(stt, user_email), user_may_use(tts, user_email))
    result: Dict[str, Any] = {
        "stt": {
            "enabled": stt_enabled,
            "show_button_by_default": stt.show_button_by_default,
        },
        "tts": {
            "enabled": tts_enabled,
            "show_button_by_default": tts.show_button_by_default,
        },
    }
    if stt_enabled:
        result["stt"].update(
            max_audio_bytes=stt.max_audio_bytes,
            max_duration_seconds=stt.max_duration_seconds,
            allowed_data_classifications=_classifications_for_client(stt, compliance_enabled),
        )
    if tts_enabled:
        result["tts"].update(
            max_input_chars=tts.max_input_chars,
            allowed_data_classifications=_classifications_for_client(tts, compliance_enabled),
        )
    return result
