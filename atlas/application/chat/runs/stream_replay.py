"""The open token segment of a tracked run, kept for late re-attach (issue #957).

A tracked run streams its answer token by token, and the client drops frames
for any conversation it is not displaying (issue #884) -- so a user who leaves
a conversation mid-stream and comes back finds the reply starting at whatever
token happened to be current on arrival, its beginning gone. The run's own
session is no help for this window: it receives a streamed segment only once
the segment is finished, as a narration row or, at turn end, as the answer.

This module holds the *currently open* segment's text per run, updated as the
tokens flow through the notifier. The reopen paths read it:

* the in-flight conversation record carries it as ``streaming_text`` -- that
  is what a second tab and a page reload show, since no live frames follow
  them (the run's frames stay bound to the socket that started it);
* ``restore_conversation`` sends it as a ``token_stream`` frame so the
  reopened view shows the reply from its first word, with the live stream
  continuing on top of it.

Only the open segment is held, and deliberately so: a segment the run has
closed is committed to the run's session history -- narration rows go in the
moment a step ends, and the answer at turn end -- which the in-flight record
already replays. Holding closed segments here as well would show their text
twice in the reopened view.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class StreamReplay:
    """Accumulates the token segment a tracked run is streaming right now.

    One instance per run, owned by its :class:`~atlas.application.chat.runs.registry.RunRecord`.
    ``is_first`` starts a new segment (clearing the previous one), ``is_last``
    closes it -- from that moment the text belongs to the session history and
    the buffer must be empty again.

    A replay is best effort. An answer longer than :attr:`MAX_CHARS` replays
    its beginning only; the reload the client does when the run ends replaces
    the transcript with the stored one either way, so the cap bounds memory,
    not correctness.
    """

    __slots__ = ("_parts", "_length", "_truncated")

    MAX_CHARS = 200_000

    def __init__(self) -> None:
        # Parts, not one growing string: observe() runs on the token hot path,
        # and repeated ``text += token`` copies the whole buffer per frame.
        # Appends are O(1); the join happens once per reopen read.
        self._parts: list = []
        self._length = 0
        self._truncated = False

    def observe(self, token: str, is_first: bool, is_last: bool) -> None:
        """Fold one ``token_stream`` frame into the buffer."""
        if is_first:
            self._parts = []
            self._length = 0
            self._truncated = False
        if is_last:
            # Closed segments reach history through the run itself; keeping
            # the text here would duplicate it in a reopened view.
            self._parts = []
            self._length = 0
            self._truncated = False
            return
        if not token or self._truncated:
            return
        room = self.MAX_CHARS - self._length
        if len(token) > room:
            if room > 0:
                self._parts.append(token[:room])
                self._length += room
            self._truncated = True
            # Once per segment, at the transition: silent truncation would
            # leave operators no signal that a replay is serving a prefix.
            logger.warning(
                "Stream replay buffer reached its %d-char cap; reopens replay the beginning only",
                self.MAX_CHARS,
            )
            return
        self._parts.append(token)
        self._length += len(token)

    def text(self) -> str:
        """What the run has streamed into its open segment so far."""
        return "".join(self._parts)

    @property
    def truncated(self) -> bool:
        return self._truncated

    def clear(self) -> None:
        self._parts = []
        self._length = 0
        self._truncated = False
