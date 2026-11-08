"""Cross-replica run-control transport over Redis Streams (issue #1128).

A run executes on the replica that admitted it: its asyncio task, steering
queue and approval futures live in that process and never migrate. A stop,
steer or approval that reaches another replica is sent to the owner as a
command and answered with a correlated reply.

Who owns a run comes from run admission's shared record (``run_directory``
reads ``<p>:run:<run_id>`` and ``<p>:run-conv:<conversation_id>``; see
``docs/developer/run-coordination.md``). This module adds only the command
keys, built by ``RunCoordinationKeys``:

``<p>:ctl:<replica_id>``
    The owner's inbox stream, named by the coordinator's replica id. One
    envelope per entry (field ``c``).
``<p>:ctl-reply:<scope>``
    The reply stream for one request id. Each send has its own attempt id
    and reads only the reply carrying it.
``<p>:ctl-done:<scope>``
    Deduplication record: written ``NX`` before a command executes, then
    replaced by its reply when it took effect (a refusal changed nothing and
    is forgotten, so it can be retried). ``<scope>`` hashes the actor and the
    request id, so one caller's id can never collide with, or replay,
    another's.

Redis Streams deliver at least once at best, and a caller may retry, so a
command never executes twice for one request id: the second delivery gets
the first reply back. Every command carries a deadline on the shared Redis
clock (``TIME``), so replica clock skew cannot move it; an owner that reads
it late answers ``expired`` without executing it. The owner deletes each
inbox entry as soon as it has read it, so a command's arguments (a steering
message, say) are not retained in Redis after delivery. Every command also carries
the run's ``generation`` as the sender read it; the owner refuses it
(``stale_generation``) unless the shared record still names this replica at
that generation. Nothing here replays tool work: a command only acts on a run
that is still live on its owner.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from atlas.application.chat.runs.coordination import RUN_LEASE_SECONDS, RUN_RETENTION_SECONDS
from atlas.application.chat.runs.run_directory import RunDirectory
from atlas.core.user_identity import normalize_user_email

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 2
# Inbox entries are trimmed past this length; a command older than that has
# long passed its deadline.
INBOX_MAXLEN = 1000
# A live owner deletes each entry as it reads it, so this only bounds how long
# commands sent to a dead or stalled owner (and their arguments, a steering
# message among them) stay in Redis. Every command expires within seconds, so
# nothing older than this is ever executable. Refreshed on each send.
INBOX_TTL_SECONDS = 4 * RUN_LEASE_SECONDS
# A serialized command larger than this is refused before it is written, so a
# large client payload cannot fill the shared Redis (sessions, leases).
MAX_COMMAND_BYTES = 256 * 1024
REPLY_TTL_SECONDS = 4 * RUN_LEASE_SECONDS
# How long a request id is remembered. A retry inside this window gets the
# original reply; the in-flight marker is short so a command whose owner died
# mid-execution does not block a retry for the full window.
DONE_TTL_SECONDS = RUN_RETENTION_SECONDS
PENDING_TTL_SECONDS = RUN_LEASE_SECONDS
# Retries of the "done" write after a command acted, within the pending TTL.
DONE_WRITE_ATTEMPTS = 6
# Kept below the pool's socket timeout so a blocking read never trips it.
BLOCK_MS = 1000
DEFAULT_TIMEOUT_SECONDS = 5.0
# The bus shares the process's Redis pool (20 connections by default) with
# sessions, admission and lease renewal. A waiting sender holds a connection
# in a blocking read for up to its timeout, so at most this many wait at once;
# a sender past the bound waits for a slot within its own timeout. Commands
# this replica executes are bounded the same way.
MAX_CONCURRENT_SENDS = 6
MAX_CONCURRENT_DISPATCH = 8

# Reply codes produced by the transport itself (handlers add their own).
OWNER_UNAVAILABLE = "owner_unavailable"
COMMAND_TIMEOUT = "command_timeout"
COMMAND_EXPIRED = "command_expired"
IN_PROGRESS = "command_in_progress"
REQUEST_CONFLICT = "request_id_conflict"
STALE_GENERATION = "stale_generation"
COMMAND_TOO_LARGE = "command_too_large"
OFF_RECORD = "off_record"


Handler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def actor_id(actor: Dict[str, Any]) -> str:
    """Stable identity of whoever sent a command, for deduplication."""
    if actor.get("kind") == "service":
        return "service"
    return f"user:{actor.get('email') or ''}"


def command_scope(actor: Dict[str, Any], request_id: str) -> str:
    digest = hashlib.sha256(f"{actor_id(actor)}|{request_id}".encode()).hexdigest()
    return digest[:40]


def reply(ok: bool, code: str = "ok", message: str = "", **payload: Any) -> Dict[str, Any]:
    """A handler's answer. ``payload`` keys go back to the caller as is."""
    return {"ok": ok, "code": code, "message": message, "payload": payload}


