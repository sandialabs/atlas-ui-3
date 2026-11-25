#!/usr/bin/env python3
"""Mock OpenAI-compatible audio server (issue #1251).

Implements the two routes Atlas's dictation and read-aloud features call, so
they can be exercised without a speech model:

  POST /v1/audio/transcriptions   multipart upload -> {"text": "..."}
  POST /v1/audio/speech           JSON -> a short WAV tone
  GET  /health                    -> {"status": "ok"}
  GET  /mock/requests             -> what was received (fields, sizes and a hash of the credential; no audio or text)
  DELETE /mock/requests

Environment:
  MOCK_AUDIO_PORT            port to listen on (default 8094)
  MOCK_AUDIO_API_KEY         when set, both audio routes require "Authorization: Bearer <key>"
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
import hashlib
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


def _fingerprint(request: Request) -> Optional[str]:
    """SHA-256 of the Authorization header, or None when there is none.

    The request log is readable without a credential, so it records which
    credential arrived (a test can hash the one it expects) and not the
    credential itself.
    """
    header = request.headers.get("authorization")
    return hashlib.sha256(header.encode()).hexdigest() if header else None


def _authorize(request: Request) -> None:
    expected = os.environ.get("MOCK_AUDIO_API_KEY")
    if expected and request.headers.get("authorization") != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid api key")


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
                "authorization_sha256": _fingerprint(request),
            }
        )
        await _delay()
        text = os.environ.get("MOCK_AUDIO_TRANSCRIPT") or f"This is a mock transcription of {len(content)} bytes of audio."
        return {"text": text}

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
                "authorization_sha256": _fingerprint(request),
            }
        )
        await _delay()
        # Longer text, longer tone: enough to try pause, stop and speed.
        return Response(content=tone_wav(seconds=min(10.0, 1.0 + len(text) / 40)), media_type="audio/wav")

    return app


app = get_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("MOCK_AUDIO_PORT", "8094")))
