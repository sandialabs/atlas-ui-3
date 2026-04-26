"""Async tail over an audit JSONL file for SSE streaming.

The writer in `audit.py` appends whole JSON lines followed by '\\n'; this
tailer reads the file position-wise, re-reading after EOF with a short
sleep so it can pick up new frames as they are written.

Designed to be cancelled by caller task cancellation - no background
threads. Terminates naturally when:
  - caller cancels the async iterator
  - the session reaches a terminal state (the route layer checks this
    and closes by breaking the iteration loop)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator, Dict, Optional

_READ_CHUNK = 64 * 1024
_TAIL_IDLE_SLEEP_S = 0.25


async def tail_frames(
    path: Path,
    *,
    since_seq: int = 0,
    stop_event: Optional[asyncio.Event] = None,
    idle_sleep_s: float = _TAIL_IDLE_SLEEP_S,
) -> AsyncIterator[Dict]:
    """Yield JSON-decoded frames from `path` in order, starting after `since_seq`.

    Waits for new lines when the file is at EOF. Stops when `stop_event`
    is set (checked each time we wake from idle).
    """
    path = Path(path)
    # Wait until the file exists (spawn may race with the subscriber).
    while not path.exists():
        if stop_event is not None and stop_event.is_set():
            return
        await asyncio.sleep(idle_sleep_s)

    buf = b""
    last_seq = since_seq
    # Open in binary to stay compatible with the canonical encoding.
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_READ_CHUNK)
            if chunk:
                buf += chunk
                # Split on newline; last element is a possibly-partial line
                # that we keep buffered for the next read.
                *complete, remainder = buf.split(b"\n")
                buf = remainder
                for line in complete:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        frame = json.loads(line)
                    except json.JSONDecodeError:
                        # Skip malformed lines rather than crashing the stream.
                        continue
                    seq = frame.get("seq", 0)
                    if seq <= last_seq:
                        continue
                    last_seq = seq
                    yield frame
            else:
                if stop_event is not None and stop_event.is_set():
                    return
                await asyncio.sleep(idle_sleep_s)