class RedisRunControlBus:
    """Command inbox for one replica, plus the directory it routes by."""

    def __init__(self, redis_manager: Any, coordinator: Any):
        """``coordinator`` is the process's ``RedisRunCoordinator``: its keys
        build every key here, its ``read()`` serves the directory, and its
        replica id (the record's ``owner``) names this replica's inbox."""
        self._redis = redis_manager
        self.keys = coordinator.keys
        self.replica_id = coordinator.replica_id
        self.directory = RunDirectory(coordinator, redis_manager)
        self._consumer: Optional[asyncio.Task] = None
        self._handlers: "set[asyncio.Task]" = set()
        self._handler: Optional[Handler] = None
        # Runs whose claim this replica's heartbeat found lost, from the
        # admission claim-lost listener. Refused at once, before the shared
        # record (or a slow Redis) says so.
        self._lost_runs: "set[str]" = set()
        self._unsubscribe_lost: Optional[Callable[[], None]] = None
        self._send_slots = asyncio.Semaphore(MAX_CONCURRENT_SENDS)
        self._dispatch_slots = asyncio.Semaphore(MAX_CONCURRENT_DISPATCH)

    def _client(self) -> Any:
        return self._redis.client

    async def owner_of(self, run_id: str, *, include_terminal: bool = False) -> Optional[Dict[str, Any]]:
        return await self.directory.owner_of(run_id, include_terminal=include_terminal)

    async def run_for_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        return await self.directory.run_for_conversation(conversation_id)

    async def stoppable_for_conversation(self, conversation_id: str, user_email: str) -> "list[Dict[str, Any]]":
        """Runs of the user's conversation a stop must reach, on any replica.

        The live run holding the conversation, and any run of it that has
        finished but still has a live descendant. The conversation key is
        released with the run, so a finished parent is found from below: the
        user's live runs (``run-user``), each followed up its ``parent_run_id``
        chain (records outlive their live descendants) to the first run of this
        conversation. Raises when Redis cannot be read.
        """
        found: Dict[str, Dict[str, Any]] = {}
        live = await self.run_for_conversation(conversation_id)
        if live is not None and live.get("user_email") == normalize_user_email(user_email):
            found[live["run_id"]] = live
        known: Dict[str, Optional[Dict[str, Any]]] = {}
        for entry in await self.runs_for_user(user_email):
            current, hops = entry, 0
            while current.get("parent_run_id") and hops < 64:
                parent_id = current["parent_run_id"]
                if parent_id not in known:
                    known[parent_id] = await self.owner_of(parent_id, include_terminal=True)
                current = known[parent_id]
                if current is None:
                    break
                if current.get("conversation_id") == conversation_id:
                    found.setdefault(current["run_id"], current)
                    break
                hops += 1
        return list(found.values())

    async def runs_for_user(self, user_email: str) -> "list[Dict[str, Any]]":
        return await self.directory.live_runs_for_user(user_email)

    async def shared_now(self) -> float:
        """Seconds since the epoch on the Redis server's clock.

        Command deadlines are stamped and checked on this one clock, so wall
        clock skew between replicas cannot expire a valid command or let one
        execute after its sender reported a timeout.
        """
        seconds, micros = await self._client().time()
        return int(seconds) + int(micros) / 1_000_000

    async def _read_json(self, key: str) -> Optional[Dict[str, Any]]:
        raw = await self._client().get(key)
        if not raw:
            return None
        try:
            value = json.loads(_text(raw))
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    # -- sending ----------------------------------------------------------------

    async def send(
        self,
        owner_replica: str,
        command: str,
        run_id: str,
        actor: Dict[str, Any],
        args: Optional[Dict[str, Any]] = None,
        *,
        generation: Optional[int] = None,
        request_id: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        off_record: bool = False,
    ) -> Dict[str, Any]:
        """Send one command to ``owner_replica`` and wait for its reply.

        Returns the reply (see :func:`reply`). A reply that does not arrive by
        the deadline is ``command_timeout``: the command may or may not have
        run, and retrying with the same ``request_id`` is safe.

        ``off_record`` marks a command whose arguments carry content from an
        incognito or not-saved turn. Such content must never reach shared
        storage, and the inbox is a Redis stream, so the command is refused
        here, before anything is written (``off_record``).
        """
        if off_record:
            return reply(
                False, OFF_RECORD,
                "This message is not saved, so it cannot be sent to the run executing on "
                "another server. Wait for the run to finish, or stop it.",
            )
        request_id = request_id or uuid.uuid4().hex
        # Replies are matched to this attempt, deduplication to the request
        # id: a retry never reads the answer an earlier attempt left behind.
        attempt = uuid.uuid4().hex
        scope = command_scope(actor, request_id)
        reply_key = self.keys.control_reply(scope)
        started = time.monotonic()
        envelope = {
            "v": PROTOCOL_VERSION,
            "request_id": request_id,
            "attempt": attempt,
            "scope": scope,
            "command": command,
            "run_id": run_id,
            "generation": generation,
            "actor": actor,
            "args": args or {},
            "sent_by": self.replica_id,
        }
        try:
            await asyncio.wait_for(self._send_slots.acquire(), timeout)
        except asyncio.TimeoutError:
            # Nothing was sent, so nothing can run late.
            return reply(False, COMMAND_TIMEOUT, "Too many run-control commands are waiting; try again.")
        try:
            return await self._send_and_wait(owner_replica, envelope, reply_key, attempt, started + timeout)
        finally:
            self._send_slots.release()

    async def _send_and_wait(self, owner_replica: str, envelope: Dict[str, Any], reply_key: str,
                             attempt: str, give_up_at: float) -> Dict[str, Any]:
        """``give_up_at`` is on this process's monotonic clock. The owner's
        deadline is the same instant on the Redis clock, read after ``TIME``
        returned, so it never falls after the moment this sender gives up."""
        client = self._client()
        inbox = self.keys.control_inbox(owner_replica)
        now = await self.shared_now()
        envelope["deadline"] = now + (give_up_at - time.monotonic())
        body = json.dumps(envelope)
        if len(body.encode()) > MAX_COMMAND_BYTES:
            # Nothing was written, so nothing can run; the run is unaffected.
            return reply(False, COMMAND_TOO_LARGE, "The message is too large to send to the run's server.")
        await client.xadd(inbox, {"c": body}, maxlen=INBOX_MAXLEN, approximate=True)
        await client.expire(inbox, INBOX_TTL_SECONDS)
        last_id = "0-0"
        while True:
            remaining = give_up_at - time.monotonic()
            # Past the deadline, read once more without blocking: a reply
            # written while the last blocking read was returning would
            # otherwise be reported as unanswered, and a retry of a steer the
            # owner already queued would queue it twice.
            final = remaining <= 0
            block = None if final else max(1, min(BLOCK_MS, int(remaining * 1000)))
            entries = await client.xread({reply_key: last_id}, block=block, count=10)
            for _stream, messages in entries or []:
                for message_id, fields in messages:
                    last_id = _text(message_id)
                    answer = self._decode(fields)
                    if answer and answer.get("attempt") == attempt:
                        answer.pop("attempt", None)
                        answer.pop("request_id", None)
                        return answer
            if final:
                return reply(False, COMMAND_TIMEOUT, "The run's server did not answer in time.")

    @staticmethod
    def _decode(fields: Any) -> Optional[Dict[str, Any]]:
        raw = (fields or {}).get(b"c") or (fields or {}).get("c")
        try:
            value = json.loads(_text(raw))
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    # -- receiving --------------------------------------------------------------

    def start(self, handler: Handler) -> None:
        self._handler = handler
        if self._unsubscribe_lost is None:
            from atlas.application.chat.runs.admission import add_claim_lost_listener

            self._unsubscribe_lost = add_claim_lost_listener(self._claim_lost)
        if self._consumer is None or self._consumer.done():
            self._consumer = asyncio.create_task(self._consume())

    def _claim_lost(self, record: Any) -> None:
        """``admission.add_claim_lost_listener`` callback (sync, never blocks)."""
        self._lost_runs.add(str(record.run_id))

    async def stop(self) -> None:
        if self._unsubscribe_lost is not None:
            self._unsubscribe_lost()
            self._unsubscribe_lost = None
        tasks = [t for t in (self._consumer, *self._handlers) if t is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass  # shutting down: a handler's own failure was already logged
        self._consumer = None
        self._handlers.clear()

    async def _consume(self) -> None:
        inbox = self.keys.control_inbox(self.replica_id)
        # The id is new for this process, so everything in the inbox is ours.
        last_id = "0-0"
        delay = 0.5
        while True:
            try:
                entries = await self._client().xread({inbox: last_id}, block=BLOCK_MS, count=50)
                delay = 0.5
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A replica resumed after a stall sees its blocking read time
                # out; that needs no traceback.
                logger.warning(
                    "run-control inbox read failed (%s); retrying", type(exc).__name__,
                    exc_info=not isinstance(exc, (RedisTimeoutError, RedisConnectionError)),
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10.0)
                continue
            for _stream, messages in entries or []:
                read_ids = [_text(message_id) for message_id, _fields in messages]
                if read_ids:
                    last_id = read_ids[-1]
                    await self._forget_entries(inbox, read_ids)
                for _message_id, fields in messages:
                    envelope = self._decode(fields)
                    if envelope is None:
                        continue
                    # Bounded before a task exists: while every slot is busy
                    # the reader waits and further commands stay in Redis
                    # (their deadlines still apply) instead of piling up here.
                    await self._dispatch_slots.acquire()
                    task = asyncio.create_task(self._dispatch_held(envelope))
                    self._handlers.add(task)
                    task.add_done_callback(self._handlers.discard)

    async def _forget_entries(self, inbox: str, message_ids: "list[str]") -> None:
        """Delete inbox entries once read. Delivery is at most once per
        entry anyway (the read cursor is in memory), so keeping them would
        only retain command arguments, a steering message among them."""
        try:
            await self._client().xdel(inbox, *message_ids)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Trimmed by INBOX_MAXLEN and the key's TTL in the worst case.
            logger.warning("run-control inbox entries could not be deleted", exc_info=True)

    async def _dispatch_held(self, envelope: Dict[str, Any]) -> None:
        """Run a command whose dispatch slot the reader already acquired."""
        try:
            await self._dispatch_now(envelope)
        finally:
            self._dispatch_slots.release()

    async def _dispatch(self, envelope: Dict[str, Any]) -> None:
        # Waiting for a slot counts against the command's deadline, which
        # _execute_once checks before acting.
        await self._dispatch_slots.acquire()
        await self._dispatch_held(envelope)

    async def _dispatch_now(self, envelope: Dict[str, Any]) -> None:
        scope = envelope.get("scope")
        request_id = envelope.get("request_id")
        if not scope or not request_id or envelope.get("v") != PROTOCOL_VERSION:
            return
        actor = envelope.get("actor") or {}
        if scope != command_scope(actor, request_id):
            # A scope that does not match its own actor would let one caller
            # read or poison another's dedup record.
            logger.warning("run-control command with a mismatched scope dropped")
            return
        try:
            answer = await self._fence(envelope)
            if answer is None:
                answer = await self._execute_once(envelope, scope)
        except Exception:
            logger.exception("run-control command failed")
            answer = reply(False, "internal_error", "The command failed on the run's server.")
        await self._send_reply(scope, request_id, envelope.get("attempt"), answer)

    async def _fence(self, envelope: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Refuse a command the run's claim has moved on from, before it is recorded.

        The sender routed by the record it read. If the record no longer names
        this replica at that generation (the lease lapsed and the run was
        claimed again, or the record is gone), acting here could touch a run
        the cluster no longer counts as ours. A run whose claim this replica
        already found lost is refused without asking Redis. A retry re-reads
        the record.
        """
        run_id = str(envelope.get("run_id") or "")
        if run_id in self._lost_runs:
            return reply(False, STALE_GENERATION, "The run's ownership changed; look it up again.")
        record = await self.directory.record(run_id)
        current = None
        if record and record.get("owner") == self.replica_id:
            current = record.get("generation")
        if current is None or str(envelope.get("generation")) != current:
            return reply(False, STALE_GENERATION, "The run's ownership changed; look it up again.")
        return None

    async def _execute_once(self, envelope: Dict[str, Any], scope: str) -> Dict[str, Any]:
        client = self._client()
        done_key = self.keys.control_done(scope)
        identity = {"command": envelope.get("command"), "run_id": envelope.get("run_id")}
        marker = json.dumps({"state": "pending", **identity})
        claimed = await client.set(done_key, marker, nx=True, ex=PENDING_TTL_SECONDS)
        if not claimed:
            previous = await self._read_json(done_key) or {}
            if {k: previous.get(k) for k in identity} != identity:
                return reply(False, REQUEST_CONFLICT, "This request id was already used for another command.")
            if previous.get("state") != "done":
                return reply(False, IN_PROGRESS, "This command is already being processed.")
            answer = dict(previous.get("reply") or {})
            answer["duplicate"] = True
            return answer
        if await self.shared_now() > float(envelope.get("deadline") or 0):
            # The caller has already reported a timeout. Acting now would make
            # its answer wrong; the id is released so a retry can run it.
            await client.delete(done_key)
            return reply(False, COMMAND_EXPIRED, "The command arrived after its deadline.")
        assert self._handler is not None
        try:
            answer = await self._handler(envelope)
        except BaseException:
            await client.delete(done_key)
            raise
        uncached = answer.pop("uncached", False)
        if answer.get("ok") and not uncached:
            # Remembered: a retry gets this reply instead of acting again.
            await self._remember_done(done_key, json.dumps({"state": "done", **identity, "reply": answer}))
        else:
            # Handlers refuse before acting, so a refusal changed nothing and
            # a retry (once the queue drained, say) must be allowed to run. An
            # ``uncached`` success is idempotent on the owner (a steer: the
            # run remembers its request id), so a repeat is answered there.
            await client.delete(done_key)
        return answer

    async def _remember_done(self, done_key: str, record: str) -> None:
        """Persist an applied command's reply, retrying a transient failure.

        The command has already acted. If the write never lands, only the
        short pending marker is left, and once it expires a retry with the
        same request id would act a second time, so the write is retried for
        as long as the marker still holds the request.
        """
        delay = 0.2
        for attempt in range(DONE_WRITE_ATTEMPTS):
            try:
                await self._client().set(done_key, record, ex=DONE_TTL_SECONDS)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt == DONE_WRITE_ATTEMPTS - 1:
                    logger.warning("run-control result could not be recorded; a retry may repeat it",
                                   exc_info=True)
                    return
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5.0)

    async def _send_reply(self, scope: str, request_id: str, attempt: Any, answer: Dict[str, Any]) -> None:
        key = self.keys.control_reply(scope)
        body = json.dumps(
            {**answer, "request_id": request_id, "attempt": attempt, "replica": self.replica_id},
        )
        client = self._client()
        try:
            await client.xadd(key, {"c": body}, maxlen=20, approximate=True)
            await client.expire(key, REPLY_TTL_SECONDS)
        except Exception:
            logger.warning("run-control reply could not be sent", exc_info=True)


_bus: Optional[RedisRunControlBus] = None


def get_run_control_bus() -> Optional[RedisRunControlBus]:
    """The bus, or None on a single instance (nothing to route to)."""
    return _bus


def set_run_control_bus(bus: Optional[RedisRunControlBus]) -> None:
    global _bus
    _bus = bus
