"""Dictation and read-aloud endpoints (issue #1251).

The audio server is an ``httpx.MockTransport``: every test sees the exact
request Atlas would send and decides the reply, so multipart framing, binary
relaying, error mapping and timeouts are exercised without a network.
"""

import asyncio
import json
import logging

import httpx
import pytest
from main import app
from starlette.testclient import TestClient

from atlas.infrastructure.app_factory import app_factory
from atlas.modules.audio import audio_http

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
MP3 = b"ID3" + b"\x00" * 64
STT_URL = "http://stt.test:8000/v1"
TTS_URL = "http://tts.test:9000/v1"


@pytest.fixture
def settings():
    return app_factory.get_config_manager().app_settings


def middleware_settings():
    """The settings object the middleware and the metrics logger read.

    It is a second ``AppSettings`` built from the same environment as the one
    the routes get from ``app_factory``; a test has to set a value on the one
    its code path reads.
    """
    from atlas.modules.config import config_manager

    return config_manager.app_settings


@pytest.fixture
def audio_on(monkeypatch, settings):
    """Both capabilities on, each with its own endpoint and credential."""
    values = {
        "feature_audio_stt_enabled": True,
        "audio_stt_base_url": STT_URL,
        "audio_stt_model": "whisper-test",
        "audio_stt_api_key": "stt-secret-value",
        "feature_audio_tts_enabled": True,
        "audio_tts_base_url": TTS_URL + "/",
        "audio_tts_model": "kokoro-test",
        "audio_tts_voice": "af_heart",
        "audio_tts_api_key": "tts-secret-value",
        "feature_compliance_levels_enabled": False,
    }
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    return settings


@pytest.fixture
def upstream(monkeypatch):
    """Install a fake audio server; ``upstream.handler`` decides the reply."""

    class Upstream:
        def __init__(self):
            self.requests = []
            self.bodies = []
            self.handler = lambda request: httpx.Response(200, json={"text": "hello world"})

        async def _dispatch(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            self.bodies.append(await request.aread())
            result = self.handler(request)
            if asyncio.iscoroutine(result):
                result = await result
            return result

    fake = Upstream()

    def build(timeout_seconds):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(fake._dispatch), timeout=timeout_seconds, follow_redirects=False
        )

    monkeypatch.setattr(audio_http, "build_http_client", build)
    return fake


@pytest.fixture
def client():
    return TestClient(app)


def post_clip(client, headers, content=WEBM, content_type="audio/webm;codecs=opus", **fields):
    return client.post(
        "/api/audio/transcriptions",
        headers=headers,
        files={"file": ("my private memo.webm", content, content_type)},
        data=fields,
    )


def post_speech(client, headers, text="Read this aloud.", **extra):
    return client.post("/api/audio/speech", headers=headers, json={"input": text, **extra})


# --- disabled by default --------------------------------------------------


def test_both_capabilities_are_off_by_default(settings):
    from atlas.modules.config.settings import AppSettings

    fields = AppSettings.model_fields
    assert fields["feature_audio_stt_enabled"].default is False
    assert fields["feature_audio_tts_enabled"].default is False


def test_disabled_endpoints_answer_404_without_calling_upstream(client, test_user_headers, upstream, monkeypatch, settings):
    monkeypatch.setattr(settings, "feature_audio_stt_enabled", False, raising=False)
    monkeypatch.setattr(settings, "feature_audio_tts_enabled", False, raising=False)
    assert post_clip(client, test_user_headers).status_code == 404
    assert post_speech(client, test_user_headers).status_code == 404
    assert upstream.requests == []


def test_capabilities_are_independent(client, test_user_headers, upstream, audio_on, monkeypatch):
    monkeypatch.setattr(audio_on, "feature_audio_tts_enabled", False)
    assert post_clip(client, test_user_headers).status_code == 200
    assert post_speech(client, test_user_headers).status_code == 404

    monkeypatch.setattr(audio_on, "feature_audio_tts_enabled", True)
    monkeypatch.setattr(audio_on, "feature_audio_stt_enabled", False)
    upstream.handler = lambda request: httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
    assert post_clip(client, test_user_headers).status_code == 404
    assert post_speech(client, test_user_headers).status_code == 200


