"""Gemini, ElevenLabs and Chat Completions audio adapters (issue #1255).

The upstream is an ``httpx.MockTransport`` installed through
``audio_http.build_http_client``, so each test sees the exact request the
adapter sends and decides the reply.
"""

import asyncio
import base64
import json
import struct

import httpx
import pytest

from atlas.modules.audio import (
    audio_http,
    elevenlabs_audio_client,
    gemini_audio_client,
    openai_chat_audio_client,
)
from atlas.modules.audio.audio_config import SttConfig, TtsConfig
from atlas.modules.audio.audio_formats import matches_signature, pcm_sample_rate, pcm_to_wav
from atlas.modules.audio.audio_http import AudioUpstreamError

WAV = pcm_to_wav(b"\x01\x00" * 32)
MP3 = b"ID3" + b"\x00" * 64
PCM = b"\x10\x00\x20\x00" * 100


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@pytest.fixture
def upstream(monkeypatch):
    class Upstream:
        def __init__(self):
            self.requests = []
            self.bodies = []
            self.handler = lambda request: httpx.Response(200, json={})

        async def dispatch(self, request):
            self.requests.append(request)
            self.bodies.append(await request.aread())
            result = self.handler(request)
            if asyncio.iscoroutine(result):
                result = await result
            return result

        def json_body(self, index=-1):
            return json.loads(self.bodies[index])

    fake = Upstream()

    def build(timeout_seconds):
        return httpx.AsyncClient(transport=httpx.MockTransport(fake.dispatch), timeout=timeout_seconds, follow_redirects=False)

    monkeypatch.setattr(audio_http, "build_http_client", build)
    return fake


def stt(provider, **overrides):
    values = dict(name="stt", enabled=True, provider=provider, base_url="https://api.test/v1", model="m-stt", api_key="stt-key")
    values.update(overrides)
    return SttConfig(**values)


def tts(provider, **overrides):
    values = dict(
        name="tts", enabled=True, provider=provider, base_url="https://api.test/v1", model="m-tts",
        api_key="tts-key", voice="v1", response_format="mp3", max_audio_bytes=1024 * 1024,
    )
    values.update(overrides)
    return TtsConfig(**values)


async def run_stt(module, config, content=WAV, content_type="audio/wav", language=None):
    return await module.transcribe(config, content=content, filename="audio.wav", content_type=content_type, language=language)


# --- PCM framing -----------------------------------------------------------


def test_pcm_to_wav_writes_a_valid_header():
    wav = pcm_to_wav(PCM, 24000)
    assert matches_signature(wav, "audio/wav")
    riff_size, = struct.unpack("<I", wav[4:8])
    assert riff_size == len(wav) - 8
    channels, rate, byte_rate, align, bits = struct.unpack("<HIIHH", wav[22:36])
    assert (channels, rate, byte_rate, align, bits) == (1, 24000, 48000, 2, 16)
    assert wav[36:40] == b"data" and struct.unpack("<I", wav[40:44])[0] == len(PCM)
    assert wav[44:] == PCM


def test_pcm_to_wav_drops_a_trailing_partial_sample():
    wav = pcm_to_wav(b"\x01\x00\x02", 16000)
    assert struct.unpack("<I", wav[40:44])[0] == 2


@pytest.mark.parametrize(
    "mime,rate",
    [
        ("audio/L16;codec=pcm;rate=24000", 24000),
        ("audio/L16; rate=16000", 16000),
        ("audio/pcm", 24000),
        ("audio/L16;rate=abc", 24000),
        ("audio/L16;rate=99999999", 24000),
        ("audio/mpeg", None),
        ("", None),
    ],
)
def test_pcm_sample_rate(mime, rate):
    assert pcm_sample_rate(mime) == rate


# --- Gemini ------------------------------------------------------------------


def gemini_reply(parts):
    return {"candidates": [{"content": {"role": "model", "parts": parts}}]}


