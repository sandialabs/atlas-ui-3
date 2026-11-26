"""Read a tracked run's conversation before its first save (issue #884 follow-up).

A tracked run persists its conversation only when the turn ends. Until then
the run's own ``Session`` is the only place its transcript exists: the user
prompt, the tool rows it has produced so far, and any completed steps. The
sidebar can list the conversation (it knows the run), but opening it went to
the repository, found nothing, and refused -- so a run started in a new chat
was unreachable from the moment the user navigated away until it finished.

This module answers the "open" from the run's session instead, in the same
shape ``ConversationRepository.get_conversation`` returns, so the existing
route and restore paths can fall back to it without the client learning a
second format. Ownership is enforced by the registry lookup: only the run's
owner resolves the conversation id to a run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from atlas.application.chat.runs.registry import RunRegistry
from atlas.domain import conversation_classification as conv_class
from atlas.domain.conversation_origin import (
    ORIGIN_METADATA_KEY,
    origin_fields,
    origin_for_run_source,
)

TITLE_MAX_CHARS = 200

# The one scope another replica may be shown. A claim says "shared" explicitly;
# an off-record turn shares nothing and a connection-bound turn lives on its
# socket's own session, so both stay on their owner. Anything else -- a record
# without the field (written before it existed), an unknown value, a record
# that cannot be read -- is hidden: "missing" is never read as "public".
SHAREABLE_SCOPE = "shared"


class ScopeLookupUnavailable(Exception):
    """The shared records that say who may see a run could not be read."""


def _iso(epoch: float) -> str:
    """The stored shape's timestamp format (ISO, UTC) from a time.time() value."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _run_message_dicts(session) -> list:
    messages = []
    history = getattr(session, "history", None)
    for index, msg in enumerate(getattr(history, "messages", []) or []):
        data = msg.to_dict()
        metadata = data.get("metadata") or {}
        data["message_type"] = metadata.get("message_type", "chat")
        data["sequence_number"] = index
        messages.append(data)
    return messages


def _live_metadata(session) -> Dict[str, Any]:
    """The stored shape's metadata, including the run's classification record.

    The record comes from the binding the run's turn gate checked (issue
    #1042); a run whose turn has not reached the gate yet has none, and reads
    as legacy -- refused while compliance levels are enforced -- until it does.
    """
    metadata: Dict[str, Any] = {
        "agent_mode": bool(session.context.get("agent_mode")),
        "workspace_id": session.context.get("workspace_id"),
    }
    classification = conv_class.metadata_value(
        session.context.get(conv_class.SESSION_BINDING_KEY)
    )
    if classification is not ...:
        metadata[conv_class.CLASSIFICATION_METADATA_KEY] = classification
    launch_origin = origin_for_run_source(session.context.get(ORIGIN_METADATA_KEY))
    if launch_origin:
        metadata[ORIGIN_METADATA_KEY] = launch_origin
    return metadata


async def _run_elsewhere_view(conversation_id: str, user_email: str) -> Optional[Dict[str, Any]]:
    """A running view of a conversation whose live run another replica owns.

    The run's session, stream buffer and task live on the owner, so this
    replica cannot build the transcript. The shared run record can still say
    that a run holds the conversation, which is enough for a client to keep a
    running state and poll instead of reading a miss as "gone" (issue #1219).
    The view carries no messages: callers fall back to the stored record for
    those, and ``attach_execution`` adds the tool rows the owner has
    projected so far. ``run_elsewhere`` is the marker; ``in_flight`` stays
    unset because the replay and live-poll paths that key on it need the
    owner's transcript.
    """
    from atlas.application.chat.runs.control_bus import get_run_control_bus
    from atlas.application.chat.runs.run_control import RunDirectoryUnavailable, conversation_entry
    from atlas.core.user_identity import normalize_user_email

    bus = get_run_control_bus()
    if bus is None:
        return None
    # Liveness comes from the shared claim, which exists from admission: a
    # text-only first turn publishes no execution projection until its first
    # tool or terminal event, so the projection cannot decide it.
    try:
        entry = await conversation_entry(conversation_id)
    except RunDirectoryUnavailable:
        return None
    if (
        entry is None
        or entry.get("replica") == bus.replica_id
        or entry.get("user_email") != normalize_user_email(user_email)
        or entry.get("scope") != SHAREABLE_SCOPE
    ):
        return None
    now = datetime.now(tz=timezone.utc).isoformat()
    return {
        "id": conversation_id,
        "user_email": user_email,
        "title": None,
        "model": None,
        "created_at": now,
        "updated_at": now,
        "message_count": 0,
        "metadata": {},
        "messages": [],
        "tags": [],
        "run_elsewhere": True,
        "run_id": entry.get("run_id"),
        "run_status": entry.get("status") or "running",
    }