@pytest.mark.parametrize(
    "field,value",
    [
        ("audio_stt_base_url", ""),
        ("audio_stt_base_url", "ftp://stt.test/v1"),
        ("audio_stt_base_url", "http://user:pw@stt.test/v1"),
        # These make urlparse (or reading the port) raise rather than return.
        ("audio_stt_base_url", "http://[::1:8000/v1"),
        ("audio_stt_base_url", "http://stt.test:port/v1"),
        ("audio_stt_base_url", "http://:8000/v1"),
        ("audio_stt_base_url", "http://stt.test:8000/v 1"),
        ("audio_stt_base_url", "http://stt.test:8000/v1\x7f"),
        ("audio_stt_model", ""),
    ],
)
def test_incomplete_stt_configuration_is_treated_as_disabled(client, test_user_headers, upstream, audio_on, monkeypatch, field, value):
    monkeypatch.setattr(audio_on, field, value)
    assert post_clip(client, test_user_headers).status_code == 404
    # A broken audio setting disables that capability only: both config
    # endpoints still answer, and the other capability is unaffected.
    for path in ("/api/config/shell", "/api/config"):
        response = client.get(path, headers=test_user_headers)
        assert response.status_code == 200, path
        assert response.json()["audio"]["stt"]["enabled"] is False
        assert response.json()["audio"]["tts"]["enabled"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("audio_tts_base_url", ""),
        ("audio_tts_base_url", "http://[::1:9000/v1"),
        ("audio_tts_model", ""),
        ("audio_tts_voice", ""),
        ("audio_tts_response_format", "exe"),
    ],
)
def test_incomplete_tts_configuration_is_treated_as_disabled(client, test_user_headers, upstream, audio_on, monkeypatch, field, value):
    monkeypatch.setattr(audio_on, field, value)
    assert post_speech(client, test_user_headers).status_code == 404
    assert client.get("/api/config/shell", headers=test_user_headers).json()["audio"]["tts"]["enabled"] is False
    assert upstream.requests == []


# --- authentication and authorization -------------------------------------


def test_unauthenticated_requests_are_refused(audio_on, upstream):
    """The routes rely on ``AuthMiddleware`` like every other ``/api`` route.

    The shared test app runs in debug mode, which signs every request in as
    the test user, so the production configuration is built here.
    """
    from fastapi import FastAPI

    from atlas.core.middleware import AuthMiddleware
    from atlas.routes.audio_routes import audio_router

    production = FastAPI()
    production.include_router(audio_router)
    production.add_middleware(AuthMiddleware, debug_mode=False)
    anonymous = TestClient(production)
    assert post_clip(anonymous, {}).status_code == 401
    assert post_speech(anonymous, {}).status_code == 401
    assert upstream.requests == []

    # Control: the same app serves an identified caller.
    assert post_clip(anonymous, {"X-User-Email": "someone@example.gov"}).status_code == 200


def test_group_restriction_hides_and_refuses_other_users(client, test_user_headers, test_user, upstream, audio_on, monkeypatch):
    async def membership(user, group):
        return group == "speakers"

    monkeypatch.setattr("atlas.core.auth.is_user_in_group", membership)
    monkeypatch.setattr(audio_on, "audio_stt_groups", "dictation-pilot, other")
    monkeypatch.setattr(audio_on, "audio_tts_groups", "speakers")

    assert post_clip(client, test_user_headers).status_code == 404
    upstream.handler = lambda request: httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
    assert post_speech(client, test_user_headers).status_code == 200

    shell = client.get("/api/config/shell", headers=test_user_headers).json()["audio"]
    assert shell["stt"]["enabled"] is False
    assert shell["tts"]["enabled"] is True


