"""Shared execution projection for a conversation run (issue #1122).

While a run executes, the transcript a client can fetch has to match the
transcript a client that stayed on the conversation can see. The run's
session history only grows at a step boundary, and the registry that tracks
approvals is process-local, so a fetch served by another replica (or a
reload in the middle of a parallel step) used to drop rows.

This module is the client-facing projection: one JSON document per
conversation, with a run-owned revision, one row per tool invocation, and
the pending approval set. Durable tool rows still flush into chat history.
The projection is coordination state. ``SESSION_REPOSITORY_TYPE=redis``
stores it in the shared Redis pool, so any replica answers
``GET /api/conversations/{id}`` with the same document. The memory store is
the single-instance fallback, the same boundary the session repository
already has. It is not a second source of truth beside Redis.

Approval outcomes live in their own key. The first write wins, so a
duplicate response or a response that lands on a replica other than the
executor returns the outcome already recorded instead of an unknown-approval
error.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from typing import Any, Dict, List, Optional

from redis.exceptions import WatchError

from atlas.core.user_identity import normalize_user_email

logger = logging.getLogger(__name__)

PROJECTION_TTL_SECONDS = 24 * 60 * 60
_MAX_STR_CHARS = 8000
_MAX_DEPTH = 6
_TERMINAL_INVOCATION = frozenset(
    {"completed", "failed", "interrupted", "expired", "rejected"}
)
_TERMINAL_RUN = frozenset({"completed", "failed", "cancelled"})
_SLEEP_TOOLS = frozenset({"atlas_sleep", "atlas_agent_sleep"})
_INPUT_EVENTS = frozenset({"tool_approval_request", "elicitation_request"})
_SETTLED_EVENTS = frozenset(
    {"tool_complete", "tool_error", "tool_interrupted", "tool_result", "auth_required"}
)
_HIDDEN_EVENT_KEYS = frozenset({"user_email"})

def invocation_id(run_id: str, step: int, tool_call_id: str) -> str:
    """Stable identity for one tool call inside one run step.

    Providers reuse ``tool_call_id`` across steps and across runs (ids often
    restart at ``call_0``). The run and the step make the id global.
    """
    return f"{run_id}:{int(step)}:{tool_call_id}"


def approval_storage_key(invocation_id_value: Optional[str], tool_call_id: Optional[str]) -> str:
    """Key for a shared approval outcome. Prefers the invocation identity."""
    if invocation_id_value:
        return invocation_id_value
    return f"tool:{tool_call_id or ''}"


def execution_identity(session_context: Optional[dict], tool_call_id: str, tool_name: Optional[str] = None) -> dict:
    """Identity fields stamped on a tool frame and on the projection."""
    from atlas.application.chat.runs.context import get_current_run

    context = session_context or {}
    run = get_current_run()
    run_id = context.get("run_id") or (run.run_id if run else None)
    conversation_id = context.get("conversation_id") or (run.conversation_id if run else None)
    try:
        step = int(context.get("agent_step") or 0)
    except (TypeError, ValueError):
        step = 0
    identity = {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "step": step,
        "tool_call_id": tool_call_id,
        "user_email": context.get("user_email") or "",
    }
    if tool_name:
        identity["tool_name"] = tool_name
    if run_id and tool_call_id:
        identity["invocation_id"] = invocation_id(str(run_id), step, str(tool_call_id))
    return identity


def _is_sleep(tool_name: Optional[str]) -> bool:
    return (tool_name or "") in _SLEEP_TOOLS


def _elide(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        if len(value) > _MAX_STR_CHARS:
            dropped = len(value) - _MAX_STR_CHARS
            return value[:_MAX_STR_CHARS] + f"...[truncated {dropped} chars]"
        return value
    if depth >= _MAX_DEPTH:
        return str(value)[:_MAX_STR_CHARS]
    if isinstance(value, dict):
        return {str(k): _elide(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_elide(v, depth + 1) for v in value]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)[:_MAX_STR_CHARS]


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def _blank(run_id: str, conversation_id: str, user_email: str) -> dict:
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "user_email": user_email,
        "revision": 0,
        "status": "running",
        "activity": "executing",
        "invocations": {},
        "pending_requests": {},
        "seq": 0,
        "updated_at": time.time(),
    }


def _status_for(event: dict) -> Optional[str]:
    event_type = event.get("type")
    if event_type == "invocation":
        return event.get("status") or "calling"
    if event_type == "tool_start":
        return "sleeping" if _is_sleep(event.get("tool_name")) else "in_progress"
    if event_type == "tool_progress":
        return "sleeping" if _is_sleep(event.get("tool_name")) else "in_progress"
    if event_type == "tool_complete":
        return "completed" if event.get("success", True) else "failed"
    if event_type == "tool_interrupted":
        return "interrupted"
    if event_type == "auth_required":
        return "failed"
    if event_type == "tool_error":
        status = event.get("status") or "failed"
        if status not in _TERMINAL_INVOCATION:
            return "failed"
        return status
    return None


def _request_key(event: dict) -> str:
    if event.get("invocation_id"):
        return str(event["invocation_id"])
    if event.get("elicitation_id"):
        return f"elicitation:{event['elicitation_id']}"
    return f"tool:{event.get('tool_call_id')}"


def _public_event(event: dict) -> dict:
    """Client-facing copy of one frame.

    Pending approval ``arguments`` stay intact. Approving the card sends those
    arguments back, and a truncated copy would run as an edited payload.
    Other fields are shortened so one document stays bounded.
    """
    public = {}
    for key, value in event.items():
        if key in _HIDDEN_EVENT_KEYS or str(key).startswith("_"):
            continue
        if key == "arguments":
            try:
                public[key] = copy.deepcopy(value)
            except Exception:
                public[key] = value
            continue
        public[key] = _elide(value)
    return public


def _touch_invocation(doc: dict, event: dict, status: Optional[str]) -> None:
    inv_id = event.get("invocation_id")
    if not inv_id:
        return
    entry = doc["invocations"].get(inv_id)
    if entry is None:
        doc["seq"] = int(doc.get("seq") or 0) + 1
        entry = {
            "invocation_id": inv_id,
            "run_id": event.get("run_id"),
            "conversation_id": event.get("conversation_id"),
            "step": event.get("step") or 0,
            "tool_call_id": event.get("tool_call_id"),
            "tool_name": event.get("tool_name"),
            "status": status or "calling",
            "seq": doc["seq"],
        }
        doc["invocations"][inv_id] = entry
    if event.get("tool_name"):
        entry["tool_name"] = event["tool_name"]
    if event.get("arguments") is not None:
        entry["arguments"] = _elide(event.get("arguments"))
    if status and not (entry.get("status") in _TERMINAL_INVOCATION and status not in _TERMINAL_INVOCATION):
        entry["status"] = status
    if _is_sleep(entry.get("tool_name")) and entry.get("status") in ("calling", "in_progress"):
        entry["status"] = "sleeping"
    if event.get("type") == "tool_complete":
        entry["result"] = _elide(event.get("result"))
        entry["success"] = bool(event.get("success", True))
    elif event.get("type") in ("tool_error", "tool_interrupted", "auth_required"):
        entry["result"] = _elide(event.get("error") or event.get("result") or event.get("message"))
        entry["success"] = False


def _drop_settled(doc: dict, event: dict) -> None:
    inv = event.get("invocation_id")
    tool_call_id = event.get("tool_call_id")
    elicitation_id = event.get("elicitation_id")
    drop = []
    for key, request in doc["pending_requests"].items():
        if inv and (request.get("invocation_id") == inv or key == inv):
            drop.append(key)
        elif elicitation_id and request.get("elicitation_id") == elicitation_id:
            drop.append(key)
        elif tool_call_id and request.get("tool_call_id") == tool_call_id and not request.get("invocation_id"):
            drop.append(key)
    for key in drop:
        del doc["pending_requests"][key]


def _refresh_activity(doc: dict) -> None:
    pending = doc["pending_requests"]
    if pending:
        kinds = {request.get("type") for request in pending.values()}
        doc["activity"] = "waiting_for_approval"
        doc["status"] = "waiting_for_input"
        doc["waiting_on"] = (
            "tool_approval_request" if "tool_approval_request" in kinds else "elicitation_request"
        )
        return
    sleeping = any(inv.get("status") == "sleeping" for inv in doc["invocations"].values())
    doc["waiting_on"] = None
    if sleeping:
        doc["activity"] = "sleeping"
        doc["status"] = "running"
        return
    doc["activity"] = "executing"
    if doc.get("status") == "waiting_for_input":
        doc["status"] = "running"


def _apply_run_status(doc: dict, event: dict) -> bool:
    """Record a terminal run transition. Returns True when ``event`` is consumed.

    Terminal is sticky. A late tool frame must not bring a finished run back
    to running or waiting_for_input.
    """
    if event.get("type") != "run_status":
        return False
    status = event.get("status")
    if status not in _TERMINAL_RUN or doc.get("status") in _TERMINAL_RUN:
        return True
    doc["status"] = status
    doc["activity"] = status
    doc["waiting_on"] = None
    doc["pending_requests"] = {}
    if event.get("error"):
        doc["error"] = _elide(event.get("error"))
    doc["updated_at"] = time.time()
    return True


def apply_event_to_document(doc: dict, event: dict) -> None:
    """Fold one tool, approval, or terminal frame into ``doc``. Does not bump the revision."""
    if event.get("run_id") and doc.get("run_id") and event["run_id"] != doc["run_id"]:
        fresh = _blank(str(event["run_id"]), doc["conversation_id"], doc["user_email"])
        doc.clear()
        doc.update(fresh)
    if _apply_run_status(doc, event):
        return
    if doc.get("status") in _TERMINAL_RUN:
        return
    status = _status_for(event)
    if status or event.get("invocation_id"):
        _touch_invocation(doc, event, status)
    if event.get("type") in _INPUT_EVENTS:
        _touch_invocation(doc, event, "calling")
        doc["pending_requests"][_request_key(event)] = _public_event(event)
    elif event.get("type") in _SETTLED_EVENTS:
        _drop_settled(doc, event)
    doc["updated_at"] = time.time()
    _refresh_activity(doc)


def public_projection(doc: dict) -> dict:
    invocations = sorted(
        doc.get("invocations", {}).values(),
        key=lambda item: (item.get("step") or 0, item.get("seq") or 0),
    )
    return {
        "run_id": doc.get("run_id"),
        "conversation_id": doc.get("conversation_id"),
        "revision": int(doc.get("revision") or 0),
        "status": doc.get("status") or "running",
        "activity": doc.get("activity") or "executing",
        "waiting_on": doc.get("waiting_on"),
        "invocations": invocations,
        "pending_requests": list(doc.get("pending_requests", {}).values()),
    }


def _message_from_invocation(invocation: dict) -> dict:
    metadata = {
        "message_type": "tool_call",
        "tool_call_id": invocation.get("tool_call_id"),
        "invocation_id": invocation.get("invocation_id"),
        "run_id": invocation.get("run_id"),
        "step": invocation.get("step"),
        "tool_name": invocation.get("tool_name"),
        "arguments": invocation.get("arguments") or {},
        "result": invocation.get("result"),
        "status": invocation.get("status") or "calling",
    }
    return {
        "role": "tool",
        "content": f"Tool call: {invocation.get('tool_name') or invocation.get('tool_call_id')}",
        "metadata": metadata,
        "message_type": "tool_call",
    }


def merge_execution_into_messages(messages: Optional[list], projection: dict) -> list:
    """Upsert projection rows into a conversation message list.

    A stored row that already carries ``invocation_id`` is updated in place.
    A legacy row with only ``tool_call_id`` is reused once. Anything still
    unmatched is appended, which is how a mid-step fetch shows tools the
    history has not flushed yet.
    """
    merged = [dict(message) for message in (messages or [])]
    by_invocation = {}
    by_legacy = {}
    for index, message in enumerate(merged):
        meta = message.get("metadata") or {}
        if (message.get("message_type") or meta.get("message_type")) != "tool_call" and message.get("role") != "tool":
            continue
        inv = meta.get("invocation_id") or message.get("invocation_id")
        tool_call_id = meta.get("tool_call_id") or message.get("tool_call_id")
        if inv:
            by_invocation[inv] = index
        elif tool_call_id:
            by_legacy.setdefault(tool_call_id, index)
    for invocation in projection.get("invocations") or []:
        inv_id = invocation.get("invocation_id")
        index = by_invocation.get(inv_id)
        if index is None:
            legacy = by_legacy.pop(invocation.get("tool_call_id"), None)
            index = legacy
        row = _message_from_invocation(invocation)
        if index is None:
            merged.append(row)
            if inv_id:
                by_invocation[inv_id] = len(merged) - 1
            continue
        meta = dict(merged[index].get("metadata") or {})
        meta.update(row["metadata"])
        merged[index]["metadata"] = meta
        merged[index]["message_type"] = "tool_call"
        if inv_id:
            by_invocation[inv_id] = index
    return merged


def merge_run_snapshots(local_runs: List[dict], shared: List[dict]) -> List[dict]:
    """Overlay shared projections onto the process-local run list.

    A replica that did not start the run still has the projection, so the
    snapshot it serves carries the same status and activity.
    """
    merged = {run.get("conversation_id"): dict(run) for run in local_runs if run.get("conversation_id")}
    for projection in shared:
        conversation_id = projection.get("conversation_id")
        if not conversation_id:
            continue
        existing = merged.get(conversation_id)
        projection_status = projection.get("status")
        # A finished projection is not a new active run. Keeping it for the
        # document TTL would leave "Needs approval" on a run that already ended.
        if existing is None:
            if projection_status in _TERMINAL_RUN:
                continue
            merged[conversation_id] = {
                "run_id": projection.get("run_id"),
                "conversation_id": conversation_id,
                "status": projection_status or "running",
                "waiting_on": projection.get("waiting_on"),
                "activity": projection.get("activity"),
                "execution_revision": projection.get("revision"),
                "detached": True,
            }
            continue
        existing["execution_revision"] = projection.get("revision")
        # The registry already recorded the outcome. A projection that has not
        # yet seen run_status must not put that run back to running.
        if existing.get("status") in _TERMINAL_RUN:
            continue
        existing["activity"] = projection.get("activity")
        if projection_status:
            existing["status"] = projection_status
        existing["waiting_on"] = projection.get("waiting_on")
    return list(merged.values())


class ExecutionStore:
    """Projection and approval-outcome storage."""

    async def apply_event(self, event: dict) -> Optional[dict]:
        raise NotImplementedError

    async def get(self, conversation_id: str, user_email: str) -> Optional[dict]:
        raise NotImplementedError

    async def list_for_user(self, user_email: str) -> List[dict]:
        raise NotImplementedError

    async def read_resolution(self, request_key: str) -> Optional[dict]:
        raise NotImplementedError

    async def put_resolution(self, request_key: str, outcome: dict) -> dict:
        raise NotImplementedError


def _prepare(event: dict) -> Optional[tuple]:
    conversation_id = event.get("conversation_id")
    run_id = event.get("run_id")
    user_email = normalize_user_email(event.get("user_email"))
    if not conversation_id or not run_id or not user_email:
        return None
    return str(conversation_id), str(run_id), user_email


class MemoryExecutionStore(ExecutionStore):
    """Process-local projection. Single-instance mode only."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._docs: Dict[tuple, dict] = {}
        self._by_user: Dict[str, set] = {}
        self._resolutions: Dict[str, dict] = {}

    async def apply_event(self, event: dict) -> Optional[dict]:
        prepared = _prepare(event)
        if prepared is None:
            return None
        conversation_id, run_id, user_email = prepared
        async with self._lock:
            key = (user_email, conversation_id)
            doc = self._docs.get(key)
            if doc is None or (doc.get("run_id") and doc["run_id"] != run_id):
                doc = _blank(run_id, conversation_id, user_email)
            else:
                doc = copy.deepcopy(doc)
            apply_event_to_document(doc, event)
            doc["revision"] = int(doc.get("revision") or 0) + 1
            self._docs[key] = doc
            self._by_user.setdefault(user_email, set()).add(conversation_id)
            return public_projection(doc)

    async def get(self, conversation_id: str, user_email: str) -> Optional[dict]:
        email = normalize_user_email(user_email)
        if not conversation_id or not email:
            return None
        async with self._lock:
            doc = self._docs.get((email, str(conversation_id)))
            return copy.deepcopy(doc) if doc else None

    async def list_for_user(self, user_email: str) -> List[dict]:
        email = normalize_user_email(user_email)
        async with self._lock:
            ids = list(self._by_user.get(email, ()))
            return [public_projection(self._docs[(email, cid)]) for cid in ids if (email, cid) in self._docs]

    async def read_resolution(self, request_key: str) -> Optional[dict]:
        async with self._lock:
            found = self._resolutions.get(request_key)
            return dict(found) if found else None

    async def put_resolution(self, request_key: str, outcome: dict) -> dict:
        async with self._lock:
            existing = self._resolutions.get(request_key)
            if existing is not None:
                return {**existing, "already_resolved": True}
            stored = {k: v for k, v in outcome.items() if k != "already_resolved"}
            self._resolutions[request_key] = stored
            return {**stored, "already_resolved": False}