async def shareable_remote_run_ids(run_ids) -> set:
    """The ids among ``run_ids`` that another replica may show: remote runs
    whose shared record was read and carries the explicit ``shared`` scope.

    This is the only place the rule lives. Runs this replica holds are not
    remote and are never listed here (their owner sees them). An absent record,
    a record without a scope, or any other scope is not in the result.
    Raises :class:`ScopeLookupUnavailable` when the records cannot be read, so a
    caller marks its answer degraded and shows no remote run.
    """
    from atlas.application.chat.runs import get_run_registry
    from atlas.application.chat.runs.control_bus import get_run_control_bus

    remote = [str(r) for r in run_ids if r and get_run_registry().get(str(r)) is None]
    if not remote:
        return set()
    bus = get_run_control_bus()
    if bus is None:
        # No shared run records: there is no other replica, so every
        # projection here is this process's own.
        return set(remote)
    try:
        scopes = await bus.scopes_of(remote)
    except Exception as exc:
        raise ScopeLookupUnavailable(type(exc).__name__) from exc
    return {run_id for run_id, scope in scopes.items() if scope == SHAREABLE_SCOPE}


async def shareable_projections(projections) -> list:
    """``projections`` minus the remote runs another replica may not show.

    Applied before projections are merged into anything, so a hidden one can
    neither be listed nor stand in for a live claim.
    """
    from atlas.application.chat.runs import get_run_registry

    registry = get_run_registry()
    remote = [p.get("run_id") for p in projections if p.get("run_id") and registry.get(str(p["run_id"])) is None]
    shown = await shareable_remote_run_ids(remote)
    kept = []
    for projection in projections:
        run_id = projection.get("run_id")
        if run_id and (registry.get(str(run_id)) is not None or str(run_id) in shown):
            kept.append(projection)
    return kept


async def remote_live_runs(user_email: str) -> list:
    """The user's live runs that other replicas own, as ``list_runs`` entries.

    Read from the shared claims, so a run that has published no execution
    projection yet (a text-only first turn) is still listed. Raises when the
    directory cannot be read, so the caller can mark its list degraded.
    """
    from atlas.application.chat.runs.control_bus import get_run_control_bus

    bus = get_run_control_bus()
    if bus is None:
        return []
    out = []
    for entry in await bus.runs_for_user(user_email):
        if (
            entry.get("replica") == bus.replica_id
            or entry.get("scope") != SHAREABLE_SCOPE
            or not entry.get("conversation_id")
        ):
            continue
        out.append({
            "run_id": entry.get("run_id"),
            "conversation_id": entry["conversation_id"],
            "status": entry.get("status") or "running",
            "parent_run_id": entry.get("parent_run_id") or None,
            "detached": True,
        })
    return out