# --- config exposure --------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/config/shell", "/api/config"])
def test_config_exposes_availability_and_defaults_but_no_provider_details(client, test_user_headers, audio_on, monkeypatch, path):
    monkeypatch.setattr(audio_on, "audio_stt_show_button_by_default", False)
    response = client.get(path, headers=test_user_headers)
    assert response.status_code == 200
    audio = response.json()["audio"]
    assert audio["stt"]["enabled"] is True
    assert audio["stt"]["show_button_by_default"] is False
    assert audio["stt"]["max_audio_bytes"] == audio_on.audio_stt_max_audio_bytes
    assert audio["stt"]["max_duration_seconds"] == audio_on.audio_stt_max_duration_seconds
    assert audio["tts"]["enabled"] is True
    assert audio["tts"]["show_button_by_default"] is True
    assert audio["tts"]["max_input_chars"] == audio_on.audio_tts_max_input_chars
    body = response.text
    for secret in ("stt-secret-value", "tts-secret-value", "stt.test", "tts.test", "whisper-test", "kokoro-test", "af_heart"):
        assert secret not in body


def test_config_reports_disabled_capabilities(client, test_user_headers, monkeypatch, settings):
    monkeypatch.setattr(settings, "feature_audio_stt_enabled", False, raising=False)
    monkeypatch.setattr(settings, "feature_audio_tts_enabled", False, raising=False)
    audio = client.get("/api/config/shell", headers=test_user_headers).json()["audio"]
    assert audio == {
        "stt": {"enabled": False, "show_button_by_default": True},
        "tts": {"enabled": False, "show_button_by_default": True},
    }


# --- transcription ----------------------------------------------------------


def test_transcription_forwards_multipart_to_the_stt_endpoint(client, test_user_headers, upstream, audio_on):
    response = post_clip(client, test_user_headers, language="en", duration_seconds="3.2")
    assert response.status_code == 200
    assert response.json() == {"text": "hello world"}

    (request,) = upstream.requests
    assert str(request.url) == f"{STT_URL}/audio/transcriptions"
    assert request.headers["authorization"] == "Bearer stt-secret-value"
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    body = upstream.bodies[0]
    assert WEBM in body
    assert b'name="model"\r\n\r\nwhisper-test' in body
    assert b'name="language"\r\n\r\nen' in body
    assert b'filename="audio.webm"' in body
    assert b"Content-Type: audio/webm" in body
    # The user's filename stays in Atlas.
    assert b"private memo" not in body


def test_transcription_without_credential_sends_no_authorization(client, test_user_headers, upstream, audio_on, monkeypatch):
    monkeypatch.setattr(audio_on, "audio_stt_api_key", None)
    assert post_clip(client, test_user_headers).status_code == 200
    assert "authorization" not in upstream.requests[0].headers


def test_transcription_rejects_oversized_audio(client, test_user_headers, upstream, audio_on, monkeypatch):
    monkeypatch.setattr(audio_on, "audio_stt_max_audio_bytes", 1024)
    # Over the audio limit but inside the multipart allowance: the part itself is measured.
    response = post_clip(client, test_user_headers, content=WEBM + b"\x00" * 2048)
    assert response.status_code == 413
    # Far over: refused from Content-Length before the body is parsed.
    response = post_clip(client, test_user_headers, content=WEBM + b"\x00" * (200 * 1024))
    assert response.status_code == 413
    assert upstream.requests == []


def test_transcription_rejects_long_declared_duration(client, test_user_headers, upstream, audio_on, monkeypatch):
    monkeypatch.setattr(audio_on, "audio_stt_max_duration_seconds", 30)
    # A few seconds over is the recorder's stop timer running late, not a longer clip.
    assert post_clip(client, test_user_headers, duration_seconds="34").status_code == 200
    assert post_clip(client, test_user_headers, duration_seconds="45").status_code == 413
    assert post_clip(client, test_user_headers, duration_seconds="nan").status_code == 413
    assert post_clip(client, test_user_headers, duration_seconds="soon").status_code == 422


