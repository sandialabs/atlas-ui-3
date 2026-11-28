"""The litellm adapter, and every provider behind the unchanged routes (issue #1255).

``litellm.aspeech`` / ``litellm.atranscription`` are replaced with fakes that
record their arguments; the direct-HTTP adapters use an
``httpx.MockTransport`` installed through ``audio_http.build_http_client``.
"""

import asyncio
import base64
import logging

import httpx
import litellm
import pytest
from main import app
from starlette.testclient import TestClient

from atlas.infrastructure.app_factory import app_factory
from atlas.modules.audio import audio_http, litellm_audio_client
from atlas.modules.audio.audio_config import SttConfig, TtsConfig
from atlas.modules.audio.audio_formats import pcm_to_wav
from atlas.modules.audio.audio_http import AudioUpstreamError

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
WAV = pcm_to_wav(b"\x01\x00" * 32)
MP3 = b"ID3" + b"\x00" * 64
PCM = b"\x10\x00\x20\x00" * 50


class FakeSpeechResponse:
    def __init__(self, content, content_type="audio/mpeg"):
        self.content = content
        self.response = httpx.Response(200, content=content, headers={"content-type": content_type})


@pytest.fixture
def fake_litellm(monkeypatch):
    class Fake:
        def __init__(self):
            self.calls = []
            self.speech_result = FakeSpeechResponse(MP3)
            self.transcription_result = litellm.types.utils.TranscriptionResponse(text="from litellm")
            self.error = None
            self.delay = 0

        async def _run(self, name, kwargs, result):
            self.calls.append((name, kwargs))
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error is not None:
                raise self.error
            return result

        async def aspeech(self, **kwargs):
            return await self._run("aspeech", kwargs, self.speech_result)

        async def atranscription(self, **kwargs):
            return await self._run("atranscription", kwargs, self.transcription_result)

    fake = Fake()
    monkeypatch.setattr(litellm, "aspeech", fake.aspeech)
    monkeypatch.setattr(litellm, "atranscription", fake.atranscription)
    return fake


def stt(**overrides):
    values = dict(name="stt", enabled=True, provider="litellm", model="groq/whisper-large-v3", timeout_seconds=5.0)
    values.update(overrides)
    return SttConfig(**values)


def tts(**overrides):
    values = dict(name="tts", enabled=True, provider="litellm", model="elevenlabs/eleven_multilingual_v2", voice="v1", max_audio_bytes=4096, timeout_seconds=5.0)
    values.update(overrides)
    return TtsConfig(**values)


# --- litellm adapter ---------------------------------------------------------


async def test_litellm_transcription_arguments(fake_litellm):
    text = await litellm_audio_client.transcribe(stt(), content=WEBM, filename="audio.webm", content_type="audio/webm", language="de")
    assert text == "from litellm"
    name, kwargs = fake_litellm.calls[0]
    assert name == "atranscription"
    assert kwargs["model"] == "groq/whisper-large-v3"
    assert kwargs["file"] == ("audio.webm", WEBM, "audio/webm")
    assert kwargs["language"] == "de"
    assert kwargs["max_retries"] == 0 and kwargs["no-log"] is True and kwargs["timeout"] == 5.0
    # Neither is sent when unset, so LiteLLM uses the provider's own endpoint
    # and environment credential.
    assert "api_key" not in kwargs and "api_base" not in kwargs


async def test_litellm_passes_base_url_and_key_when_set(fake_litellm):
    await litellm_audio_client.transcribe(
        stt(base_url="http://speaches:8000/v1", api_key="k-1"), content=WAV, filename="audio.wav", content_type="audio/wav"
    )
    kwargs = fake_litellm.calls[0][1]
    assert kwargs["api_base"] == "http://speaches:8000/v1" and kwargs["api_key"] == "k-1"
    assert "language" not in kwargs


async def test_litellm_speech_arguments_and_reply(fake_litellm):
    audio, media_type = await litellm_audio_client.synthesize(tts(instructions="Warmly.", speed=1.25, response_format="mp3"), "Hello.")
    assert (audio, media_type) == (MP3, "audio/mpeg")
    kwargs = fake_litellm.calls[0][1]
    assert kwargs["input"] == "Hello." and kwargs["voice"] == "v1" and kwargs["response_format"] == "mp3"
    assert kwargs["instructions"] == "Warmly." and kwargs["speed"] == 1.25
    assert kwargs["no-log"] is True and kwargs["max_retries"] == 0


async def test_litellm_speech_omits_unset_options(fake_litellm):
    await litellm_audio_client.synthesize(tts(voice=""), "Hello.")
    kwargs = fake_litellm.calls[0][1]
    for name in ("voice", "instructions", "speed", "api_key", "api_base"):
        assert name not in kwargs


