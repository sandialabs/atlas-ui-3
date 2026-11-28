"""HTTP plumbing shared by every direct audio adapter (issues #1251, #1255).

Every adapter that talks to an audio service over HTTP goes through
:func:`post`, so all of them get the same guarantees:

* one short-lived client per request that does not follow redirects (the
  operator configured one endpoint, and a redirect would send the audio and
  the credential somewhere else);
* the configured timeout as a ceiling on the whole exchange, slow trickles
  included;
* the reply read with a byte cap, refusing a declared or actual overrun;
* failures reported by upstream status only. An error body is never read,
  logged or forwarded, because it can echo the text or transcript that was
  sent.

Nothing here logs, stores or returns payloads. Audio is held in memory for
the length of one request and is never written to disk by this module.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from typing import Any, Dict, Optional, Tuple

import httpx

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


def bad_response(detail: str = "The speech service returned a reply that is not audio.") -> AudioUpstreamError:
    return AudioUpstreamError(502, detail, "upstream_bad_response")


def response_too_large() -> AudioUpstreamError:
    return AudioUpstreamError(502, "The speech service returned more data than is allowed.", "upstream_response_too_large")


def timed_out() -> AudioUpstreamError:
    return AudioUpstreamError(504, "The speech service did not answer in time.", "timeout")


def unreachable() -> AudioUpstreamError:
    return AudioUpstreamError(502, "The speech service could not be reached.", "unreachable")


def build_http_client(timeout_seconds: float) -> httpx.AsyncClient:
    """One short-lived client per request, never following redirects."""
    timeout = httpx.Timeout(timeout_seconds, connect=min(_CONNECT_TIMEOUT_SECONDS, timeout_seconds))
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False)


def error_for_status(status: int) -> AudioUpstreamError:
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
        raise response_too_large()
    chunks = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise response_too_large()
        chunks.append(chunk)
    return b"".join(chunks)


async def post(
    capability: str,
    url: str,
    *,
    timeout_seconds: float,
    response_limit: int,
    headers: Optional[Dict[str, str]] = None,
    **request_kwargs: Any,
) -> Tuple[bytes, httpx.Headers]:
    """POST to ``url`` under one overall deadline; returns the capped body."""

    async def run() -> Tuple[bytes, httpx.Headers]:
        # Looked up on the module at call time so tests can install a mock
        # transport for every adapter at once.
        async with build_http_client(timeout_seconds) as client:
            async with client.stream("POST", url, headers=headers or {}, **request_kwargs) as response:
                if response.status_code != 200:
                    raise error_for_status(response.status_code)
                return await _read_capped(response, response_limit), response.headers

    try:
        # httpx timeouts are per phase; the wrapper makes the configured value
        # a ceiling on the whole exchange.
        return await asyncio.wait_for(run(), timeout=timeout_seconds)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        raise timed_out() from None
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # InvalidURL is not an HTTPError: a base URL that passed the settings
        # check but that httpx still will not request. Only the exception
        # type is logged; its message can contain the URL.
        logger.warning("Audio %s request failed: %s", capability, type(exc).__name__)
        raise unreachable() from None


def audio_media_type(headers: httpx.Headers, expected: str) -> str:
    """The media type to relay a binary audio reply as.

    The reply goes to a browser, so it must be audio. A server that labels
    it generically (or not at all) gets the type of the format that was asked
    for; anything else -- an HTML error page served with a 200, say -- is
    refused rather than forwarded.
    """
    upstream_type = headers.get("content-type", "").split(";")[0].strip().lower()
    if upstream_type.startswith("audio/"):
        return upstream_type
    if upstream_type in ("", "application/octet-stream"):
        return expected
    raise bad_response()


# --- JSON replies that carry base64 audio (issue #1255) -------------------

# Room for the JSON envelope around a base64 audio payload.
_JSON_ENVELOPE_BYTES = 64 * 1024


def base64_reply_limit(max_audio_bytes: int) -> int:
    """The byte cap for a JSON reply whose audio is base64 encoded."""
    return (max_audio_bytes * 4) // 3 + 4 + _JSON_ENVELOPE_BYTES


def parse_json_reply(body: bytes, detail: str = "The speech service returned a reply that is not audio.") -> Any:
    try:
        return json.loads(body)
    except ValueError:
        raise bad_response(detail) from None


def decode_base64_audio(data: Any, max_audio_bytes: int) -> bytes:
    """Decode a base64 audio payload, refusing one that is too large or malformed."""
    if not isinstance(data, str) or not data:
        raise bad_response("The speech service returned no audio.")
    if (len(data) * 3) // 4 > max_audio_bytes + 3:
        raise response_too_large()
    try:
        audio = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise bad_response() from None
    if len(audio) > max_audio_bytes:
        raise response_too_large()
    if not audio:
        raise bad_response("The speech service returned no audio.")
    return audio