@pytest.mark.parametrize(
    "content,content_type,expected",
    [
        (WEBM, "text/plain", 415),
        (WEBM, "application/octet-stream", 415),
        (b"<html>not audio</html>", "audio/webm", 415),
        (b"MZ" + b"\x00" * 32, "audio/wav", 415),
        (b"", "audio/webm", 422),
        (b"OggS" + b"\x00" * 32, "audio/ogg; codecs=opus", 200),
        (b"\x00\x00\x00\x1cftypM4A " + b"\x00" * 32, "audio/mp4", 200),
        (b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32, "audio/x-wav", 200),
        (MP3, "audio/mpeg", 200),
    ],
)
def test_transcription_validates_content_type_and_signature(client, test_user_headers, upstream, audio_on, content, content_type, expected):
    assert post_clip(client, test_user_headers, content=content, content_type=content_type).status_code == expected
    assert len(upstream.requests) == (1 if expected == 200 else 0)


def test_transcription_requires_multipart_with_a_file(client, test_user_headers, upstream, audio_on):
    response = client.post("/api/audio/transcriptions", headers=test_user_headers, json={"file": "x"})
    assert response.status_code == 415
    response = client.post("/api/audio/transcriptions", headers=test_user_headers, files={"other": ("a.webm", WEBM, "audio/webm")})
    assert response.status_code == 422
    assert post_clip(client, test_user_headers, language="en; drop").status_code == 422
    assert upstream.requests == []


def test_transcription_malformed_multipart_is_a_422(client, test_user_headers, upstream, audio_on):
    """Broken framing under a valid boundary is the parser's own error type."""
    for body in (b"garbage", b"--xyz\r\nnot-a-header\r\n\r\nbody\r\n--xyz--\r\n"):
        response = client.post(
            "/api/audio/transcriptions",
            headers={**test_user_headers, "content-type": "multipart/form-data; boundary=xyz"},
            content=body,
        )
        assert response.status_code == 422, response.text
    assert upstream.requests == []


def test_transcription_caps_a_body_sent_without_content_length(client, test_user_headers, upstream, audio_on, monkeypatch):
    """A chunked upload declares no size, so the cap has to act on the stream."""
    monkeypatch.setattr(audio_on, "audio_stt_max_audio_bytes", 1024)

    def chunks():
        yield b"--xyz\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.webm\"\r\n"
        yield b"Content-Type: audio/webm\r\n\r\n" + WEBM
        for _ in range(64):
            yield b"\x00" * 4096
        yield b"\r\n--xyz--\r\n"

    response = client.post(
        "/api/audio/transcriptions",
        headers={**test_user_headers, "content-type": "multipart/form-data; boundary=xyz"},
        content=chunks(),
    )
    assert "content-length" not in {k.lower() for k in response.request.headers}
    assert response.status_code == 413
    assert "1 KB limit" in response.json()["detail"]
    assert upstream.requests == []


def test_concurrent_relays_are_bounded(client, test_user_headers, upstream, audio_on, monkeypatch):
    from atlas.routes import audio_routes

    monkeypatch.setattr(audio_on, "audio_max_concurrent_requests", 2, raising=False)
    monkeypatch.setattr(audio_routes._relay_slots, "in_flight", 2)
    for response in (post_clip(client, test_user_headers), post_speech(client, test_user_headers)):
        assert response.status_code == 503
        assert response.headers["retry-after"] == "2"
    assert upstream.requests == []

    # A slot is given back whether the relay succeeds or fails.
    monkeypatch.setattr(audio_routes._relay_slots, "in_flight", 0)
    assert post_clip(client, test_user_headers).status_code == 200
    upstream.handler = lambda request: httpx.Response(500)
    assert post_clip(client, test_user_headers).status_code == 502
    assert post_clip(client, test_user_headers, content=b"not audio").status_code == 415
    assert audio_routes._relay_slots.in_flight == 0


