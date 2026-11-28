#!/usr/bin/env python3
"""Mock OpenAI-compatible audio server (issue #1251).

Implements the two routes Atlas's dictation and read-aloud features call, so
they can be exercised without a speech model:

  POST /v1/audio/transcriptions   multipart upload -> {"text": "..."}
  POST /v1/audio/speech           JSON -> a short WAV tone

Provider-native routes for the issue #1255 adapters (same canned replies):

  POST /v1beta/models/{model}:generateContent   Gemini: transcript text, or base64 raw PCM
                                                when responseModalities is ["AUDIO"]
  POST /v1/speech-to-text                       ElevenLabs: multipart -> {"text": "..."}
  POST /v1/text-to-speech/{voice_id}            ElevenLabs: WAV tone, or raw PCM for pcm_* output_format
  POST /v1/chat/completions                     Chat Completions audio: transcript text, or
                                                base64 WAV in message.audio when "audio" is a modality

  GET  /health                    -> {"status": "ok"}
  GET  /mock/requests             -> what was received (fields, sizes, which known credential; no audio or text)
  DELETE /mock/requests

Environment:
  MOCK_AUDIO_PORT            port to listen on (default 8094)
  MOCK_AUDIO_API_KEY         when set, every audio route requires that key ("Authorization: Bearer <key>",
                             or "x-goog-api-key" / "xi-api-key" on the provider-native routes)
  MOCK_AUDIO_KEY_LABELS      "name=key,name=key": the request log names which of these arrived
  MOCK_AUDIO_DELAY_SECONDS   wait this long before answering (to exercise timeouts)
  MOCK_AUDIO_TRANSCRIPT      the text every transcription returns

The speech route always answers with WAV (``audio/wav``), whatever
``response_format`` asks for: a tone is easy to synthesize without an encoder
and every browser plays it. Set ``AUDIO_TTS_RESPONSE_FORMAT=wav`` in Atlas
when pointing it here.

Run:  python mocks/openai-audio-mock/main.py
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import io
import math
import os
import struct
import wave
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, Response

SAMPLE_RATE = 16000
_requests: List[Dict[str, Any]] = []


def tone_wav(seconds: float = 1.0, frequency: float = 440.0) -> bytes:
    """A mono 16-bit sine tone as a complete WAV file."""
    frames = int(SAMPLE_RATE * seconds)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(
            b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * frequency * i / SAMPLE_RATE)))
                for i in range(frames)
            )
        )
    return buffer.getvalue()


def _credential_label(request: Request) -> Optional[str]:
    """Which known credential arrived: a label, "unrecognized", or None.

    The request log is readable without a credential, so it never holds the
    credential or anything derived from it (a hash of a short key can be
    guessed offline). A test names the keys it expects in
    ``MOCK_AUDIO_KEY_LABELS`` ("stt=<key>,tts=<key>") and reads back the label.
    """
    presented = _presented_key(request)
    if presented is None:
        return None
    for pair in os.environ.get("MOCK_AUDIO_KEY_LABELS", "").split(","):
        label, _, key = pair.partition("=")
        if key and hmac.compare_digest(presented, key):
            return label.strip()
    return "unrecognized"


def _presented_key(request: Request) -> Optional[str]:
    """The key a request carries, in whichever header its provider uses."""
    header = request.headers.get("authorization")
    if header:
        return header[len("Bearer "):] if header.startswith("Bearer ") else header
    return request.headers.get("x-goog-api-key") or request.headers.get("xi-api-key")


def _authorize(request: Request) -> None:
    expected = os.environ.get("MOCK_AUDIO_API_KEY")
    if expected and _presented_key(request) != expected:
        raise HTTPException(status_code=401, detail="invalid api key")


def _transcript(byte_count: int) -> str:
    return os.environ.get("MOCK_AUDIO_TRANSCRIPT") or f"This is a mock transcription of {byte_count} bytes of audio."


def _tone_seconds(text: str) -> float:
    return min(10.0, 1.0 + len(text) / 40)


def _pcm(seconds: float) -> bytes:
    """Raw 16-bit PCM samples: a WAV tone without its 44-byte header."""
    return tone_wav(seconds)[44:]


async def _delay() -> None:
    seconds = float(os.environ.get("MOCK_AUDIO_DELAY_SECONDS", "0") or 0)
    if seconds > 0:
        await asyncio.sleep(seconds)


def get_app() -> FastAPI:
    app = FastAPI(title="Mock OpenAI-compatible audio server", version="1.0.0")

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/mock/requests")
    async def list_requests() -> Dict[str, Any]:
        return {"requests": _requests}

    @app.delete("/mock/requests")
    async def clear_requests() -> Dict[str, Any]:
        _requests.clear()
        return {"requests": []}

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request) -> Dict[str, str]:
        _authorize(request)
        form = await request.form()
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise HTTPException(status_code=400, detail="file is required")
        content = await upload.read()
        _requests.append(
            {
                "route": "transcriptions",
                "model": form.get("model"),
                "language": form.get("language"),
                "filename": upload.filename,
                "content_type": upload.content_type,
                "bytes": len(content),
                "credential": _credential_label(request),
            }
        )
        await _delay()
        return {"text": _transcript(len(content))}

    @app.post("/v1/audio/speech")
    async def speech(request: Request) -> Response:
        _authorize(request)
        body = await request.json()
        text = body.get("input")
        if not isinstance(text, str) or not text:
            raise HTTPException(status_code=400, detail="input is required")
        _requests.append(
            {
                "route": "speech",
                "model": body.get("model"),
                "voice": body.get("voice"),
                "response_format": body.get("response_format"),
                "input_chars": len(text),
                # Names only (never values) of optional fields such as
                # instructions and speed, so a test can tell they were sent.
                "extra_fields": sorted(set(body) - {"model", "input", "voice", "response_format"}),
                "credential": _credential_label(request),
            }
        )
        await _delay()
        # Longer text, longer tone: enough to try pause, stop and speed.
        return Response(content=tone_wav(seconds=_tone_seconds(text)), media_type="audio/wav")

    _add_provider_routes(app)
    return app


def _add_provider_routes(app: FastAPI) -> None:
    """Gemini, ElevenLabs and Chat Completions audio routes (issue #1255)."""

    @app.post("/v1beta/models/{model_action}")
    async def gemini_generate_content(model_action: str, request: Request) -> Dict[str, Any]:
        _authorize(request)
        model, _, action = model_action.partition(":")
        if action != "generateContent":
            raise HTTPException(status_code=404, detail="unknown action")
        body = await request.json()
        parts = body["contents"][0]["parts"]
        generation = body.get("generationConfig") or {}
        await _delay()
        if generation.get("responseModalities") == ["AUDIO"]:
            text = "".join(part.get("text", "") for part in parts)
            voice = (((generation.get("speechConfig") or {}).get("voiceConfig") or {}).get("prebuiltVoiceConfig") or {}).get("voiceName")
            _requests.append({"route": "gemini_speech", "model": model, "voice": voice, "input_chars": len(text), "credential": _credential_label(request)})
            pcm = _pcm(_tone_seconds(text))
            inline = {"mimeType": f"audio/L16;codec=pcm;rate={SAMPLE_RATE}", "data": base64.b64encode(pcm).decode()}
            return {"candidates": [{"content": {"role": "model", "parts": [{"inlineData": inline}]}}]}
        audio = next((part["inlineData"] for part in parts if "inlineData" in part), None)
        if audio is None:
            raise HTTPException(status_code=400, detail="no audio part")
        size = len(base64.b64decode(audio["data"]))
        _requests.append({"route": "gemini_transcription", "model": model, "mime_type": audio.get("mimeType"), "bytes": size, "credential": _credential_label(request)})
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": _transcript(size)}]}}]}

    @app.post("/v1/speech-to-text")
    async def elevenlabs_speech_to_text(request: Request) -> Dict[str, Any]:
        _authorize(request)
        form = await request.form()
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise HTTPException(status_code=400, detail="file is required")
        content = await upload.read()
        _requests.append({"route": "elevenlabs_transcription", "model": form.get("model_id"), "language": form.get("language_code"), "bytes": len(content), "credential": _credential_label(request)})
        await _delay()
        return {"text": _transcript(len(content)), "language_code": form.get("language_code") or "en"}

    @app.post("/v1/text-to-speech/{voice_id}")
    async def elevenlabs_text_to_speech(voice_id: str, request: Request) -> Response:
        _authorize(request)
        body = await request.json()
        text = body.get("text") or ""
        output_format = request.query_params.get("output_format", "mp3_44100_128")
        _requests.append({"route": "elevenlabs_speech", "model": body.get("model_id"), "voice": voice_id, "output_format": output_format, "input_chars": len(text), "credential": _credential_label(request)})
        await _delay()
        if output_format.startswith("pcm_"):
            return Response(content=_pcm(_tone_seconds(text)), media_type="application/octet-stream")
        return Response(content=tone_wav(_tone_seconds(text)), media_type="audio/wav")

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Dict[str, Any]:
        _authorize(request)
        body = await request.json()
        messages = body.get("messages") or []
        await _delay()
        if "audio" in (body.get("modalities") or []):
            text = next((m.get("content") for m in messages if m.get("role") == "user"), "") or ""
            audio = body.get("audio") or {}
            _requests.append({"route": "chat_speech", "model": body.get("model"), "voice": audio.get("voice"), "format": audio.get("format"), "input_chars": len(text), "credential": _credential_label(request)})
            data = base64.b64encode(tone_wav(_tone_seconds(text))).decode()
            message = {"role": "assistant", "content": None, "audio": {"id": "audio_mock", "data": data, "transcript": ""}}
        else:
            part = next(
                (p for m in messages if isinstance(m.get("content"), list) for p in m["content"] if p.get("type") == "input_audio"),
                None,
            )
            if part is None:
                raise HTTPException(status_code=400, detail="no input_audio part")
            size = len(base64.b64decode(part["input_audio"]["data"]))
            _requests.append({"route": "chat_transcription", "model": body.get("model"), "format": part["input_audio"].get("format"), "bytes": size, "credential": _credential_label(request)})
            message = {"role": "assistant", "content": _transcript(size)}
        return {"id": "chatcmpl-mock", "object": "chat.completion", "choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}


app = get_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("MOCK_AUDIO_PORT", "8094")))
