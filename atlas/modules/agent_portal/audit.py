"""SHA-256-chained JSONL audit writer.

One stream per session. Each frame carries `prev` = SHA-256 of the
canonical JSON of the previous frame, so tampering with any frame
invalidates all downstream frames. Verification is an external read of
the file plus a rolling hash recomputation.

The v0 writer is synchronous (fsync per frame). A later optimization
can batch; the on-disk format is stable.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterator, Optional

_GENESIS_PREV = "0" * 64


def _canonical(frame: Dict[str, Any]) -> bytes:
    """Canonical JSON encoding used for both writing and hashing.

    sort_keys + no whitespace + ensure_ascii=False gives a single byte
    form that is stable across Python versions. Using this in both
    write and verify means the chain is self-describing.
    """
    return json.dumps(frame, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AuditStream:
    """Per-session append-only audit stream.

    Thread-safe for a single process (Lock). Multi-process writers are
    out of scope for v0; adapters that fork should serialize through
    this instance.
    """

    def __init__(self, path: Path, session_id: str) -> None:
        self._path = Path(path)
        self._session = session_id
        self._seq = 0
        self._prev = _GENESIS_PREV
        self._lock = Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Restrict audit directory to owner-only (best-effort on POSIX)
        try:
            os.chmod(self._path.parent, 0o700)
        except OSError:
            pass

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self,
        stream: str,
        payload: Optional[Dict[str, Any]] = None,
        data: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """Write one frame. Returns the frame as written (with seq + prev).

        `stream` is one of: stdin, stdout, stderr, tool, lifecycle, policy.
        `payload` carries structured fields (tool name, state transition, etc.).
        `data`    carries opaque bytes which are base64-encoded inline.

        Structured `payload` and byte `data` may both appear on the same
        frame; consumers dispatch on `stream`.
        """
        with self._lock:
            self._seq += 1
            frame: Dict[str, Any] = {
                "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "session": self._session,
                "seq": self._seq,
                "prev": self._prev,
                "stream": stream,
            }
            if payload:
                # Never overwrite reserved fields.
                for k, v in payload.items():
                    if k in frame:
                        continue
                    frame[k] = v
            if data is not None:
                frame["data_b64"] = base64.b64encode(data).decode("ascii")
            encoded = _canonical(frame)
            self._prev = _sha256_hex(encoded)
            # Append newline-delimited JSON; open/close per frame keeps
            # the file readable by external tailers even mid-write.
            with self._path.open("ab") as fh:
                fh.write(encoded + b"\n")
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
            return frame


def verify_chain(path: Path) -> Dict[str, Any]:
    """Verify a JSONL audit stream end-to-end.

    Returns `{"ok": True, "frames": N, "tip": "<hex>"}` on success.
    Raises `AuditChainError` on the first frame that fails to chain.
    """
    expected_prev = _GENESIS_PREV
    frames = 0
    last_hash = _GENESIS_PREV
    with Path(path).open("rb") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.rstrip(b"\n")
            if not raw:
                continue
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AuditChainError(f"line {lineno}: not JSON: {exc}") from exc
            if frame.get("prev") != expected_prev:
                raise AuditChainError(
                    f"line {lineno}: prev mismatch "
                    f"(got {frame.get('prev')!r}, expected {expected_prev!r})"
                )
            # Re-canonicalize and hash to advance expected_prev.
            # Use the parsed frame rather than the raw bytes so
            # whitespace/encoding variations do not break verification.
            re_encoded = _canonical(frame)
            last_hash = _sha256_hex(re_encoded)
            expected_prev = last_hash
            frames += 1
    return {"ok": True, "frames": frames, "tip": last_hash}


def read_frames(path: Path) -> Iterator[Dict[str, Any]]:
    """Yield frames in order. Does not verify the chain."""
    with Path(path).open("rb") as fh:
        for raw in fh:
            raw = raw.rstrip(b"\n")
            if not raw:
                continue
            yield json.loads(raw)


class AuditChainError(ValueError):
    """Raised when a frame's `prev` does not match the running hash."""
