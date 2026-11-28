"""Google Gemini speech-to-text and text-to-speech (issue #1255; provider ``gemini``).

Gemini has no ``/audio/*`` routes, including on its OpenAI-compatible
endpoint. Both capabilities use ``POST {base_url}/models/{model}:generateContent``
with the key in ``x-goog-api-key`` (``AUDIO_*_API_KEY``, else
``GOOGLE_API_KEY`` / ``GEMINI_API_KEY``):

* Transcription sends the clip as base64 ``inlineData`` next to a fixed
  instruction to transcribe it verbatim, and returns the text parts of the
  reply.
* Speech asks for ``responseModalities: ["AUDIO"]`` (with the configured
  prebuilt voice, if any) and receives base64 raw 16-bit PCM, which is framed
  as WAV so the browser can play it.

All HTTP goes through :mod:`atlas.modules.audio.audio_http`.
"""

from __future__ import annotations

import base64
from typing import Any, Optional, Tuple
from urllib.parse import quote

from atlas.modules.audio import audio_http
from atlas.modules.audio.audio_config import SttConfig, TtsConfig
from atlas.modules.audio.audio_formats import pcm_sample_rate, pcm_to_wav

_TRANSCRIBE_PROMPT = (
    "Generate a verbatim transcript of the speech in this audio. Reply with the "
    "transcript text only: no commentary, labels, timestamps or quotation marks. "
    "If there is no speech, reply with nothing."
)


def _url(config) -> str:
    model = config.model[len("models/"):] if config.model.startswith("models/") else config.model
    return f"{config.base_url}/models/{quote(model, safe='-._~')}:generateContent"


def _headers(config) -> dict:
    return {"x-goog-api-key": config.api_key or ""}


def _first_candidate_parts(payload: Any) -> list:
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        # No candidate: the request was blocked or the reply is malformed.
        return []
    content = candidates[0].get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    return [part for part in parts if isinstance(part, dict)] if isinstance(parts, list) else []


async def transcribe(
    config: SttConfig,
    *,
    content: bytes,
    filename: str,
    content_type: str,
    language: Optional[str] = None,
) -> str:
    prompt = _TRANSCRIBE_PROMPT
    if language:
        prompt += f" The speech is in the language with code '{language}'."
    request = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": content_type, "data": base64.b64encode(content).decode("ascii")}},
                ],
            }
        ],
        "generationConfig": {"temperature": 0},
    }
    body, _ = await audio_http.post(
        config.name,
        _url(config),
        timeout_seconds=config.timeout_seconds,
        response_limit=audio_http.MAX_TRANSCRIPTION_RESPONSE_BYTES,
        headers=_headers(config),
        json=request,
    )
    not_transcript = "The speech service returned a reply that is not a transcript."
    payload = audio_http.parse_json_reply(body, not_transcript)
    parts = _first_candidate_parts(payload)
    texts = [part["text"] for part in parts if isinstance(part.get("text"), str)]
    if not texts:
        raise audio_http.bad_response(not_transcript)
    return "".join(texts).strip()


async def synthesize(config: TtsConfig, text: str) -> Tuple[bytes, str]:
    # Gemini takes delivery direction in the prompt itself ("Say cheerfully: ...").
    prompt = f"{config.instructions}: {text}" if config.instructions else text
    generation_config: dict = {"responseModalities": ["AUDIO"]}
    if config.voice:
        generation_config["speechConfig"] = {
            "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": config.voice}}
        }
    body, _ = await audio_http.post(
        config.name,
        _url(config),
        timeout_seconds=config.timeout_seconds,
        response_limit=audio_http.base64_reply_limit(config.max_audio_bytes),
        headers=_headers(config),
        json={"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": generation_config},
    )
    payload = audio_http.parse_json_reply(body)
    for part in _first_candidate_parts(payload):
        inline = part.get("inlineData") or part.get("inline_data")
        if not isinstance(inline, dict):
            continue
        mime_type = str(inline.get("mimeType") or inline.get("mime_type") or "")
        audio = audio_http.decode_base64_audio(inline.get("data"), config.max_audio_bytes)
        rate = pcm_sample_rate(mime_type or "audio/L16")
        if rate is not None:
            wav = pcm_to_wav(audio, rate)
            if len(wav) > config.max_audio_bytes:
                raise audio_http.response_too_large()
            return wav, "audio/wav"
        media_type = mime_type.split(";")[0].strip().lower()
        if media_type.startswith("audio/"):
            return audio, media_type
        raise audio_http.bad_response()
    raise audio_http.bad_response("The speech service returned no audio.")