async def test_gemini_transcription_request_and_reply(upstream):
    upstream.handler = lambda r: httpx.Response(200, json=gemini_reply([{"text": " hello "}, {"text": "world"}]))
    text = await run_stt(gemini_audio_client, stt("gemini", model="models/gemini-2.5-flash"), language="en")
    assert text == "hello world"
    request = upstream.requests[0]
    assert str(request.url) == "https://api.test/v1/models/gemini-2.5-flash:generateContent"
    assert request.headers["x-goog-api-key"] == "stt-key"
    assert "authorization" not in request.headers
    parts = upstream.json_body()["contents"][0]["parts"]
    assert "'en'" in parts[0]["text"]
    assert parts[1]["inlineData"] == {"mimeType": "audio/wav", "data": b64(WAV)}


@pytest.mark.parametrize("reply", [{}, {"candidates": []}, gemini_reply([{"inlineData": {}}]), {"promptFeedback": {"blockReason": "SAFETY"}}])
async def test_gemini_transcription_without_text_is_a_bad_response(upstream, reply):
    upstream.handler = lambda r: httpx.Response(200, json=reply)
    with pytest.raises(AudioUpstreamError) as raised:
        await run_stt(gemini_audio_client, stt("gemini"))
    assert raised.value.kind == "upstream_bad_response"


