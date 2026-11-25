"""Client for OpenAI-compatible audio endpoints (issue #1251).

Speaks exactly two requests: ``POST {base_url}/audio/transcriptions``
(multipart upload, JSON reply) and ``POST {base_url}/audio/speech`` (JSON
request, binary reply). There are no provider-specific branches; any server
or gateway that implements those two routes works, including Speaches and a
LiteLLM proxy.

Nothing here logs, stores or returns payloads: failures are reported with the
upstream status only, because an error body can echo the text or transcript
that was sent. Audio is held in memory for the length of one request and is
never written to disk by this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional, Tuple

import httpx

from atlas.modules.audio.audio_config import (
    TTS_FORMAT_MEDIA_TYPES,
    AudioCapabilityConfig,
    SttConfig,
    TtsConfig,
)

logger = logging.getLogger(__name__)

# A transcript reply is small JSON. The cap only stops a misbehaving endpoint
# from filling memory.
MAX_TRANSCRIPTION_RESPONSE_BYTES = 2 * 1024 * 1024

_CONNECT_TIMEOUT_SECONDS = 10.0


class AudioUpstreamError(Exception):
    """An audio request failed; ``detail`` is safe to show to the user."""

    def __init__(self, status_code: int, detail: str, kind: str, upstream_status: Optional[int] = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.kind = kind
        self.upstream_status = upstream_status


def build_http_client(timeout_seconds: float) -> httpx.AsyncClient:
    """One short-lived client per request.

    Redirects are not followed: the operator configured one endpoint, and a
    redirect would send the audio (and the credential) somewhere else.
    """
    timeout = httpx.Timeout(timeout_seconds, connect=min(_CONNECT_TIMEOUT_SECONDS, timeout_seconds))
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False)


def _headers(config: AudioCapabilityConfig) -> dict:
    return {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}


def _error_for_status(status: int) -> AudioUpstreamError:
    if status in (401, 403):
        return AudioUpstreamError(
            502, "The speech service rejected the configured credentials. Ask an administrator to check the audio settings.",
            "upstream_auth", status,
        )
    if status == 404:
        return AudioUpstreamError(
            502, "The speech service does not offer the configured endpoint or model. Ask an administrator to check the audio settings.",
            "upstream_not_found", status,
        )
    if status == 413:
        return AudioUpstreamError(413, "The speech service refused the request as too large.", "upstream_too_large", status)
    if status in (400, 415, 422):
        return AudioUpstreamError(422, "The speech service could not process this request.", "upstream_rejected", status)
    if status == 429:
        return AudioUpstreamError(503, "The speech service is busy. Try again in a moment.", "upstream_busy", status)
    return AudioUpstreamError(502, "The speech service returned an error.", "upstream_error", status)


async def _read_capped(response: httpx.Response, limit: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise AudioUpstreamError(502, "The speech service returned more data than is allowed.", "upstream_response_too_large")
    chunks = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise AudioUpstreamError(502, "The speech service returned more data than is allowed.", "upstream_response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _post(config: AudioCapabilityConfig, path: str, response_limit: int, **request_kwargs) -> Tuple[bytes, httpx.Headers]:
    """POST to the capability's endpoint under one overall deadline."""
    url = f"{config.base_url}{path}"

    async def run() -> Tuple[bytes, httpx.Headers]:
        async with build_http_client(config.timeout_seconds) as client:
            async with client.stream("POST", url, headers=_headers(config), **request_kwargs) as response:
                if response.status_code != 200:
                    raise _error_for_status(response.status_code)
                return await _read_capped(response, response_limit), response.headers

    try:
        # httpx timeouts are per phase; the wrapper makes the configured value
        # a ceiling on the whole exchange, slow trickles included.
        return await asyncio.wait_for(run(), timeout=config.timeout_seconds)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        raise AudioUpstreamError(504, "The speech service did not answer in time.", "timeout") from None
    except httpx.HTTPError as exc:
        logger.warning("Audio %s request failed: %s", config.name, type(exc).__name__)
        raise AudioUpstreamError(502, "The speech service could not be reached.", "unreachable") from None


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
    body, _ = await _post(
        config,
        "/audio/transcriptions",
        MAX_TRANSCRIPTION_RESPONSE_BYTES,
        data=data,
        files={"file": (filename, content, content_type)},
    )
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        raise AudioUpstreamError(502, "The speech service returned a reply that is not a transcript.", "upstream_bad_response")
    return text


async def synthesize(config: TtsConfig, text: str) -> Tuple[bytes, str]:
    """Synthesize speech for ``text``; returns the audio and its media type."""
    body, headers = await _post(
        config,
        "/audio/speech",
        config.max_audio_bytes,
        json={
            "model": config.model,
            "input": text,
            "voice": config.voice,
            "response_format": config.response_format,
        },
    )
    expected = TTS_FORMAT_MEDIA_TYPES[config.response_format]
    upstream_type = headers.get("content-type", "").split(";")[0].strip().lower()
    # The reply is relayed to a browser, so it must be audio. A server that
    # labels it generically (or not at all) gets the type of the format that
    # was asked for; anything else -- an HTML error page served with a 200,
    # say -- is refused rather than forwarded.
    if upstream_type.startswith("audio/"):
        media_type = upstream_type
    elif upstream_type in ("", "application/octet-stream"):
        media_type = expected
    else:
        raise AudioUpstreamError(502, "The speech service returned a reply that is not audio.", "upstream_bad_response")
    if not body:
        raise AudioUpstreamError(502, "The speech service returned no audio.", "upstream_bad_response")
    return body, media_type
