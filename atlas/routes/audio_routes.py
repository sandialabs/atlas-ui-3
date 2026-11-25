"""Dictation and read-aloud endpoints (issue #1251).

``POST /api/audio/transcriptions`` takes a recorded clip and returns its
text; ``POST /api/audio/speech`` takes text and returns audio. Both relay to
the OpenAI-compatible endpoint the operator configured for that capability
(``atlas.modules.audio``); the browser never talks to the audio server and
never sees its address or credential.

Policy applied to every request, in this order:

1. Authentication (``AuthMiddleware``; the routes sit behind it like any
   other ``/api`` route) and the global rate limit.
2. Availability: a capability that is off, incompletely configured, or
   restricted to groups the user is not in answers 404, so a restricted
   capability is indistinguishable from a disabled one.
3. Data classification: with compliance levels on, the conversation's active
   level must be one the endpoint is approved for (the rule models and MCP
   servers follow, issue #1032).
4. Limits: request size is capped while the body is read, not after.

Audio and text are held in memory for one request and are not written to
disk, cached, or logged; the metrics line records sizes and timings only.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from atlas.core.compliance import get_compliance_manager
from atlas.core.log_sanitizer import get_current_user
from atlas.core.metrics_logger import log_metric
from atlas.infrastructure.app_factory import app_factory
from atlas.modules.audio.audio_config import (
    AudioCapabilityConfig,
    resolve_stt_config,
    resolve_tts_config,
    user_may_use,
)
from atlas.modules.audio.audio_formats import (
    extension_for,
    matches_signature,
    normalize_media_type,
)
from atlas.modules.audio.openai_audio_client import (
    AudioUpstreamError,
    synthesize,
    transcribe,
)

logger = logging.getLogger(__name__)

audio_router = APIRouter(prefix="/api/audio", tags=["audio"])

# Room for the multipart boundaries and the small text fields around the clip.
_MULTIPART_OVERHEAD_BYTES = 64 * 1024
# Room for the JSON envelope around the text; 4 bytes covers any UTF-8 character.
_JSON_OVERHEAD_BYTES = 4 * 1024
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
_MAX_LEVEL_NAME_CHARS = 200


class SpeechRequest(BaseModel):
    """Body of ``POST /api/audio/speech``."""

    input: str
    compliance_level: Optional[str] = None


def _settings():
    return app_factory.get_config_manager().app_settings


async def _require_capability(config: AudioCapabilityConfig, user: str) -> None:
    if not await user_may_use(config, user):
        raise HTTPException(status_code=404, detail="Feature not enabled")


def _check_classification(config: AudioCapabilityConfig, settings, level: Optional[str]) -> None:
    """Refuse a request whose conversation level the endpoint is not approved for."""
    if not getattr(settings, "feature_compliance_levels_enabled", False):
        return
    level = (level or "").strip() or None
    if level is None:
        if getattr(settings, "compliance_level_required_effective", False):
            raise HTTPException(status_code=403, detail="A security level is required for audio requests.")
        return
    if len(level) > _MAX_LEVEL_NAME_CHARS or not get_compliance_manager().classification_permits(
        level, list(config.allowed_data_classifications)
    ):
        raise HTTPException(
            status_code=403,
            detail="This audio service is not approved for the conversation's security level.",
        )


def _reject_declared_oversize(request: Request, limit: int, detail: str) -> None:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail=detail)


async def _capped_stream(request: Request, limit: int, detail: str):
    """The request body, refusing to read past ``limit`` bytes."""
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail=detail)
        yield chunk


def _raise_upstream(exc: AudioUpstreamError, capability: str, user: str) -> None:
    logger.warning(
        "Audio %s request failed: kind=%s upstream_status=%s", capability, exc.kind, exc.upstream_status
    )
    log_metric("error", user, error_type=f"audio_{capability}_{exc.kind}")
    raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@audio_router.post("/transcriptions")
async def transcribe_audio(request: Request, current_user: str = Depends(get_current_user)):
    """Transcribe one recorded clip. Multipart fields: ``file`` (required),
    ``language``, ``duration_seconds`` and ``compliance_level`` (optional)."""
    settings = _settings()
    config = resolve_stt_config(settings)
    await _require_capability(config, current_user)

    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise HTTPException(status_code=415, detail="Send the recording as multipart/form-data.")
    too_large = f"The recording is larger than the {config.max_audio_bytes // (1024 * 1024) or 1} MB limit."
    body_limit = config.max_audio_bytes + _MULTIPART_OVERHEAD_BYTES
    _reject_declared_oversize(request, body_limit, too_large)

    try:
        form = await MultiPartParser(
            request.headers,
            _capped_stream(request, body_limit, too_large),
            max_files=1,
            max_fields=8,
            max_part_size=16 * 1024,
        ).parse()
    except MultiPartException:
        raise HTTPException(status_code=422, detail="The upload could not be read as a multipart form.") from None

    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=422, detail="No audio file was uploaded.")

        level = form.get("compliance_level")
        _check_classification(config, settings, level if isinstance(level, str) else None)

        language = form.get("language")
        language = language.strip() if isinstance(language, str) else ""
        if language and not _LANGUAGE_RE.match(language):
            raise HTTPException(status_code=422, detail="The language must be a code such as 'en' or 'pt-BR'.")

        declared_duration = form.get("duration_seconds")
        if isinstance(declared_duration, str) and declared_duration.strip():
            try:
                seconds = float(declared_duration)
            except ValueError:
                raise HTTPException(status_code=422, detail="duration_seconds must be a number.") from None
            # A small tolerance for the recorder's own timer granularity.
            if not seconds >= 0 or seconds > config.max_duration_seconds + 2:
                raise HTTPException(
                    status_code=413,
                    detail=f"Recordings are limited to {config.max_duration_seconds} seconds.",
                )

        media_type = normalize_media_type(upload.content_type)
        if media_type is None:
            raise HTTPException(status_code=415, detail="This audio format is not supported for dictation.")

        content = await upload.read(config.max_audio_bytes + 1)
        if not content:
            raise HTTPException(status_code=422, detail="The recording is empty.")
        if len(content) > config.max_audio_bytes:
            raise HTTPException(status_code=413, detail=too_large)
        if not matches_signature(content, media_type):
            raise HTTPException(status_code=415, detail="The upload is not audio in the format it claims to be.")

        started = time.monotonic()
        try:
            text = await transcribe(
                config,
                content=content,
                # The client's filename is not forwarded: it is user data the
                # audio server has no use for. The extension tells the server
                # which container to expect.
                filename=f"audio.{extension_for(media_type)}",
                content_type=media_type,
                language=language or None,
            )
        except AudioUpstreamError as exc:
            _raise_upstream(exc, "stt", current_user)
    finally:
        # Starlette spools a large part to a temporary file; closing the form
        # deletes it now instead of whenever the request object is collected.
        await form.close()

    log_metric(
        "audio_transcription",
        current_user,
        audio_bytes=len(content),
        content_type=media_type,
        text_chars=len(text),
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return {"text": text}


@audio_router.post("/speech")
async def synthesize_speech(request: Request, current_user: str = Depends(get_current_user)):
    """Synthesize speech for ``input`` and return the audio bytes."""
    settings = _settings()
    config = resolve_tts_config(settings)
    await _require_capability(config, current_user)

    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        raise HTTPException(status_code=415, detail="Send the text as application/json.")
    too_long = f"The text is longer than the {config.max_input_chars} character limit for read-aloud."
    body_limit = config.max_input_chars * 4 + _JSON_OVERHEAD_BYTES
    _reject_declared_oversize(request, body_limit, too_long)
    raw = b"".join([chunk async for chunk in _capped_stream(request, body_limit, too_long)])
    try:
        payload = SpeechRequest.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(status_code=422, detail="The request body must be JSON with an 'input' string.") from None

    _check_classification(config, settings, payload.compliance_level)

    text = payload.input.strip()
    if not text:
        raise HTTPException(status_code=422, detail="There is no text to read aloud.")
    if len(text) > config.max_input_chars:
        raise HTTPException(status_code=413, detail=too_long)

    started = time.monotonic()
    try:
        audio, media_type = await synthesize(config, text)
    except AudioUpstreamError as exc:
        _raise_upstream(exc, "tts", current_user)

    log_metric(
        "audio_speech",
        current_user,
        text_chars=len(text),
        audio_bytes=len(audio),
        content_type=media_type,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return Response(
        content=audio,
        media_type=media_type,
        headers={
            # Generated per request and never stored by Atlas; keep
            # intermediaries and the browser cache from storing it either.
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )
