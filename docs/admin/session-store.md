# Session Store Configuration

Last updated: 2026-10-20

## Overview

Atlas keeps a session for every chat WebSocket: the in-memory conversation
history the next turn is built from, the file map (attachments and tool
artifacts), the conversation, workspace and data-classification bindings, and
the incognito state. The session repository that holds them is selected at
startup by `SESSION_REPOSITORY_TYPE`:

| Value | Scope | Use for |
|-------|-------|---------|
| `memory` (default) | One process | Development and single-instance deployments |
| `redis` | Shared by every replica | Multi-replica deployments (issue #1091) |

Conversations themselves are always stored by
[chat history](chat-history.md) (DuckDB or PostgreSQL). The session store is a
cache of live state on top of that record, not a second record: losing it
loses only state a reconnecting client rebuilds from the database.

## Process-local store (`memory`)

The default keeps sessions in a dictionary inside the API process. A client
that reconnects to the same process resumes its session; a client that lands
on another replica starts a new one and its next turn reloads the conversation
from chat history. Multi-replica deployments on this store need sticky
sessions so a user's sockets reach the same process.

## Shared store (`redis`)

```bash
SESSION_REPOSITORY_TYPE=redis
REDIS_URL=rediss://atlas:${REDIS_PASSWORD}@redis.internal:6380/0
REDIS_KEY_PREFIX=atlas-prod
```

| Variable | Default | Meaning |
|----------|---------|---------|
| `REDIS_URL` | (none; required) | `redis://`, `rediss://` (TLS) or `unix://`. Credentials go in the URL; logs and `/api/health` print it with the password masked. |
| `REDIS_KEY_PREFIX` | `atlas` | Namespace for every key. Give each deployment sharing a Redis server its own prefix. |
| `REDIS_MAX_CONNECTIONS` | `20` | Connections each process may hold. The pool blocks rather than growing past it. |
| `REDIS_POOL_TIMEOUT` | `5` | Seconds a command waits for a free pooled connection before failing. |
| `REDIS_SOCKET_TIMEOUT` | `5` | Seconds to wait for a reply. |
| `REDIS_CONNECT_TIMEOUT` | `5` | Seconds to wait when opening a connection. |
| `REDIS_HEALTH_CHECK_INTERVAL` | `30` | Seconds between liveness checks on an idle pooled connection (`0` disables). |
| `REDIS_TLS_CA_CERTS` | (none) | CA bundle that signs the server certificate, for `rediss://`. |
| `SESSION_TTL_SECONDS` | `86400` | A stored session expires this long after its last write. |

Each API process opens one bounded pool, shared by every Redis-backed feature.
The deployment's Redis connection budget is:

```
replicas x uvicorn workers per replica x REDIS_MAX_CONNECTIONS
```

Keep it below the server's `maxclients` with room for administration.

### Startup and shutdown

Startup pings Redis before the app takes traffic. If it is unreachable the
process exits with `STARTUP FAILED: Redis at ... is unreachable`; it never
falls back to the process-local store, because a replica serving sessions
other replicas cannot see would bring back the problem the setting removes.
A missing or malformed `REDIS_URL` fails the same way. Shutdown closes the
pool.

### What is stored

One hash per session, `<prefix>:session:<session id>`, with two fields:
`data` (the session as JSON) and `version`. Every write refreshes the key's
TTL.

Only serializable session data is written: identity and owner, history,
and JSON-shaped context entries. Process-local runtime handles (a turn's
citation register, task or channel objects) are left out; any context entry
that cannot be expressed as JSON is dropped from the stored copy with a
warning naming the key. **Incognito sessions are stored without their
messages**: incognito promises the conversation is not stored, so only its
context (the incognito flag, save floor and bindings) is written, and a
resumed incognito session continues with an empty history, as a reconnect
always did.

### Concurrent updates

Each write carries the version the copy was read at and is applied in a Redis
transaction (`WATCH`/`MULTI`) only if the stored version still matches.
A stale copy is refused (`SessionConflictError`, logged and counted as
`session_conflicts`) instead of replacing newer state. Writes from one process
to one session are serialized, so a process never conflicts with itself.

Inside a process the chat pipeline mutates the session object it was handed.
The repository returns the same object for sessions that are live on that
replica (created or last written there and still active) and reads other
sessions from Redis. `ChatService` passes its session object to the
orchestrator explicitly, so a turn never depends on two lookups returning the
same object.

Sessions are written at turn boundaries: after every turn (completed,
stopped, or failed in the model call), after attaching a file, after
restoring a conversation, on New Chat, after a tracked run hands its files
back, and when the socket closes.

### Reconnecting to another replica

The browser remembers the session id the server sends in a
`connection_session` frame and names it when its socket reconnects
(`/ws?resume_session=<id>`). The replica that receives the reconnect, any
replica, resumes the session when:

- it exists in the store,
- it belongs to the authenticated user (another user's id is refused and
  logged), and
- it is a connection's session, not a tracked run's.

Resuming stamps the new connection as the session's owner and writes it, so
the old connection's late teardown leaves it alone, and its stale copy can no
longer overwrite it. The resumed turn is still subject to the conversation's
recorded data classification, which travels with the session. Anything that
fails (unknown id, refused, store unreachable, lost write race) starts a fresh
session, which reloads the conversation from chat history on its next turn.

A page reload starts a new UI and a new session; only reconnects within a
page resume.

### Outages

| Situation | Behavior |
|-----------|----------|
| Redis down at startup | Startup fails. |
| Redis down, session live on this replica | Turns continue from the local copy; each session write fails, is logged at ERROR and counted. The conversation is still saved to chat history. |
| Redis down, session not on this replica | Reads raise `SessionStoreUnavailableError`; a reconnect starts a fresh session and the next turn rebuilds context from chat history. |
| Redis restarted or flushed | Stored sessions are gone; live replicas re-store their sessions on the next write. Reconnects start fresh. |
| Session idle past the TTL | Expired; a replica still holding it live re-stores it on the next write. |

Nothing falls back to a process-local store, and no write silently replaces
newer state.

### Observability

`GET /api/health` includes a `session_store` block when the shared store is
configured: reachability, latency, the masked URL, pool size, command error
count and last error type, `session_conflicts`, `session_expired_while_live`,
`session_decode_errors`, and the number of sessions live on that replica. An
unreachable store reports `"status": "degraded"` with HTTP 200, so a load
balancer does not pull every replica during a Redis outage; alert on the
field instead.

### Kubernetes notes

- Run Redis as a managed service or a StatefulSet with persistence matching
  how much a Redis restart may cost (losing sessions costs only resumable
  state; see above). Use `rediss://` across untrusted networks.
- Set `REDIS_KEY_PREFIX` per environment if environments share a Redis.
- Size `REDIS_MAX_CONNECTIONS` from the budget formula above.
- Rolling deploys: a replica that is stopping closes its sockets; clients
  reconnect to a remaining replica and resume.

## Limits of the shared store

The shared store moves **session state**. It does not move live work:

- **Tracked agent runs** (parallel conversation runs), their pending approvals,
  stop/steer routing and the per-user run cap are still process-local (see
  [Parallel Conversation Runs](parallel-conversation-runs.md)). A user who
  reconnects to another replica does not see a run that is executing on the
  first one until it finishes and its conversation is saved. Deployments that
  rely on tracked runs should keep sticky sessions until distributed run
  coordination (issue #1091, later phases) lands.
- **An in-flight untracked turn** is cancelled when its socket closes, and its
  completed work committed, as before; the resumed session carries the
  committed state.
- **MCP client sessions** in the backend are per process; a resumed session
  opens new ones on the replica it lands on.
- **`MCP_STATE_BACKEND=redis`** is a separate, older setting for the
  FastMCP server-side state of bundled MCP servers (`MCP_REDIS_URL`). It does
  not share Atlas sessions, and `SESSION_REPOSITORY_TYPE=redis` does not
  configure it. Pointing both at one Redis is fine; keep them on separate
  database numbers or rely on the `REDIS_KEY_PREFIX` namespace.

## Adding another store

A further implementation registers in
`atlas.infrastructure.sessions.factory.create_session_repository` and must
satisfy the `SessionRepository` protocol in `atlas.interfaces.sessions`
(`get`, `create`, `update`, `delete`, `exists`). An unknown type fails at
startup rather than falling back to the in-memory store. A shared store must
handle `update` of a stale copy (refuse it or merge it) and must not return
process-local objects from another process.

## Related

- [Chat History Persistence](chat-history.md) - The durable conversation
  record and the PostgreSQL connection budget.
- [Issue #1091](https://github.com/sandialabs/atlas-ui-3/issues/1091) -
  Multi-replica readiness.
- [Issue #760](https://github.com/sandialabs/atlas-ui-3/issues/760) - Client
  disconnect cancels and discards the in-flight turn.
