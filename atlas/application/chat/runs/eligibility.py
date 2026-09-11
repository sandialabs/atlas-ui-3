"""Which chat turns are allowed to run as independent background runs (#884).

Parallel, browser-independent runs are deliberately *not* the behaviour for
every turn. They are opt-in along three axes, and all three must hold:

1. ``FEATURE_CHAT_HISTORY_ENABLED`` -- a run that outlives the socket is only
   useful if its transcript is written somewhere the user can reopen. With
   history off there is nothing to come back to.
2. The user's save mode is ``server``. ``local`` and incognito modes keep the
   transcript in the browser (or nowhere), so a run that continued after the
   tab closed would produce work no one could ever see.
3. The turn asks for agent mode with at least one tool. Plain completions are
   short; the whole point of a background run is a loop that keeps working.

When any axis fails the caller keeps the pre-#884 single-run behaviour
unchanged, which is what makes this feature additive rather than a rewrite of
every deployment's semantics.

The agent-mode check here is on the *requested* turn. The orchestrator may
still silently downgrade ``agent_mode`` to False (model without tool support,
or tools that resolve to nothing); such a run is admitted but simply finishes
as an ordinary turn would, so the downgrade costs a run slot for the length of
one completion and nothing more.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence


def save_mode_is_server(save_mode: Optional[str], incognito: Any = False) -> bool:
    """Whether this turn's transcript is written to the server.

    Mirrors the transport's own incognito derivation so the two cannot drift:
    an older client that sends only ``incognito`` still resolves correctly.
    """
    if incognito is True:
        return False
    return (save_mode or "server") == "server"


def turn_is_eligible_for_background_run(
    *,
    chat_history_enabled: bool,
    save_mode: Optional[str],
    incognito: Any = False,
    agent_mode: bool = False,
    selected_tools: Optional[Sequence[str]] = None,
    conversation_id: Optional[str] = None,
) -> bool:
    """Whether this turn should be tracked as an independent run."""
    if not chat_history_enabled:
        return False
    if not save_mode_is_server(save_mode, incognito):
        return False
    if not agent_mode:
        return False
    if not selected_tools:
        return False
    # A run is keyed by conversation; without an id there is nothing to route
    # events to, nothing to enforce the one-run-per-conversation lock against,
    # and nothing for the user to reopen.
    if not conversation_id:
        return False
    return True