def test_a_body_that_trickles_in_gives_its_slot_back(client, test_user_headers, upstream, audio_on, monkeypatch):
    """A relay slot is held while the body is read, so reading has a deadline."""
    from atlas.routes import audio_routes

    async def stalled(request, limit, detail):
        await asyncio.sleep(5)
        yield b""

    monkeypatch.setattr(audio_routes, "_capped_stream", stalled)
    monkeypatch.setattr(audio_on, "audio_stt_timeout_seconds", 0.05)
    monkeypatch.setattr(audio_on, "audio_tts_timeout_seconds", 0.05)
    assert post_clip(client, test_user_headers).status_code == 408
    assert post_speech(client, test_user_headers).status_code == 408
    assert audio_routes._relay_slots.in_flight == 0
    assert upstream.requests == []


@pytest.mark.parametrize(
    "upstream_status,expected",
    [(401, 502), (403, 502), (404, 502), (400, 422), (413, 413), (429, 503), (500, 502), (302, 502)],
)
def test_transcription_maps_provider_errors_without_echoing_them(client, test_user_headers, upstream, audio_on, upstream_status, expected):
    upstream.handler = lambda request: httpx.Response(
        upstream_status, json={"error": "leaky upstream detail"}, headers={"location": "http://elsewhere.test/"}
    )
    response = post_clip(client, test_user_headers)
    assert response.status_code == expected
    assert "leaky" not in response.text
    assert len(upstream.requests) == 1  # a redirect is not followed


@pytest.mark.parametrize("reply", [b"not json", b'["text"]', b'{"text": 5}', b'{"other": "x"}'])
def test_transcription_rejects_malformed_provider_reply(client, test_user_headers, upstream, audio_on, reply):
    upstream.handler = lambda request: httpx.Response(200, content=reply)
    assert post_clip(client, test_user_headers).status_code == 502


def test_transcription_timeout_is_a_504(client, test_user_headers, upstream, audio_on, monkeypatch):
    monkeypatch.setattr(audio_on, "audio_stt_timeout_seconds", 0.05)

    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json={"text": "late"})

    upstream.handler = slow
    assert post_clip(client, test_user_headers).status_code == 504


def test_a_url_the_http_client_rejects_is_a_502_not_a_500(client, test_user_headers, upstream, audio_on):
    def invalid(request):
        raise httpx.InvalidURL("Invalid non-printable ASCII character in URL")

    upstream.handler = invalid
    assert post_clip(client, test_user_headers).status_code == 502
    assert post_speech(client, test_user_headers).status_code == 502


def test_transcription_connection_failure_is_a_502(client, test_user_headers, upstream, audio_on):
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    upstream.handler = refuse
    response = post_clip(client, test_user_headers)
    assert response.status_code == 502
    assert "could not be reached" in response.json()["detail"]


# --- speech -----------------------------------------------------------------


def test_speech_sends_json_and_relays_binary_audio(client, test_user_headers, upstream, audio_on):
    audio = MP3 + bytes(range(256))
    upstream.handler = lambda request: httpx.Response(200, content=audio, headers={"content-type": "audio/mpeg"})
    response = post_speech(client, test_user_headers, text="  Hello there.  ")
    assert response.status_code == 200
    assert response.content == audio
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"

    (request,) = upstream.requests
    assert str(request.url) == f"{TTS_URL}/audio/speech"
    assert request.headers["authorization"] == "Bearer tts-secret-value"
    assert json.loads(upstream.bodies[0]) == {
        "model": "kokoro-test",
        "input": "Hello there.",
        "voice": "af_heart",
        "response_format": "mp3",
    }


def test_speech_ignores_client_supplied_model_and_voice(client, test_user_headers, upstream, audio_on):
    upstream.handler = lambda request: httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
    assert post_speech(client, test_user_headers, model="other-model", voice="other-voice").status_code == 200
    sent = json.loads(upstream.bodies[0])
    assert sent["model"] == "kokoro-test" and sent["voice"] == "af_heart"