@pytest.mark.parametrize(
    "result,kind",
    [
        (FakeSpeechResponse(b""), "upstream_bad_response"),
        (FakeSpeechResponse(MP3, "text/html"), "upstream_bad_response"),
        (FakeSpeechResponse(b"\x00" * 5000), "upstream_response_too_large"),
        (object(), "upstream_bad_response"),
    ],
)
async def test_litellm_speech_bad_replies(fake_litellm, result, kind):
    fake_litellm.speech_result = result
    with pytest.raises(AudioUpstreamError) as raised:
        await litellm_audio_client.synthesize(tts(), "Hello.")
    assert raised.value.kind == kind


async def test_litellm_unlabeled_speech_gets_the_requested_type(fake_litellm):
    fake_litellm.speech_result = FakeSpeechResponse(WAV, "application/octet-stream")
    assert (await litellm_audio_client.synthesize(tts(response_format="wav"), "Hi."))[1] == "audio/wav"


async def test_litellm_transcription_without_text_is_a_bad_response(fake_litellm):
    fake_litellm.transcription_result = {"segments": []}
    with pytest.raises(AudioUpstreamError) as raised:
        await litellm_audio_client.transcribe(stt(), content=WAV, filename="audio.wav", content_type="audio/wav")
    assert raised.value.kind == "upstream_bad_response"


def _litellm_error(cls, status):
    request = httpx.Request("POST", "https://provider.test/v1")
    response = httpx.Response(status, request=request, text="echo: the secret sentence, key sk-provider-secret")
    return cls(message="echo: the secret sentence, key sk-provider-secret", llm_provider="openai", model="m", response=response)


@pytest.mark.parametrize(
    "error,kind,status",
    [
        (lambda: _litellm_error(litellm.exceptions.AuthenticationError, 401), "upstream_auth", 502),
        (lambda: _litellm_error(litellm.exceptions.NotFoundError, 404), "upstream_not_found", 502),
        (lambda: _litellm_error(litellm.exceptions.BadRequestError, 400), "upstream_rejected", 422),
        (lambda: _litellm_error(litellm.exceptions.RateLimitError, 429), "upstream_busy", 503),
        (lambda: litellm.exceptions.Timeout(message="the secret sentence", model="m", llm_provider="openai"), "timeout", 504),
        (lambda: litellm.exceptions.APIConnectionError(message="the secret sentence", llm_provider="openai", model="m"), "unreachable", 502),
        (lambda: ValueError("the secret sentence"), "upstream_error", 502),
    ],
)
async def test_litellm_errors_are_mapped_without_their_text(fake_litellm, caplog, error, kind, status):
    fake_litellm.error = error()
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(AudioUpstreamError) as raised:
            await litellm_audio_client.synthesize(tts(), "the secret sentence")
    assert (raised.value.kind, raised.value.status_code) == (kind, status)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "secret" not in logged and "secret" not in raised.value.detail


async def test_litellm_call_is_bounded_by_the_timeout(fake_litellm):
    fake_litellm.delay = 5
    with pytest.raises(AudioUpstreamError) as raised:
        await litellm_audio_client.synthesize(tts(timeout_seconds=0.05), "Hello.")
    assert raised.value.kind == "timeout"


# --- every provider behind the routes ----------------------------------------


@pytest.fixture
def settings():
    return app_factory.get_config_manager().app_settings


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def upstream(monkeypatch):
    class Upstream:
        def __init__(self):
            self.requests = []
            self.handler = lambda request: httpx.Response(500)

        async def dispatch(self, request):
            await request.aread()
            self.requests.append(request)
            return self.handler(request)

    fake = Upstream()
    monkeypatch.setattr(
        audio_http,
        "build_http_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(fake.dispatch), timeout=timeout, follow_redirects=False),
    )
    return fake


@pytest.fixture
def provider_on(monkeypatch, settings):
    def configure(stt_provider, tts_provider, **extra):
        values = {
            "feature_audio_stt_enabled": True,
            "audio_stt_provider": stt_provider,
            "audio_stt_base_url": "",
            "audio_stt_model": "stt-model",
            "audio_stt_api_key": "stt-secret-value",
            "feature_audio_tts_enabled": True,
            "audio_tts_provider": tts_provider,
            "audio_tts_base_url": "",
            "audio_tts_model": "tts-model",
            "audio_tts_voice": "voice-1",
            "audio_tts_api_key": "tts-secret-value",
            "audio_tts_response_format": "mp3",
            "audio_tts_instructions": "",
            "audio_tts_speed": "",
            "feature_compliance_levels_enabled": False,
        }
        values.update(extra)
        for name, value in values.items():
            monkeypatch.setattr(settings, name, value, raising=False)
        return settings

    return configure


def post_clip(client, headers, content=WEBM, content_type="audio/webm;codecs=opus", **fields):
    return client.post("/api/audio/transcriptions", headers=headers, files={"file": ("memo.webm", content, content_type)}, data=fields)


def post_speech(client, headers, text="Read this aloud.", **extra):
    return client.post("/api/audio/speech", headers=headers, json={"input": text, **extra})