class RedisExecutionStore(ExecutionStore):
    """Projection in the process's shared Redis pool (issue #1091)."""

    def __init__(self, redis_manager):
        self._redis = redis_manager
        self._lock = asyncio.Lock()

    def _client(self):
        return self._redis.client

    def _doc_key(self, email: str, conversation_id: str) -> str:
        return self._redis.key("exec", email, conversation_id)

    def _rev_key(self, email: str, conversation_id: str) -> str:
        return self._redis.key("exec-rev", email, conversation_id)

    def _user_key(self, email: str) -> str:
        return self._redis.key("exec-user", email)

    def _resolution_key(self, request_key: str) -> str:
        return self._redis.key("approval-result", request_key)

    async def apply_event(self, event: dict) -> Optional[dict]:
        prepared = _prepare(event)
        if prepared is None:
            return None
        conversation_id, run_id, user_email = prepared
        doc_key = self._doc_key(user_email, conversation_id)
        rev_key = self._rev_key(user_email, conversation_id)
        user_key = self._user_key(user_email)
        client = self._client()
        async with self._lock:
            for _attempt in range(8):
                current = _text(await client.get(rev_key)) or "0"
                raw = await client.get(doc_key)
                if raw:
                    doc = json.loads(_text(raw))
                else:
                    doc = _blank(run_id, conversation_id, user_email)
                if doc.get("run_id") and doc["run_id"] != run_id:
                    doc = _blank(run_id, conversation_id, user_email)
                apply_event_to_document(doc, event)
                next_rev = str(int(current) + 1)
                doc["revision"] = int(next_rev)
                payload = json.dumps(doc, default=str)
                # The revision key is the compare-and-swap token. WATCH fails
                # the write when another replica moved it, and this process
                # retries against the document that replica stored. A Lua
                # script would do the same compare, but the shared test Redis
                # does not implement EVAL.
                try:
                    wrote = await self._compare_and_set(
                        client, rev_key, doc_key, user_key,
                        current, next_rev, payload, conversation_id,
                    )
                except WatchError:
                    wrote = False
                if wrote:
                    return public_projection(doc)
            logger.warning("execution projection update lost the revision race")
            return None

    async def _compare_and_set(
        self, client, rev_key, doc_key, user_key,
        current, next_rev, payload, conversation_id,
    ) -> bool:
        async with client.pipeline() as pipe:
            await pipe.watch(rev_key)
            observed = _text(await client.get(rev_key)) or "0"
            if observed != current:
                await pipe.unwatch()
                return False
            pipe.multi()
            pipe.set(rev_key, next_rev, ex=PROJECTION_TTL_SECONDS)
            pipe.set(doc_key, payload, ex=PROJECTION_TTL_SECONDS)
            pipe.sadd(user_key, conversation_id)
            pipe.expire(user_key, PROJECTION_TTL_SECONDS)
            await pipe.execute()
        return True

    async def get(self, conversation_id: str, user_email: str) -> Optional[dict]:
        email = normalize_user_email(user_email)
        if not conversation_id or not email:
            return None
        raw = await self._client().get(self._doc_key(email, str(conversation_id)))
        if not raw:
            return None
        return json.loads(_text(raw))

    async def list_for_user(self, user_email: str) -> List[dict]:
        email = normalize_user_email(user_email)
        if not email:
            return []
        client = self._client()
        members = await client.smembers(self._user_key(email))
        projections = []
        for member in members or []:
            conversation_id = _text(member)
            doc = await self.get(conversation_id, email)
            if doc is None:
                await client.srem(self._user_key(email), member)
                continue
            projections.append(public_projection(doc))
        return projections

    async def read_resolution(self, request_key: str) -> Optional[dict]:
        raw = await self._client().get(self._resolution_key(request_key))
        if not raw:
            return None
        return json.loads(_text(raw))

    async def put_resolution(self, request_key: str, outcome: dict) -> dict:
        key = self._resolution_key(request_key)
        client = self._client()
        stored = {k: v for k, v in outcome.items() if k != "already_resolved"}
        created = await client.set(key, json.dumps(stored, default=str), nx=True, ex=PROJECTION_TTL_SECONDS)
        if created:
            return {**stored, "already_resolved": False}
        raw = await client.get(key)
        existing = json.loads(_text(raw)) if raw else stored
        return {**existing, "already_resolved": True}