def test_speech_enforces_text_limits(client, test_user_headers, upstream, audio_on, monkeypatch):
    monkeypatch.setattr(audio_on, "audio_tts_max_input_chars", 20)
    assert post_speech(client, test_user_headers, text="x" * 21).status_code == 413
    # Far over: refused while the body is read, before it is parsed.
    assert post_speech(client, test_user_headers, text="x" * 50_000).status_code == 413
    assert post_speech(client, test_user_headers, text="   ").status_code == 422
    assert client.post("/api/audio/speech", headers=test_user_headers, json={"text": "wrong key"}).status_code == 422
    response = client.post(
        "/api/audio/speech", headers={**test_user_headers, "content-type": "text/plain"}, content=b"hello"
    )
    assert response.status_code == 415
    assert upstream.requests == []


@pytest.mark.parametrize("character", ["a", "\u4e2d", "\U0001f680"], ids=["ascii", "cjk", "astral"])
@pytest.mark.parametrize("ensure_ascii", [True, False], ids=["escaped", "raw-utf8"])
def test_speech_accepts_text_at_the_limit_however_it_is_encoded(
    client, test_user_headers, upstream, audio_on, monkeypatch, character, ensure_ascii
):
    """The byte cap on the body must not be tighter than the character limit.

    A client may send non-ASCII text raw or as ``\\uXXXX`` escapes (the default
    of Python's ``json.dumps``), which take up to 12 bytes for one character.
    """
    # Large enough that the allowance for the JSON envelope cannot hide a cap
    # that is too tight per character.
    limit = 4096
    monkeypatch.setattr(audio_on, "audio_tts_max_input_chars", limit)
    upstream.handler = lambda request: httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})

    def post(text):
        body = json.dumps({"input": text, "compliance_level": None}, ensure_ascii=ensure_ascii).encode()
        return client.post(
            "/api/audio/speech", headers={**test_user_headers, "content-type": "application/json"}, content=body
        )

    assert post(character * limit).status_code == 200
    assert json.loads(upstream.bodies[-1])["input"] == character * limit
    # One character over is still refused, by the count of decoded characters.
    assert post(character * (limit + 1)).status_code == 413
    assert len(upstream.requests) == 1


def test_speech_labels_generic_replies_and_refuses_non_audio(client, test_user_headers, upstream, audio_on):
    upstream.handler = lambda request: httpx.Response(200, content=MP3, headers={"content-type": "application/octet-stream"})
    response = post_speech(client, test_user_headers)
    assert response.status_code == 200 and response.headers["content-type"] == "audio/mpeg"

    upstream.handler = lambda request: httpx.Response(200, content=b"<html>login</html>", headers={"content-type": "text/html"})
    assert post_speech(client, test_user_headers).status_code == 502

    upstream.handler = lambda request: httpx.Response(200, content=b"", headers={"content-type": "audio/mpeg"})
    assert post_speech(client, test_user_headers).status_code == 502


def test_speech_caps_the_provider_reply(client, test_user_headers, upstream, audio_on, monkeypatch):
    monkeypatch.setattr(audio_on, "audio_tts_max_audio_bytes", 1000)
    upstream.handler = lambda request: httpx.Response(200, content=MP3 + b"\x00" * 5000, headers={"content-type": "audio/mpeg"})
    assert post_speech(client, test_user_headers).status_code == 502


@pytest.mark.parametrize("upstream_status,expected", [(401, 502), (400, 422), (429, 503), (503, 502)])
def test_speech_maps_provider_errors(client, test_user_headers, upstream, audio_on, upstream_status, expected):
    upstream.handler = lambda request: httpx.Response(upstream_status, text="echo: Read this aloud.")
    response = post_speech(client, test_user_headers)
    assert response.status_code == expected
    assert "echo" not in response.text


def test_speech_timeout_is_a_504(client, test_user_headers, upstream, audio_on, monkeypatch):
    monkeypatch.setattr(audio_on, "audio_tts_timeout_seconds", 0.05)

    def timed_out(request):
        raise httpx.ReadTimeout("slow", request=request)

    upstream.handler = timed_out
    assert post_speech(client, test_user_headers).status_code == 504