async def test_gemini_speech_frames_pcm_as_wav(upstream):
    upstream.handler = lambda r: httpx.Response(
        200, json=gemini_reply([{"inlineData": {"mimeType": "audio/L16;codec=pcm;rate=24000", "data": b64(PCM)}}])
    )
    audio, media_type = await gemini_audio_client.synthesize(
        tts("gemini", model="gemini-2.5-flash-preview-tts", voice="Kore", instructions="Say cheerfully", response_format="wav"),
        "Have a nice day.",
    )
    assert media_type == "audio/wav"
    assert audio == pcm_to_wav(PCM, 24000)
    body = upstream.json_body()
    assert body["contents"][0]["parts"][0]["text"] == "Say cheerfully: Have a nice day."
    assert body["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert body["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore"
    assert str(upstream.requests[0].url).endswith("/models/gemini-2.5-flash-preview-tts:generateContent")


async def test_gemini_speech_without_voice_omits_speech_config(upstream):
    upstream.handler = lambda r: httpx.Response(200, json=gemini_reply([{"inline_data": {"mime_type": "audio/L16;rate=16000", "data": b64(PCM)}}]))
    audio, _ = await gemini_audio_client.synthesize(tts("gemini", voice="", response_format="wav"), "Hi.")
    assert "speechConfig" not in upstream.json_body()["generationConfig"]
    assert struct.unpack("<I", audio[24:28])[0] == 16000


async def test_gemini_model_name_is_escaped_in_the_path(upstream):
    upstream.handler = lambda r: httpx.Response(200, json=gemini_reply([{"text": "ok"}]))
    await run_stt(gemini_audio_client, stt("gemini", model="../evil?x=1"))
    assert upstream.requests[0].url.raw_path == b"/v1/models/..%2Fevil%3Fx%3D1:generateContent"


@pytest.mark.parametrize(
    "parts,kind",
    [
        ([{"text": "I cannot do that"}], "upstream_bad_response"),
        ([{"inlineData": {"mimeType": "audio/L16", "data": "!!!not base64"}}], "upstream_bad_response"),
        ([{"inlineData": {"mimeType": "text/html", "data": b64(b"<html>")}}], "upstream_bad_response"),
        ([{"inlineData": {"mimeType": "audio/L16", "data": ""}}], "upstream_bad_response"),
    ],
)
async def test_gemini_speech_bad_replies(upstream, parts, kind):
    upstream.handler = lambda r: httpx.Response(200, json=gemini_reply(parts))
    with pytest.raises(AudioUpstreamError) as raised:
        await gemini_audio_client.synthesize(tts("gemini", response_format="wav"), "Hi.")
    assert raised.value.kind == kind


async def test_gemini_speech_over_the_size_cap(upstream):
    upstream.handler = lambda r: httpx.Response(200, json=gemini_reply([{"inlineData": {"mimeType": "audio/L16", "data": b64(PCM * 10)}}]))
    with pytest.raises(AudioUpstreamError) as raised:
        await gemini_audio_client.synthesize(tts("gemini", response_format="wav", max_audio_bytes=len(PCM)), "Hi.")
    assert raised.value.kind == "upstream_response_too_large"


async def test_gemini_non_json_reply_is_a_bad_response(upstream):
    upstream.handler = lambda r: httpx.Response(200, content=b"<html>oops</html>")
    with pytest.raises(AudioUpstreamError) as raised:
        await gemini_audio_client.synthesize(tts("gemini", response_format="wav"), "Hi.")
    assert raised.value.kind == "upstream_bad_response"


# --- ElevenLabs ----------------------------------------------------------------


async def test_elevenlabs_transcription_request(upstream):
    upstream.handler = lambda r: httpx.Response(200, json={"text": "scribed", "language_code": "en"})
    text = await run_stt(elevenlabs_audio_client, stt("elevenlabs", model="scribe_v2"), language="en")
    assert text == "scribed"
    request = upstream.requests[0]
    assert str(request.url) == "https://api.test/v1/speech-to-text"
    assert request.headers["xi-api-key"] == "stt-key"
    assert "authorization" not in request.headers
    body = upstream.bodies[0]
    assert b'name="model_id"\r\n\r\nscribe_v2' in body
    assert b'name="language_code"\r\n\r\nen' in body
    assert b'filename="audio.wav"' in body


async def test_elevenlabs_speech_request_and_binary_reply(upstream):
    upstream.handler = lambda r: httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
    audio, media_type = await elevenlabs_audio_client.synthesize(
        tts("elevenlabs", model="eleven_multilingual_v2", voice="JBFqnCBsd6RMkjVDRZzb", speed=1.1, instructions="ignored"),
        "Hello.",
    )
    assert (audio, media_type) == (MP3, "audio/mpeg")
    request = upstream.requests[0]
    assert request.url.path == "/v1/text-to-speech/JBFqnCBsd6RMkjVDRZzb"
    assert request.url.params["output_format"] == "mp3_44100_128"
    assert request.headers["xi-api-key"] == "tts-key"
    assert upstream.json_body() == {"text": "Hello.", "model_id": "eleven_multilingual_v2", "voice_settings": {"speed": 1.1}}


async def test_elevenlabs_voice_id_cannot_change_the_path(upstream):
    upstream.handler = lambda r: httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
    await elevenlabs_audio_client.synthesize(tts("elevenlabs", voice="../../admin"), "Hi.")
    assert upstream.requests[0].url.raw_path.startswith(b"/v1/text-to-speech/..%2F..%2Fadmin")


async def test_elevenlabs_wav_is_requested_as_pcm_and_framed(upstream):
    upstream.handler = lambda r: httpx.Response(200, content=PCM, headers={"content-type": "application/octet-stream"})
    audio, media_type = await elevenlabs_audio_client.synthesize(tts("elevenlabs", response_format="wav"), "Hi.")
    assert upstream.requests[0].url.params["output_format"] == "pcm_24000"
    assert media_type == "audio/wav" and audio == pcm_to_wav(PCM, 24000)


async def test_elevenlabs_wav_refuses_a_non_audio_reply(upstream):
    upstream.handler = lambda r: httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
    with pytest.raises(AudioUpstreamError):
        await elevenlabs_audio_client.synthesize(tts("elevenlabs", response_format="wav"), "Hi.")


async def test_elevenlabs_speech_without_speed_sends_no_voice_settings(upstream):
    upstream.handler = lambda r: httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
    await elevenlabs_audio_client.synthesize(tts("elevenlabs"), "Hi.")
    assert "voice_settings" not in upstream.json_body()


# --- OpenAI Chat Completions audio -------------------------------------------


def chat_reply(message):
    return {"choices": [{"index": 0, "message": {"role": "assistant", **message}}]}


async def test_chat_audio_speech_request_and_reply(upstream):
    upstream.handler = lambda r: httpx.Response(200, json=chat_reply({"content": None, "audio": {"id": "a1", "data": b64(MP3), "transcript": "x"}}))
    audio, media_type = await openai_chat_audio_client.synthesize(
        tts("openai_chat_audio", model="gpt-audio-1.5", voice="alloy", instructions="Calm tone.", speed=2.0), "Read me."
    )
    assert (audio, media_type) == (MP3, "audio/mpeg")
    request = upstream.requests[0]
    assert str(request.url) == "https://api.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer tts-key"
    body = upstream.json_body()
    assert body["model"] == "gpt-audio-1.5"
    assert body["modalities"] == ["text", "audio"]
    assert body["audio"] == {"voice": "alloy", "format": "mp3"}
    assert body["messages"][0]["role"] == "system" and "Calm tone." in body["messages"][0]["content"]
    assert body["messages"][1] == {"role": "user", "content": "Read me."}
    assert "speed" not in body


@pytest.mark.parametrize(
    "reply",
    [
        chat_reply({"content": "Sure! Here it is."}),
        chat_reply({"audio": {"data": "%%%"}}),
        {"choices": []},
        {"error": {"message": "x"}},
    ],
)
async def test_chat_audio_speech_bad_replies(upstream, reply):
    upstream.handler = lambda r: httpx.Response(200, json=reply)
    with pytest.raises(AudioUpstreamError) as raised:
        await openai_chat_audio_client.synthesize(tts("openai_chat_audio"), "Hi.")
    assert raised.value.kind == "upstream_bad_response"


async def test_chat_audio_speech_checks_the_decoded_signature(upstream):
    upstream.handler = lambda r: httpx.Response(200, json=chat_reply({"audio": {"data": b64(b"<html>not audio</html>")}}))
    with pytest.raises(AudioUpstreamError) as raised:
        await openai_chat_audio_client.synthesize(tts("openai_chat_audio", response_format="wav"), "Hi.")
    assert raised.value.kind == "upstream_bad_response"


async def test_chat_audio_transcription_request_and_reply(upstream):
    upstream.handler = lambda r: httpx.Response(200, json=chat_reply({"content": "  dictated text \n"}))
    text = await run_stt(openai_chat_audio_client, stt("openai_chat_audio", model="gpt-audio-1.5"), content=MP3, content_type="audio/mpeg", language="fr")
    assert text == "dictated text"
    body = upstream.json_body()
    assert body["modalities"] == ["text"]
    assert "'fr'" in body["messages"][0]["content"]
    part = body["messages"][1]["content"][0]
    assert part == {"type": "input_audio", "input_audio": {"data": b64(MP3), "format": "mp3"}}


async def test_chat_audio_transcription_accepts_list_content(upstream):
    upstream.handler = lambda r: httpx.Response(200, json=chat_reply({"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}))
    assert await run_stt(openai_chat_audio_client, stt("openai_chat_audio")) == "ab"


async def test_chat_audio_transcription_refuses_other_formats(upstream):
    with pytest.raises(AudioUpstreamError) as raised:
        await run_stt(openai_chat_audio_client, stt("openai_chat_audio"), content=b"\x1a\x45\xdf\xa3", content_type="audio/webm")
    assert raised.value.status_code == 415
    assert upstream.requests == []


# --- shared error handling across adapters -----------------------------------

ADAPTER_CALLS = [
    ("gemini-stt", lambda: run_stt(gemini_audio_client, stt("gemini"))),
    ("gemini-tts", lambda: gemini_audio_client.synthesize(tts("gemini", response_format="wav"), "secret text")),
    ("elevenlabs-stt", lambda: run_stt(elevenlabs_audio_client, stt("elevenlabs"))),
    ("elevenlabs-tts", lambda: elevenlabs_audio_client.synthesize(tts("elevenlabs"), "secret text")),
    ("chat-stt", lambda: run_stt(openai_chat_audio_client, stt("openai_chat_audio"))),
    ("chat-tts", lambda: openai_chat_audio_client.synthesize(tts("openai_chat_audio"), "secret text")),
]


@pytest.mark.parametrize("label,call", ADAPTER_CALLS, ids=[label for label, _ in ADAPTER_CALLS])
@pytest.mark.parametrize(
    "status,kind,client_status",
    [(401, "upstream_auth", 502), (404, "upstream_not_found", 502), (400, "upstream_rejected", 422), (429, "upstream_busy", 503), (500, "upstream_error", 502)],
)
async def test_upstream_status_mapping_hides_the_error_body(upstream, label, call, status, kind, client_status):
    upstream.handler = lambda r: httpx.Response(status, json={"error": {"message": "echo: secret text"}})
    with pytest.raises(AudioUpstreamError) as raised:
        await call()
    assert (raised.value.kind, raised.value.status_code, raised.value.upstream_status) == (kind, client_status, status)
    assert "secret" not in raised.value.detail


@pytest.mark.parametrize("label,call", ADAPTER_CALLS, ids=[label for label, _ in ADAPTER_CALLS])
async def test_redirects_are_not_followed(upstream, label, call):
    upstream.handler = lambda r: httpx.Response(307, headers={"location": "https://elsewhere.test/"})
    with pytest.raises(AudioUpstreamError):
        await call()
    assert len(upstream.requests) == 1


@pytest.mark.parametrize("label,call", ADAPTER_CALLS, ids=[label for label, _ in ADAPTER_CALLS])
async def test_unreachable_upstream(upstream, label, call):
    def fail(request):
        raise httpx.ConnectError("connection refused to https://api.test/v1?key=stt-key")

    upstream.handler = fail
    with pytest.raises(AudioUpstreamError) as raised:
        await call()
    assert raised.value.kind == "unreachable"


async def test_overall_timeout_applies_to_native_adapters(upstream):
    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json={})

    upstream.handler = slow
    with pytest.raises(AudioUpstreamError) as raised:
        await gemini_audio_client.synthesize(tts("gemini", response_format="wav", timeout_seconds=0.05), "Hi.")
    assert raised.value.kind == "timeout" and raised.value.status_code == 504


async def test_oversized_json_reply_is_refused_while_reading(upstream):
    upstream.handler = lambda r: httpx.Response(200, content=b"x" * (audio_http.MAX_TRANSCRIPTION_RESPONSE_BYTES + 1))
    with pytest.raises(AudioUpstreamError) as raised:
        await run_stt(gemini_audio_client, stt("gemini"))
    assert raised.value.kind == "upstream_response_too_large"


def test_base64_reply_limit_fits_the_largest_allowed_audio():
    limit = audio_http.base64_reply_limit(3000)
    assert limit >= len(b64(b"\x00" * 3000))


# --- capped reads without Content-Length -------------------------------------


def _chunked(total_bytes, chunk_bytes=4096):
    """A reply streamed in chunks with no Content-Length header."""

    async def body():
        sent = 0
        while sent < total_bytes:
            size = min(chunk_bytes, total_bytes - sent)
            sent += size
            yield b"x" * size

    return httpx.Response(200, content=body(), headers={"content-type": "application/json"})


async def test_chunked_reply_over_the_limit_is_refused_while_reading(upstream):
    upstream.handler = lambda r: _chunked(audio_http.MAX_TRANSCRIPTION_RESPONSE_BYTES + 1)
    with pytest.raises(AudioUpstreamError) as raised:
        await run_stt(gemini_audio_client, stt("gemini"))
    assert raised.value.kind == "upstream_response_too_large"


async def test_capped_read_stops_at_the_first_chunk_past_the_limit():
    chunks_read = []

    async def body():
        for index in range(100):
            chunks_read.append(index)
            yield b"x" * 10

    response = httpx.Response(200, content=body())
    assert "content-length" not in response.headers
    with pytest.raises(AudioUpstreamError) as raised:
        await audio_http._read_capped(response, 25)
    assert raised.value.kind == "upstream_response_too_large"
    assert len(chunks_read) == 3


async def test_capped_read_accepts_a_chunked_reply_at_the_limit():
    async def body():
        for _ in range(5):
            yield b"x" * 10

    assert await audio_http._read_capped(httpx.Response(200, content=body()), 50) == b"x" * 50


async def test_default_provider_transcription_cap_applies_to_chunked_replies(upstream):
    from atlas.modules.audio import openai_audio_client

    upstream.handler = lambda r: _chunked(audio_http.MAX_TRANSCRIPTION_RESPONSE_BYTES + 1)
    with pytest.raises(AudioUpstreamError) as raised:
        await run_stt(openai_audio_client, stt("openai_compatible"))
    assert raised.value.kind == "upstream_response_too_large"


async def test_speech_cap_applies_to_chunked_audio(upstream):
    upstream.handler = lambda r: _chunked(2048)
    with pytest.raises(AudioUpstreamError) as raised:
        await elevenlabs_audio_client.synthesize(tts("elevenlabs", max_audio_bytes=1024), "Hello.")
    assert raised.value.kind == "upstream_response_too_large"