_store: Optional[ExecutionStore] = None


def get_execution_store() -> ExecutionStore:
    """The process projection store. Memory until the app binds Redis."""
    global _store
    if _store is None:
        _store = MemoryExecutionStore()
    return _store


def set_execution_store(store: Optional[ExecutionStore]) -> None:
    global _store
    _store = store


def reset_execution_store() -> None:
    set_execution_store(None)


async def publish_execution_event(event: dict) -> None:
    """Record one frame. A store error must not fail the tool call."""
    try:
        await get_execution_store().apply_event(event)
    except Exception:
        logger.warning("execution projection update failed", exc_info=True)


async def attach_execution(conversation: Optional[dict], user_email: Optional[str]) -> Optional[dict]:
    """Add the projection to a conversation payload and merge invocation rows."""
    if not isinstance(conversation, dict) or not conversation.get("id") or not user_email:
        return conversation
    try:
        doc = await get_execution_store().get(str(conversation["id"]), user_email)
    except Exception:
        logger.warning("execution projection read failed", exc_info=True)
        return conversation
    if not doc:
        return conversation
    projection = public_projection(doc)
    merged = dict(conversation)
    merged["execution"] = projection
    merged["messages"] = merge_execution_into_messages(merged.get("messages") or [], projection)
    merged["message_count"] = len(merged["messages"])
    merged["run_activity"] = projection.get("activity")
    return merged