# --- data classification ----------------------------------------------------


def test_classification_gate(client, test_user_headers, upstream, audio_on, monkeypatch):
    from atlas.core.compliance import get_compliance_manager

    levels = get_compliance_manager().get_all_levels()
    # Not a skip: without two levels this would silently stop testing the gate.
    assert len(levels) >= 2, "the packaged compliance-levels.json defines several levels"
    approved, other = levels[0], levels[1]
    monkeypatch.setattr(audio_on, "feature_compliance_levels_enabled", True)
    monkeypatch.setattr(audio_on, "feature_compliance_level_required", False, raising=False)
    monkeypatch.setattr(audio_on, "audio_stt_allowed_data_classifications", approved)
    monkeypatch.setattr(audio_on, "audio_tts_allowed_data_classifications", "")
    upstream.handler = lambda request: (
        httpx.Response(200, json={"text": "ok"})
        if request.url.path.endswith("transcriptions")
        else httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
    )

    assert post_clip(client, test_user_headers, compliance_level=approved).status_code == 200
    assert post_clip(client, test_user_headers, compliance_level=other).status_code == 403
    assert post_clip(client, test_user_headers, compliance_level="no-such-level").status_code == 403
    # An endpoint that declares nothing is approved for no classified conversation.
    assert post_speech(client, test_user_headers, compliance_level=approved).status_code == 403
    # No active level: nothing to protect.
    assert post_speech(client, test_user_headers).status_code == 200

    shell = client.get("/api/config/shell", headers=test_user_headers).json()["audio"]
    assert shell["stt"]["allowed_data_classifications"] == [approved]
    assert shell["tts"]["allowed_data_classifications"] == []

    # With a level required, a request that names none is refused.
    monkeypatch.setattr(audio_on, "feature_compliance_level_required", True, raising=False)
    assert post_speech(client, test_user_headers).status_code == 403


# --- logging and headers ----------------------------------------------------


def test_payloads_and_credentials_are_not_logged(client, test_user_headers, upstream, audio_on, monkeypatch, caplog):
    monkeypatch.setattr(middleware_settings(), "feature_metrics_logging_enabled", True, raising=False)
    upstream.handler = lambda request: (
        httpx.Response(200, json={"text": "the secret transcript"})
        if request.url.path.endswith("transcriptions")
        else httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
    )
    with caplog.at_level(logging.DEBUG):
        assert post_clip(client, test_user_headers).status_code == 200
        assert post_speech(client, test_user_headers, text="the secret sentence").status_code == 200
        upstream.handler = lambda request: httpx.Response(500, text="the secret sentence")
        assert post_speech(client, test_user_headers, text="the secret sentence").status_code == 502
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "audio_transcription" in logged and "audio_speech" in logged
    for leaked in ("secret transcript", "secret sentence", "stt-secret-value", "tts-secret-value", "private memo"):
        assert leaked not in logged


def test_csp_allows_blob_media_only_while_tts_is_on(client, test_user_headers, monkeypatch):
    shared = middleware_settings()
    monkeypatch.setattr(shared, "security_csp_enabled", True, raising=False)
    monkeypatch.setattr(shared, "security_csp_value", "default-src 'self'; connect-src 'self'", raising=False)

    def csp():
        return client.get("/api/heartbeat", headers=test_user_headers).headers["content-security-policy"]

    monkeypatch.setattr(shared, "feature_audio_tts_enabled", True, raising=False)
    assert "media-src 'self' blob:" in csp()

    monkeypatch.setattr(shared, "feature_audio_tts_enabled", False, raising=False)
    assert "media-src" not in csp()

    # An operator's own media-src is left exactly as written.
    monkeypatch.setattr(shared, "feature_audio_tts_enabled", True, raising=False)
    monkeypatch.setattr(shared, "security_csp_value", "default-src 'self'; media-src 'none'", raising=False)
    assert "media-src 'none'" in csp() and "blob:" not in csp()
