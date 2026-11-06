"""Live auto-approve bit for turns that have no run record.

Incognito, local-save, and chat-history-disabled turns are not admitted, so
a mid-turn toggle has no ``RunRecord`` to update. ``AgentContext`` also copies
the bit once, at the start of the turn. The gate reads this map instead, keyed
by the connection's session, on the replica that is executing the turn. The
socket that can change the switch is attached to that same process.

The entry is not cleared when the socket closes. A tool still in flight keeps
the last choice, so leaving auto-approve on does not start asking just because
the browser went away. Only a real boolean ``True`` opts in.
"""

from typing import Dict, Optional

_by_session: Dict[str, bool] = {}


def set_session_auto_approve(session_id, enabled: bool) -> None:
    """Record the switch for one session. Missing ids are ignored."""
    if session_id is None:
        return
    _by_session[str(session_id)] = enabled is True


def session_auto_approve(session_id) -> Optional[bool]:
    """The live bit, or ``None`` when this session has not set one."""
    if session_id is None:
        return None
    return _by_session.get(str(session_id))


def clear_session_auto_approve(session_id=None) -> None:
    """Drop one session, or the whole map when ``session_id`` is omitted."""
    if session_id is None:
        _by_session.clear()
        return
    _by_session.pop(str(session_id), None)