async def list_execution_snapshots(user_email: str) -> List[dict]:
    try:
        return await get_execution_store().list_for_user(user_email)
    except Exception:
        logger.warning("execution projection list failed", exc_info=True)
        return []


async def wait_for_shared_approval(request, timeout: Optional[float], request_key: Optional[str]) -> dict:
    """Wait on the local future and, when present, the shared outcome.

    The executor replica holds the future. A response that arrives on another
    replica is written to the store; polling observes it. Whichever finishes
    first wins. Cancellation still unwinds both waits.
    """
    local = asyncio.ensure_future(request.wait_for_response(timeout))
    remote = None
    if request_key:
        remote = asyncio.ensure_future(_poll_resolution(request_key, timeout))
    try:
        tasks = [local] + ([remote] if remote is not None else [])
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if local in done:
            error = local.exception()
            if isinstance(error, asyncio.TimeoutError) and request_key:
                found = await get_execution_store().read_resolution(request_key)
                if found:
                    return found
            if error:
                raise error
            return local.result()
        assert remote is not None
        return remote.result()
    finally:
        for task in (local, remote):
            if task is not None and not task.done():
                task.cancel()


async def _poll_resolution(request_key: str, timeout: Optional[float]) -> dict:
    deadline = None if timeout is None or timeout <= 0 else time.monotonic() + timeout
    while True:
        found = await get_execution_store().read_resolution(request_key)
        if found:
            return found
        if deadline is not None and time.monotonic() >= deadline:
            raise asyncio.TimeoutError
        await asyncio.sleep(0.2)


def observe_tool_callback(inner, identity: dict):
    """Publish tool frames into the projection, then forward them.

    No-op wrapper when this call has no run identity, so untracked turns keep
    the payload shape they had before.
    """
    if not identity.get("invocation_id"):
        return inner

    async def tagged(payload):
        if isinstance(payload, dict):
            merged = dict(payload)
            for key, value in identity.items():
                if key == "user_email" or value is None:
                    continue
                merged.setdefault(key, value)
            event = dict(merged)
            event["user_email"] = identity.get("user_email")
            await publish_execution_event(event)
            payload = merged
        if inner is not None:
            await inner(payload)

    return tagged
