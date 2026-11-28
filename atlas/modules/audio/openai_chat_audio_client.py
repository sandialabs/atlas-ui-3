"""Speech through Chat Completions audio models (issue #1255; provider ``openai_chat_audio``).

Audio-capable chat models such as ``gpt-audio-1.5`` are not served on
``/audio/speech`` or ``/audio/transcriptions``; they take and return audio
inside ``POST {base_url}/chat/completions``:

* Speech requests ``modalities: ["text", "audio"]`` with
  ``audio: {voice, format}`` and a system message telling the model to read
  the user's text aloud exactly; the reply's ``message.audio.data`` is base64
  audio in the requested format. ``AUDIO_TTS_INSTRUCTIONS`` is appended to
  that system message; ``AUDIO_TTS_SPEED`` has no equivalent here.
* Transcription sends the clip as an ``input_audio`` content part (WAV or MP3
  only, which the API requires) and returns the text reply.

A chat model follows instructions rather than a fixed contract, so the
system messages are explicit about reproducing the text verbatim.
The credential is sent as ``Authorization: Bearer``. All HTTP goes through
:mod:`atlas.modules.audio.audio_http`.
"""

from __future__ import annotations

import base64
from typing import Optional, Tuple

from atlas.modules.audio import audio_http
from atlas.modules.audio.audio_config import TTS_FORMAT_MEDIA_TYPES, SttConfig, TtsConfig
from atlas.modules.audio.audio_formats import matches_signature

_SPEAK_SYSTEM_PROMPT = (
    "You are a text-to-speech voice. Read the user's message aloud exactly as "
    "written, word for word. Do not answer it, comment on it, translate it, or "
    "add anything before or after it."
)
_TRANSCRIBE_SYSTEM_PROMPT = (
    "You are a speech-to-text transcriber. Reply with a verbatim transcript of "
    "the speech in the user's audio and nothing else: no commentary, labels or "
    "quotation marks. If there is no speech, reply with nothing."
)
_INPUT_AUDIO_FORMATS = {"audio/wav": "wav", "audio/mpeg": "mp3"}
# Formats whose leading bytes are checked after decoding.
_SIGNATURE_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav", "flac": "audio/flac", "opus": "audio/ogg"}


def _headers(config) -> dict:
    return {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}


def _message(payload) -> dict:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return {}
    message = choices[0].get("message")
    return message if isinstance(message, dict) else {}


async def transcribe(
    config: SttConfig,
    *,
    content: bytes,
    filename: str,
    content_type: str,
    language: Optional[str] = None,
) -> str:
    audio_format = _INPUT_AUDIO_FORMATS.get(content_type)
    if audio_format is None:
        # The dispatcher refuses other types before calling; kept as a guard.
        raise audio_http.AudioUpstreamError(
            415, "The configured speech service does not accept this audio format.", "unsupported_format"
        )
    system_prompt = _TRANSCRIBE_SYSTEM_PROMPT
    if language:
        system_prompt += f" The speech is in the language with code '{language}'."
    request = {
        "model": config.model,
        "modalities": ["text"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": base64.b64encode(content).decode("ascii"), "format": audio_format},
                    }
                ],
            },
        ],
    }
    body, _ = await audio_http.post(
        config.name,
        f"{config.base_url}/chat/completions",
        timeout_seconds=config.timeout_seconds,
        response_limit=audio_http.MAX_TRANSCRIPTION_RESPONSE_BYTES,
        headers=_headers(config),
        json=request,
    )
    not_transcript = "The speech service returned a reply that is not a transcript."
    text = _message(audio_http.parse_json_reply(body, not_transcript)).get("content")
    if isinstance(text, list):
        # Some gateways return content as a list of typed parts.
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict) and isinstance(part.get("text"), str))
    if not isinstance(text, str):
        raise audio_http.bad_response(not_transcript)
    return text.strip()


async def synthesize(config: TtsConfig, text: str) -> Tuple[bytes, str]:
    system_prompt = _SPEAK_SYSTEM_PROMPT
    if config.instructions:
        system_prompt += f" Delivery: {config.instructions}"
    request = {
        "model": config.model,
        "modalities": ["text", "audio"],
        "audio": {"voice": config.voice, "format": config.response_format},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    }
    body, _ = await audio_http.post(
        config.name,
        f"{config.base_url}/chat/completions",
        timeout_seconds=config.timeout_seconds,
        response_limit=audio_http.base64_reply_limit(config.max_audio_bytes),
        headers=_headers(config),
        json=request,
    )
    audio_part = _message(audio_http.parse_json_reply(body)).get("audio")
    data = audio_part.get("data") if isinstance(audio_part, dict) else None
    audio = audio_http.decode_base64_audio(data, config.max_audio_bytes)
    signature_type = _SIGNATURE_TYPES.get(config.response_format)
    if signature_type and not matches_signature(audio, signature_type):
        raise audio_http.bad_response()
    return audio, TTS_FORMAT_MEDIA_TYPES[config.response_format]