async def compose_run_list(user_email: str, local_runs: list, ended_for=None):
    """The ``list_runs`` answer: this replica's runs plus what other replicas
    own that may be shown, as ``(runs, degraded)``.

    The visibility rule (``shareable_remote_run_ids``) is applied to the shared
    projections before they are merged into anything and to the live claims by
    their own ``scope``, so a hidden run can neither be listed nor stand in for
    a live claim. Any failure to read the shared state returns the local runs
    only, with ``degraded`` true, so a client keeps what it tracks instead of
    reading the absence of a remote run as its end.
    """
    import logging

    from atlas.application.chat.runs.execution_state import (
        TERMINAL_RUN_STATUSES,
        list_execution_snapshots,
        merge_run_snapshots,
    )

    try:
        projections = await shareable_projections(await list_execution_snapshots(user_email, strict=True))
        claims = await remote_live_runs(user_email)
    except Exception:
        logging.getLogger(__name__).warning("shared run state could not be read", exc_info=True)
        return local_runs, True
    runs = merge_run_snapshots(local_runs, projections, ended_for=ended_for)
    live_remote = {
        r.get("conversation_id"): r for r in runs
        if r.get("detached") is True and r.get("status") not in TERMINAL_RUN_STATUSES
    }
    held = {r.get("conversation_id") for r in runs if r.get("status") not in TERMINAL_RUN_STATUSES}
    for claim in claims:
        conversation_id = claim["conversation_id"]
        stale = live_remote.get(conversation_id)
        if stale is not None and stale.get("run_id") != claim["run_id"]:
            # An earlier run's projection that never closed yields to the live claim.
            runs.remove(stale)
            held.discard(conversation_id)
        if conversation_id not in held:
            runs.append(claim)
            held.add(conversation_id)
    return runs, False


async def in_flight_conversation(
    session_repository,
    run_registry: RunRegistry,
    conversation_id: Optional[str],
    user_email: Optional[str],
) -> Optional[Dict[str, Any]]:
    """The live transcript of an unsaved conversation with an active run.

    Returns ``None`` when there is no active run for this user and
    conversation, or when the run's session cannot be read -- callers treat
    that exactly like a repository miss.
    """
    if not conversation_id or not user_email or session_repository is None:
        return None
    record = run_registry.active_for_conversation(conversation_id, user_email)
    if record is None:
        return await _run_elsewhere_view(conversation_id, user_email)
    if record.off_record or record.connection_bound:
        # A plain turn runs on its socket's own session, which can hold turns
        # that were never saved (an incognito prefix); only a run with a
        # session of its own has a transcript another socket may read.
        return None
    try:
        session = await session_repository.get(record.session_id)
    except Exception:  # pragma: no cover - defensive; a miss is a miss
        return None
    if session is None:
        return None

    messages = _run_message_dicts(session)
    title = None
    for msg in messages:
        if msg.get("role") == "user" and msg.get("content"):
            title = str(msg["content"])[:TITLE_MAX_CHARS]
            break

    # What the run is streaming right now (issue #957). Finished segments are
    # already in the messages above; this is the open one, which exists
    # nowhere else until the turn ends. A client that opens the conversation
    # from a tab that will receive no further frames (another tab, a reload)
    # renders it with an "in progress" marker; the socket that owns the run
    # also gets it again as a replay frame on restore, so its continuation
    # appends onto the same text.
    stream_text = record.stream.text()

    metadata = _live_metadata(session)
    payload = {
        "id": conversation_id,
        "user_email": user_email,
        "title": title,
        "model": None,
        # The record stands in for a stored conversation, so it carries the
        # stored shape's timestamps too -- from the run's own clock (created
        # at admission, updated at its last status change), in the same ISO
        # format the repository serializes.
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
        "message_count": len(messages),
        "metadata": metadata,
        "messages": messages,
        "tags": [],
        **conv_class.public_fields(metadata),
        **origin_fields(metadata),
        # Tells a client this is the run's live view, not a stored record.
        "in_flight": True,
        "run_id": record.run_id,
        # The open token segment, for seeding the partial answer on reopen.
        # None when there is nothing open, so a record with nothing streaming
        # reads exactly like the stored shape. A segment longer than the
        # buffer's cap replays its beginning only; the run-end reload
        # replaces the view with the stored transcript either way, so there
        # is nothing for a client to do with a truncation flag that the
        # "in progress" marker does not already say.
        "streaming_text": stream_text or None,
    }
    # Mid-step tool rows live in the shared projection, not in session
    # history. Merging here is idempotent with the route's second attach.
    from atlas.application.chat.runs.execution_state import attach_execution

    attached = await attach_execution(payload, user_email)
    return attached if attached is not None else payload
