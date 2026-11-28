"""ElevenLabs speech-to-text and text-to-speech (issue #1255; provider ``elevenlabs``).

ElevenLabs' native API differs from OpenAI's: the key goes in ``xi-api-key``
(``AUDIO_*_API_KEY``, else ``ELEVENLABS_API_KEY``), the voice is part of the
path, and fields are ``model_id`` / ``text``:

* ``POST {base_url}/speech-to-text`` -- multipart ``file`` and ``model_id``
  (e.g. ``scribe_v2``), optional ``language_code``; JSON reply with ``text``.
* ``POST {base_url}/text-to-speech/{voice_id}?output_format=...`` -- JSON
  ``text`` and ``model_id``; binary audio reply. ``wav`` is requested as raw
  PCM and framed as WAV here, which every ElevenLabs plan offers.

``AUDIO_TTS_SPEED`` is sent as ``voice_settings.speed``;
``AUDIO_TTS_INSTRUCTIONS`` has no ElevenLabs equivalent and is not sent.

All HTTP goes through :mod:`atlas.modules.audio.audio_http`.
"""

from __future__ import annotations

from typing import Optional, Tuple
from urllib.parse import quote

from atlas.modules.audio import audio_http
from atlas.modules.audio.audio_config import SttConfig, TtsConfig
from atlas.modules.audio.audio_formats import pcm_to_wav

_PCM_RATE = 24000

# AUDIO_TTS_RESPONSE_FORMAT -> (ElevenLabs output_format, media type if unlabeled)
_OUTPUT_FORMATS = {
    "mp3": ("mp3_44100_128", "audio/mpeg"),
    "opus": ("opus_48000_128", "audio/ogg"),
    "wav": (f"pcm_{_PCM_RATE}", "audio/wav"),
}


def _headers(config) -> dict:
    return {"xi-api-key": config.api_key or ""}


async def transcribe(
    config: SttConfig,
    *,
    content: bytes,
    filename: str,
    content_type: str,
    language: Optional[str] = None,
) -> str:
    data = {"model_id": config.model}
    if language:
        data["language_code"] = language
    body, _ = await audio_http.post(
        config.name,
        f"{config.base_url}/speech-to-text",
        timeout_seconds=config.timeout_seconds,
        response_limit=audio_http.MAX_TRANSCRIPTION_RESPONSE_BYTES,
        headers=_headers(config),
        data=data,
        files={"file": (filename, content, content_type)},
    )
    not_transcript = "The speech service returned a reply that is not a transcript."
    payload = audio_http.parse_json_reply(body, not_transcript)
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        raise audio_http.bad_response(not_transcript)
    return text


async def synthesize(config: TtsConfig, text: str) -> Tuple[bytes, str]:
    output_format, expected_type = _OUTPUT_FORMATS[config.response_format]
    request: dict = {"text": text, "model_id": config.model}
    if config.speed is not None:
        request["voice_settings"] = {"speed": config.speed}
    body, headers = await audio_http.post(
        config.name,
        f"{config.base_url}/text-to-speech/{quote(config.voice, safe='')}",
        timeout_seconds=config.timeout_seconds,
        response_limit=config.max_audio_bytes,
        headers=_headers(config),
        params={"output_format": output_format},
        json=request,
    )
    if not body:
        raise audio_http.bad_response("The speech service returned no audio.")
    if config.response_format == "wav":
        # Raw PCM: refuse a reply labeled as something else (an error page),
        # then add the header a browser needs.
        label = headers.get("content-type", "").split(";")[0].strip().lower()
        if label and not (label.startswith("audio/") or label == "application/octet-stream"):
            raise audio_http.bad_response()
        wav = pcm_to_wav(body, _PCM_RATE)
        if len(wav) > config.max_audio_bytes:
            raise audio_http.response_too_large()
        return wav, "audio/wav"
    return body, audio_http.audio_media_type(headers, expected_type)
