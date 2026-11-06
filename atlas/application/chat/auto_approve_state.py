"""Live auto-approve bit for turns that have no run record.

Incognito, local-save, and chat-history-disabled turns are not admitted, so
a mid-turn toggle has no ``RunRecord`` to update. ``AgentContext`` also copies
the bit once, at the start of the turn. The gate reads this map instead, keyed
by the connection's session, on the replica that is executing the turn. The
socket that can change the switch is attached to that same process.

An entry exists only while at least one untracked turn on that session is
running. The websocket handler opens it before it schedules the turn's task,
so a toggle that arrives while the turn is still starting is not overwritten
by the admission value. A turn that is being torn down can overlap the next
one on the same session; each turn holds a reference, and the entry, with the
latest choice, stays until the last of them finishes.

The entry is not cleared when the socket closes. A tool still in flight keeps
the last choice, so leaving auto-approve on does not start asking just because
the browser went away. Only a real boolean ``True`` opts in.
"""

from typing import Dict, List, Optional

# session id -> [enabled, number of untracked turns holding the entry]
_by_session: Dict[str, List] = {}


def begin_untracked_turn(session_id, enabled: bool) -> None:
    """Hold the session's entry for one turn and record the admission value."""
    if session_id is None:
        return
    entry = _by_session.setdefault(str(session_id), [False, 0])
    entry[0] = enabled is True
    entry[1] += 1


def end_untracked_turn(session_id) -> None:
    """Release one turn's hold. The entry goes when no turn holds it."""
    if session_id is None:
        return
    key = str(session_id)
    entry = _by_session.get(key)
    if entry is None:
        return
    entry[1] -= 1
    if entry[1] <= 0:
        _by_session.pop(key, None)


def set_session_auto_approve(session_id, enabled: bool) -> bool:
    """Apply a toggle to the session's running untracked turns.

    Returns whether a turn was running. With none, there is nothing to update:
    the next turn records the value its own admission frame carries.
    """
    if session_id is None:
        return False
    entry = _by_session.get(str(session_id))
    if entry is None:
        return False
    entry[0] = enabled is True
    return True


def session_auto_approve(session_id) -> Optional[bool]:
    """The live bit, or ``None`` when no untracked turn holds this session."""
    if session_id is None:
        return None
    entry = _by_session.get(str(session_id))
    return None if entry is None else entry[0]


def clear_session_auto_approve(session_id=None) -> None:
    """Drop one session, or the whole map when ``session_id`` is omitted."""
    if session_id is None:
        _by_session.clear()
        return
    _by_session.pop(str(session_id), None)
