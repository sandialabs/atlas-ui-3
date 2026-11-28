"""Provider selection and per-provider completeness checks (issue #1255)."""

from types import SimpleNamespace

import pytest

from atlas.modules.audio.audio_config import (
    public_audio_config,
    resolve_stt_config,
    resolve_tts_config,
)
from atlas.modules.audio.audio_providers import PROVIDERS


def stt_settings(**overrides):
    values = {
        "feature_audio_stt_enabled": True,
        "audio_stt_base_url": "",
        "audio_stt_model": "model-x",
        "audio_stt_api_key": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def tts_settings(**overrides):
    values = {
        "feature_audio_tts_enabled": True,
        "audio_tts_base_url": "",
        "audio_tts_model": "model-y",
        "audio_tts_voice": "voice-1",
        "audio_tts_api_key": None,
        "audio_tts_response_format": "mp3",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def no_provider_keys(monkeypatch):
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_settings_default_to_the_openai_compatible_provider():
    from atlas.modules.config.settings import AppSettings

    fields = AppSettings.model_fields
    assert fields["audio_stt_provider"].default == "openai_compatible"
    assert fields["audio_tts_provider"].default == "openai_compatible"
    assert fields["audio_tts_instructions"].default == ""
    assert fields["audio_tts_speed"].default == ""


def test_settings_without_provider_attributes_resolve_to_openai_compatible():
    """An object without the #1255 fields resolves exactly as #1251 did."""
    stt = resolve_stt_config(stt_settings(audio_stt_base_url="http://stt.test/v1"))
    tts = resolve_tts_config(tts_settings(audio_tts_base_url="http://tts.test/v1"))
    assert stt.provider == tts.provider == "openai_compatible"
    assert stt.available and tts.available
    assert tts.instructions == "" and tts.speed is None


def test_empty_speed_setting_does_not_stop_appsettings_loading(monkeypatch):
    from atlas.modules.config.settings import AppSettings

    monkeypatch.setenv("AUDIO_TTS_SPEED", "")
    monkeypatch.setenv("AUDIO_TTS_PROVIDER", "")
    settings = AppSettings(_env_file=None)
    assert settings.audio_tts_speed == ""


def test_openai_compatible_still_requires_a_base_url():
    assert resolve_stt_config(stt_settings()).config_error == "no base URL is set"
    assert resolve_tts_config(tts_settings()).config_error == "no base URL is set"


def test_unknown_provider_disables_the_capability():
    config = resolve_stt_config(stt_settings(audio_stt_provider="whisperco", audio_stt_base_url="http://x.test"))
    assert not config.available
    assert "AUDIO_STT_PROVIDER must be one of" in config.config_error
    for name in PROVIDERS:
        assert name in config.config_error


def test_provider_name_is_case_insensitive():
    config = resolve_tts_config(tts_settings(audio_tts_provider=" LiteLLM "))
    assert config.provider == "litellm" and config.available


def test_litellm_needs_no_base_url_or_voice():
    stt = resolve_stt_config(stt_settings(audio_stt_provider="litellm", audio_stt_model="groq/whisper-large-v3"))
    tts = resolve_tts_config(tts_settings(audio_tts_provider="litellm", audio_tts_voice=""))
    assert stt.available and stt.base_url == ""
    assert tts.available and tts.voice == ""


def test_litellm_still_validates_a_base_url_when_one_is_set():
    config = resolve_stt_config(stt_settings(audio_stt_provider="litellm", audio_stt_base_url="ftp://x.test"))
    assert config.config_error == "the base URL is not an http(s) URL"


def test_litellm_still_requires_a_model():
    config = resolve_stt_config(stt_settings(audio_stt_provider="litellm", audio_stt_model=""))
    assert config.config_error == "AUDIO_STT_MODEL is not set"


def test_gemini_uses_the_public_endpoint_and_google_api_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    stt = resolve_stt_config(stt_settings(audio_stt_provider="gemini"))
    assert stt.available
    assert stt.base_url == "https://generativelanguage.googleapis.com/v1beta"
    assert stt.api_key == "google-key"


def test_gemini_accepts_gemini_api_key_and_prefers_the_audio_setting(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    assert resolve_stt_config(stt_settings(audio_stt_provider="gemini")).api_key == "gemini-key"
    explicit = resolve_stt_config(stt_settings(audio_stt_provider="gemini", audio_stt_api_key="own-key"))
    assert explicit.api_key == "own-key"


def test_gemini_without_a_key_is_disabled():
    config = resolve_tts_config(tts_settings(audio_tts_provider="gemini"))
    assert not config.available
    assert "GOOGLE_API_KEY" in config.config_error


def test_gemini_tts_needs_no_voice_and_always_yields_wav(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    config = resolve_tts_config(tts_settings(audio_tts_provider="gemini", audio_tts_voice="", audio_tts_response_format="mp3"))
    assert config.available
    assert config.response_format == "wav"


def test_gemini_base_url_can_be_overridden(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    config = resolve_stt_config(stt_settings(audio_stt_provider="gemini", audio_stt_base_url="https://gw.test/gemini/"))
    assert config.base_url == "https://gw.test/gemini"


def test_elevenlabs_defaults_and_key(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-key")
    config = resolve_tts_config(tts_settings(audio_tts_provider="elevenlabs"))
    assert config.available
    assert config.base_url == "https://api.elevenlabs.io/v1"
    assert config.api_key == "xi-key"


def test_elevenlabs_needs_a_voice_and_a_supported_format(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-key")
    assert resolve_tts_config(tts_settings(audio_tts_provider="elevenlabs", audio_tts_voice="")).config_error == "AUDIO_TTS_VOICE is not set"
    config = resolve_tts_config(tts_settings(audio_tts_provider="elevenlabs", audio_tts_response_format="aac"))
    assert not config.available and "elevenlabs" in config.config_error


def test_openai_chat_audio_requires_base_url_voice_and_supported_format():
    base = {"audio_tts_provider": "openai_chat_audio", "audio_tts_base_url": "https://api.test/v1"}
    assert resolve_tts_config(tts_settings(**base)).available
    assert resolve_tts_config(tts_settings(audio_tts_provider="openai_chat_audio")).config_error == "no base URL is set"
    assert resolve_tts_config(tts_settings(**base, audio_tts_voice="")).config_error == "AUDIO_TTS_VOICE is not set"
    assert not resolve_tts_config(tts_settings(**base, audio_tts_response_format="aac")).available


@pytest.mark.parametrize("raw,expected", [("", None), ("  ", None), ("1", 1.0), ("0.25", 0.25), ("4", 4.0), (" 1.5 ", 1.5)])
def test_speed_parsing(raw, expected):
    config = resolve_tts_config(tts_settings(audio_tts_base_url="http://tts.test", audio_tts_speed=raw))
    assert config.available
    assert config.speed == expected


@pytest.mark.parametrize("raw", ["fast", "0", "0.1", "4.5", "nan", "inf", "-1"])
def test_bad_speed_disables_read_aloud(raw):
    config = resolve_tts_config(tts_settings(audio_tts_base_url="http://tts.test", audio_tts_speed=raw))
    assert not config.available
    assert "AUDIO_TTS_SPEED" in config.config_error


def test_instructions_are_trimmed_and_kept_out_of_repr():
    config = resolve_tts_config(
        tts_settings(audio_tts_base_url="http://tts.test", audio_tts_instructions="  Speak warmly.  ", audio_tts_api_key="k")
    )
    assert config.instructions == "Speak warmly."
    assert "Speak warmly" not in repr(config)
    assert "'k'" not in repr(config)


@pytest.mark.asyncio
async def test_public_config_never_names_the_provider(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key-value")
    settings = SimpleNamespace(
        **vars(stt_settings(audio_stt_provider="gemini")),
        **vars(tts_settings(audio_tts_provider="gemini", audio_tts_instructions="Speak warmly.")),
    )
    result = await public_audio_config(settings, "user@test.com")
    assert result["stt"]["enabled"] is True and result["tts"]["enabled"] is True
    rendered = repr(result)
    for secret in ("gemini", "googleapis", "google-key-value", "model-x", "model-y", "voice-1", "Speak warmly"):
        assert secret not in rendered
