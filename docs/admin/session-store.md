# Session Store Configuration

Last updated: 2026-08-25

## Overview

Atlas stores per-connection chat sessions (conversation history in memory, MCP
session bindings, incognito flags) in a session repository. The repository
implementation is selected at startup by the `SESSION_REPOSITORY_TYPE`
environment variable (default: `memory`).

## The Sticky-Session Constraint

The default `memory` repository is an in-process dictionary
(`InMemorySessionRepository`). Every WebSocket that connects to a different
replica sees no session state, so a multi-replica deployment must use sticky
sessions (a load balancer that routes all of a user's connections to the same
backend pod). This is an undocumented constraint that the setting exists to
remove.

## Configuring a Distributed Store

Set `SESSION_REPOSITORY_TYPE` to a custom type and register the implementation
in `atlas.infrastructure.sessions.factory.create_session_repository`:

```python
# atlas/infrastructure/sessions/factory.py

def create_session_repository(repository_type: str = "memory"):
    if repository_type == "memory":
        return InMemorySessionRepository()
    if repository_type == "redis":
        from myorg.atlas_sessions import RedisSessionRepository
        return RedisSessionRepository(url=os.environ["SESSION_REPOSITORY_URL"])
    raise ValueError(f"Unknown SESSION_REPOSITORY_TYPE={repository_type!r}")
```

An unknown type fails loudly at startup rather than silently falling back to
the in-memory store, so a misconfigured deployment is caught immediately
instead of re-introducing the sticky-session requirement.

The implementation must satisfy the `SessionRepository` protocol in
`atlas.interfaces.sessions` (`get`, `create`, `update`, `delete`, `exists`).

## Current Limitations

- Only the `memory` implementation ships in-tree. A distributed implementation
  (Redis, shared DB, etc.) must be registered by the deployment.

- Decoupling session identity from the WebSocket connection (so a reconnect
  can reattach to an existing session) is future work; today each WebSocket
  gets a fresh `session_id = uuid4()`. See issue #760 for the design direction.

- Opt-in server-side continuation of an in-flight turn after disconnect is a
  policy decision that has not been implemented. The current behavior
  (commit completed work on disconnect, stop the turn) is the "default off"
  option the issue describes.

## Related

- [Issue #760](https://github.com/sandialabs/atlas-ui-3/issues/760) - Client
  disconnect cancels and discards the in-flight turn
- [PR #776](https://github.com/sandialabs/atlas-ui-3/pull/776) - Persist
  stopped agent turns (closed #755, fixed the data-loss half of #760)