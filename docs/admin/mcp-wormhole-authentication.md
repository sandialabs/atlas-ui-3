# Wormhole MCP Authentication

**Created:** 2026-06-09
**PR:** #640
**Related issue:** #640 (Genesis Mission Wormhole Support)

## Overview

A *Wormhole*-wrapped Atlas instance runs behind a Wormhole proxy that
authenticates the user. The subtoken originates as a field in the JWT the
Wormhole wrapper receives, but the wrapper **unpacks it into the incoming HTTP
request as the `x-subtoken` header** before the request reaches Atlas. Atlas
therefore reads it as a plain request header and does **not** decode the JWT
itself. Downstream MCP servers that are themselves Wormhole-enabled require that
subtoken in order to authorize calls made on the user's behalf.

This feature captures the `x-subtoken` header from each authenticated request and
forwards it to the MCP servers that opt in, as an `X-Token` header, when Atlas
opens a streamable-HTTP connection. (Reading from the header and forwarding as
`X-Token` were both confirmed by the issue author in
[#640](https://github.com/sandialabs/atlas-ui-3/issues/640).)

The subtoken is:

- **session-scoped and short-lived** — its lifetime is managed by Wormhole;
- **the same value for every Wormhole-enabled MCP server** in a session;
- **never persisted to disk** — it lives only in process memory; and
- **only logged masked** (first/last few characters).

This is distinct from the per-user API-key/JWT/bearer mechanism described in
[mcp-server-authentication.md](./mcp-server-authentication.md), where each user
manually supplies and stores a credential per server. Wormhole subtokens are
captured automatically from the request and shared across all Wormhole servers.

## Enabling

Wormhole support is **off by default**. Enable it with environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `FEATURE_WORMHOLE_ENABLED` | `false` | Master switch for capture + forwarding |
| `WORMHOLE_SUBTOKEN_HEADER` | `x-subtoken` | Incoming header carrying the subtoken |
| `WORMHOLE_FORWARD_HEADER` | `X-Token` | Header used to forward it to MCP servers |

## Marking a server as Wormhole-enabled

Add `"wormhole": true` to the server entry in `mcp.json`. The server must use
HTTP/streamable-HTTP transport:

```json
{
  "servers": {
    "genesis_tools": {
      "url": "https://genesis.example.gov/mcp",
      "transport": "http",
      "wormhole": true
    }
  }
}
```

Wormhole forwarding composes with the existing `auth_type` mechanism: if a server
is both Wormhole-enabled and uses `api_key`/`bearer`/`jwt` auth, the subtoken
rides as an additional `X-Token` header alongside the primary credential.

## How it works

```
Wormhole proxy (validates user, mints JWT with x-subtoken)
        │
        ▼
Atlas WebSocket handshake / HTTP request
        │   capture_subtoken_from_headers(headers, user_email)
        ▼
WormholeTokenStore  (in-memory, keyed by normalized user email)
        │
        ▼
MCPToolManager._get_or_create_user_http_client(server, user, conversation)
        │   _build_wormhole_headers() -> {"X-Token": "<subtoken>"}
        ▼
StreamableHttpTransport(url, headers={"X-Token": ...}) -> FastMCP Client
        │
        ▼
Wormhole-enabled MCP server
```

1. **Capture.** On each authenticated WebSocket handshake
   (`atlas/main.py:websocket_endpoint`) and HTTP request
   (`atlas/core/middleware.py:AuthMiddleware`), Atlas reads the configured
   subtoken header and stores it for the authenticated user in
   `WormholeTokenStore` (`atlas/modules/mcp_tools/wormhole_token_store.py`). The
   latest value seen for a user wins, which naturally handles Wormhole rotating
   the token.

2. **Forward.** When a tool call needs a connection to a Wormhole-enabled server,
   `MCPToolManager` looks up the user's subtoken and adds it as the forward
   header on a `StreamableHttpTransport`
   (`atlas/modules/mcp_tools/client.py`).

3. **Rotation.** Per-conversation HTTP clients are cached. The subtoken baked
   into each cached client is tracked; if the captured subtoken changes (for
   example, the user reconnects with a fresh token), the stale client is torn
   down and rebuilt so the new subtoken is used.

## Security notes

- The subtoken is never written to disk and is dropped when the process exits.
- Logs only ever contain a masked form of the subtoken.
- A request that arrives without the subtoken header clears any previously stored
  value for that user, so a stale token is not left behind.
- If no subtoken is available for a user, Atlas connects without the forward
  header; the Wormhole MCP server is then responsible for rejecting the
  unauthenticated call (`MCP session rejected (check authentication/token)`).

## Limitations

- Forwarding requires per-user context, so it applies to user-initiated tool
  calls. The shared client created at startup for tool discovery does not carry a
  subtoken; a Wormhole MCP server that rejects unauthenticated `list_tools` will
  fail discovery until a per-user connection is made — the same limitation that
  applies to other per-user-authenticated servers.