def test_gemini_through_the_routes(client, test_user_headers, upstream, provider_on):
    provider_on("gemini", "gemini")

    def reply(request):
        body = request.content
        if b"responseModalities" in body:
            data = base64.b64encode(PCM).decode()
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/L16;rate=24000", "data": data}}]}}]})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "gemini heard you"}]}}]})

    upstream.handler = reply
    assert post_clip(client, test_user_headers).json() == {"text": "gemini heard you"}
    response = post_speech(client, test_user_headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["cache-control"] == "no-store"
    assert response.content == pcm_to_wav(PCM, 24000)
    assert all(str(r.url).startswith("https://generativelanguage.googleapis.com/v1beta/models/") for r in upstream.requests)


def test_openai_chat_audio_refuses_browser_webm_before_calling_upstream(client, test_user_headers, upstream, provider_on):
    provider_on("openai_chat_audio", "openai_chat_audio", audio_stt_base_url="https://api.test/v1", audio_tts_base_url="https://api.test/v1")
    response = post_clip(client, test_user_headers)
    assert response.status_code == 415
    assert "does not accept this audio format" in response.json()["detail"]
    assert upstream.requests == []

    upstream.handler = lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "wav ok"}}]})
    assert post_clip(client, test_user_headers, content=WAV, content_type="audio/wav").json() == {"text": "wav ok"}


def test_elevenlabs_through_the_routes(client, test_user_headers, upstream, provider_on):
    provider_on("elevenlabs", "elevenlabs")
    upstream.handler = lambda r: (
        httpx.Response(200, json={"text": "scribe says hi"})
        if r.url.path.endswith("/speech-to-text")
        else httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
    )
    assert post_clip(client, test_user_headers).json() == {"text": "scribe says hi"}
    assert post_speech(client, test_user_headers).content == MP3
    assert {r.headers["xi-api-key"] for r in upstream.requests} == {"stt-secret-value", "tts-secret-value"}


def test_litellm_through_the_routes(client, test_user_headers, upstream, provider_on, fake_litellm):
    provider_on("litellm", "litellm")
    assert post_clip(client, test_user_headers).json() == {"text": "from litellm"}
    assert post_speech(client, test_user_headers).content == MP3
    assert [name for name, _ in fake_litellm.calls] == ["atranscription", "aspeech"]
    assert upstream.requests == []


def test_providers_are_independent(client, test_user_headers, upstream, provider_on, fake_litellm):
    provider_on("litellm", "openai_compatible", audio_tts_base_url="http://tts.test/v1", audio_tts_speed="1.5", audio_tts_instructions="Slowly.")
    upstream.handler = lambda r: httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
    assert post_clip(client, test_user_headers).status_code == 200
    assert post_speech(client, test_user_headers).status_code == 200
    assert [name for name, _ in fake_litellm.calls] == ["atranscription"]
    import json

    sent = json.loads(upstream.requests[0].content)
    assert sent["speed"] == 1.5 and sent["instructions"] == "Slowly."


def test_misconfigured_provider_answers_404(client, test_user_headers, upstream, provider_on, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider_on("gemini", "nonsense", audio_stt_api_key=None)
    assert post_clip(client, test_user_headers).status_code == 404
    assert post_speech(client, test_user_headers).status_code == 404
    shell = client.get("/api/config/shell", headers=test_user_headers).json()["audio"]
    assert shell["stt"]["enabled"] is False and shell["tts"]["enabled"] is False
    assert upstream.requests == []


def test_compliance_gate_applies_to_every_provider(client, test_user_headers, upstream, provider_on, fake_litellm):
    from atlas.core.compliance import get_compliance_manager

    levels = get_compliance_manager().get_all_levels()
    approved, other = levels[0], levels[1]
    provider_on(
        "litellm", "gemini",
        feature_compliance_levels_enabled=True,
        feature_compliance_level_required=False,
        audio_stt_allowed_data_classifications=approved,
        audio_tts_allowed_data_classifications=approved,
    )
    assert post_clip(client, test_user_headers, compliance_level=other).status_code == 403
    assert post_speech(client, test_user_headers, compliance_level=other).status_code == 403
    assert fake_litellm.calls == [] and upstream.requests == []
    assert post_clip(client, test_user_headers, compliance_level=approved).status_code == 200


def test_provider_payloads_and_credentials_are_not_logged(client, test_user_headers, upstream, provider_on, caplog, monkeypatch):
    from atlas.modules.config import config_manager

    monkeypatch.setattr(config_manager.app_settings, "feature_metrics_logging_enabled", True, raising=False)
    provider_on("gemini", "openai_chat_audio", audio_tts_base_url="https://api.test/v1", audio_tts_instructions="the secret style")
    upstream.handler = lambda r: (
        httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "the secret transcript"}]}}]})
        if b"inlineData" in r.content
        else httpx.Response(500, text="echo: the secret sentence")
    )
    with caplog.at_level(logging.DEBUG):
        assert post_clip(client, test_user_headers).status_code == 200
        assert post_speech(client, test_user_headers, text="the secret sentence").status_code == 502
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "audio_transcription" in logged
    for leaked in ("secret transcript", "secret sentence", "secret style", "stt-secret-value", "tts-secret-value", "memo.webm"):
        assert leaked not in logged
