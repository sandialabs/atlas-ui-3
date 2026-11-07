"""Locate the run an actor is addressing (issue #1128).

Every entry point that acts on a run it did not just start (WebSocket stop,
WebSocket steer, REST cancel, the approval nudge) asks the same question:
which run does this frame name, and where is it? :func:`resolve` answers it in
one fixed order, so no handler carries its own fallback chain:

1. ``run_id`` named and in this replica's registry for the actor -> :class:`Local`.
2. ``run_id`` named and in the shared run directory -> :class:`Remote`. A
   finished run's record counts only with ``include_terminal``.
3. No ``run_id``, or it located nothing: the conversation's active run in this
   replica's registry -> :class:`Local`.
4. The conversation's live run in the shared directory -> :class:`Remote`.
5. The directory could not be read (at step 2, with step 3 also empty, or at
   step 4) -> :class:`Unknown`. A failed read is never reported as
   :class:`Nothing`: the run may be executing on another replica.
6. Otherwise :class:`Nothing`.

``resolve`` only locates. Whether the actor may act on the run is decided by
the owner (see ``run_control.RunControlService``). Two scoping rules mirror the
local registry's: a ``run_id`` is only located among the actor's own runs, and
the trusted service actor addresses by run id alone. A conversation held by
*another user's* run is returned as :class:`Remote` so the caller can refuse it
instead of starting a turn beside it. A directory entry naming this replica is
never :class:`Remote`: the registry already answered for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

from atlas.application.chat.runs.control_bus import get_run_control_bus
from atlas.application.chat.runs.run_control import (
    RunDirectoryUnavailable,
    conversation_entry,
    remote_entry,
)
from atlas.core.user_identity import normalize_user_email


@dataclass(frozen=True)
class Address:
    run_id: Optional[str] = None
    conversation_id: Optional[str] = None


@dataclass(frozen=True)
class Local:
    """The run is in this replica's registry."""

    record: Any


@dataclass(frozen=True)
class Remote:
    """The run is on another replica; ``entry`` is its shared run record."""

    entry: Dict[str, Any]


@dataclass(frozen=True)
class Unknown:
    """The shared directory could not be read."""

    reason: str


@dataclass(frozen=True)
class Nothing:
    """No run matches the address."""


Resolution = Union[Local, Remote, Unknown, Nothing]


async def resolve(registry, actor: Dict[str, Any], address: Address, *,
                  include_terminal: bool = False) -> Resolution:
    """Locate the run ``address`` names for ``actor``; see the module docstring."""
    service = actor.get("kind") == "service"
    email = None if service else normalize_user_email(actor.get("email") or "")
    unknown: Optional[Unknown] = None

    if address.run_id:
        record = registry.get(address.run_id) if service else registry.get_for_user(address.run_id, email)
        if record is not None:
            return Local(record)
        try:
            entry = await remote_entry(address.run_id, include_terminal=include_terminal)
        except RunDirectoryUnavailable as exc:
            unknown = Unknown(str(exc))
        else:
            if entry is not None and (service or entry.get("user_email") == email):
                return Remote(entry)

    if service or not address.conversation_id:
        return unknown or Nothing()
    record = registry.active_for_conversation(address.conversation_id, email)
    if record is not None:
        return Local(record)
    if unknown is not None:
        return unknown
    try:
        entry = await conversation_entry(address.conversation_id)
    except RunDirectoryUnavailable as exc:
        return Unknown(str(exc))
    bus = get_run_control_bus()
    if entry is None or bus is None or entry.get("replica") == bus.replica_id:
        return Nothing()
    return Remote(entry)
