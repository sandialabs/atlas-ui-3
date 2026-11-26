"""Which uploads count as audio for dictation (issue #1251).

A recording is accepted only when its declared media type is on the list
below *and* its first bytes are what that container starts with. The second
check is what makes the first one mean something: the declared type comes
from the client, so on its own it would let any file be forwarded to the
audio server under an audio label.
"""

from __future__ import annotations

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
