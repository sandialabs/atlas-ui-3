"""Which uploads count as audio for dictation (issue #1251).

A recording is accepted only when its declared media type is on the list
below *and* its first bytes are what that container starts with. The second
check is what makes the first one mean something: the declared type comes
from the client, so on its own it would let any file be forwarded to the
audio server under an audio label.
"""

from __future__ import annotations

import struct
from typing import Optional

# Declared type -> (canonical type, file extension sent upstream). Covers what
# MediaRecorder produces in current browsers (WebM/Opus, Ogg/Opus, MP4/AAC)
# and the formats the OpenAI transcription API lists.
_MEDIA_TYPES = {
    "audio/webm": ("audio/webm", "webm"),
    "video/webm": ("audio/webm", "webm"),
    "audio/ogg": ("audio/ogg", "ogg"),
    "audio/opus": ("audio/ogg", "ogg"),
    "audio/mp4": ("audio/mp4", "m4a"),
    "audio/x-m4a": ("audio/mp4", "m4a"),
    "audio/m4a": ("audio/mp4", "m4a"),
    "video/mp4": ("audio/mp4", "m4a"),
    "audio/mpeg": ("audio/mpeg", "mp3"),
    "audio/mp3": ("audio/mpeg", "mp3"),
    "audio/wav": ("audio/wav", "wav"),
    "audio/x-wav": ("audio/wav", "wav"),
    "audio/wave": ("audio/wav", "wav"),
    "audio/flac": ("audio/flac", "flac"),
    "audio/x-flac": ("audio/flac", "flac"),
}

_EXTENSIONS = {canonical: extension for canonical, extension in _MEDIA_TYPES.values()}


def normalize_media_type(declared: Optional[str]) -> Optional[str]:
    """The canonical type for a declared one, or None when it is not accepted.

    Parameters are dropped first: MediaRecorder reports
    ``audio/webm;codecs=opus``.
    """
    if not declared:
        return None
    base = declared.split(";")[0].strip().lower()
    entry = _MEDIA_TYPES.get(base)
    return entry[0] if entry else None


def extension_for(media_type: str) -> str:
    return _EXTENSIONS[media_type]


def matches_signature(content: bytes, media_type: str) -> bool:
    """Whether ``content`` starts the way ``media_type`` (canonical) must."""
    head = content[:16]
    if media_type == "audio/webm":
        return head.startswith(b"\x1a\x45\xdf\xa3")  # EBML
    if media_type == "audio/ogg":
        return head.startswith(b"OggS")
    if media_type == "audio/mp4":
        return head[4:8] == b"ftyp"
    if media_type == "audio/wav":
        return head.startswith(b"RIFF") and head[8:12] == b"WAVE"
    if media_type == "audio/flac":
        return head.startswith(b"fLaC")
    if media_type == "audio/mpeg":
        # An ID3 tag, or an MPEG audio frame sync (11 set bits).
        return head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)
    return False


# --- raw PCM from providers (issue #1255) ---------------------------------
#
# Gemini text-to-speech returns bare 16-bit little-endian PCM (declared as
# ``audio/L16;codec=pcm;rate=24000``), and OpenAI Chat Completions audio can
# too (``pcm16``). A browser cannot play headerless PCM, so it is framed as a
# WAV file: a 44-byte RIFF header in front of the unchanged samples.

DEFAULT_PCM_SAMPLE_RATE = 24000
_MAX_SAMPLE_RATE = 384000


def pcm_sample_rate(mime_type: Optional[str], default: int = DEFAULT_PCM_SAMPLE_RATE) -> Optional[int]:
    """The sample rate of a raw PCM media type, or None when it is not raw PCM.

    Accepts ``audio/L16`` and ``audio/pcm`` with an optional ``rate=``
    parameter; a missing or unusable rate falls back to ``default``.
    """
    if not mime_type:
        return None
    parts = [part.strip() for part in mime_type.split(";")]
    if parts[0].lower() not in ("audio/l16", "audio/pcm"):
        return None
    for param in parts[1:]:
        key, _, value = param.partition("=")
        if key.strip().lower() == "rate" and value.strip().isdigit():
            rate = int(value.strip())
            if 0 < rate <= _MAX_SAMPLE_RATE:
                return rate
    return default


def pcm_to_wav(pcm: bytes, sample_rate: int = DEFAULT_PCM_SAMPLE_RATE, channels: int = 1, sample_width: int = 2) -> bytes:
    """Frame raw little-endian PCM samples as a playable WAV file."""
    block_align = channels * sample_width
    # A trailing partial sample would make the data chunk length disagree
    # with the block alignment; drop it.
    pcm = pcm[: len(pcm) - (len(pcm) % block_align)]
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,  # fmt chunk size
        1,  # PCM
        channels,
        sample_rate,
        sample_rate * block_align,
        block_align,
        sample_width * 8,
        b"data",
        len(pcm),
    )
    return header + pcm
