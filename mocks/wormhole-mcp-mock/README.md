# Wormhole MCP Mock

A test double for the **external** service in the Genesis Mission Wormhole flow
(issue #640): a streamable-HTTP MCP server that expects the per-session Wormhole
subtoken to arrive as an `X-Token` HTTP header.

In production, a Wormhole proxy authenticates the user, unpacks the subtoken from
the JWT into the `x-subtoken` request header, and Atlas forwards it to
Wormhole-enabled MCP servers as `X-Token`. This mock stands in for such a server
so the Atlas capture-and-forward path can be validated end to end without any
real Wormhole infrastructure.

## What it does

- Reads the forward header (default `X-Token`, set via `WORMHOLE_FORWARD_HEADER`)
  on every MCP request and records what it saw.
- Tools:
  - `whoami` — reports whether the subtoken arrived (and its masked value).
  - `get_protected_resource` — returns a payload only when the subtoken is
    present; otherwise returns `MCP session rejected (check authentication/token)`,
    mirroring a real Wormhole MCP server.
- HTTP endpoints (outside MCP):
  - `GET /status` — HTML dashboard of observed requests + the latest E2E report
    (used for screenshots).
  - `GET /log` — JSON view of observed requests (used for E2E assertions).
  - `GET /health`, `POST /report`, `POST /reset`.

It performs **no real authorization** — it only observes and echoes the
forwarded header so the Atlas side of the flow can be verified.

## Running

```bash
cd mocks/wormhole-mcp-mock
python main.py --port 8021        # MCP at /mcp, dashboard at /status
```

Point a Wormhole-enabled Atlas server entry at it (see `mcp-config-example.json`):

```json
{ "wormhole_demo": { "url": "http://127.0.0.1:8021/mcp", "transport": "http", "wormhole": true } }
```

## End-to-end test

`run_e2e.sh` starts the mock, runs `e2e_wormhole_test.py` (which drives the real
Atlas capture → forward path), prints a PASS/FAIL summary, and leaves the
dashboard populated for inspection:

```bash
cd mocks/wormhole-mcp-mock
./run_e2e.sh
```

The driver exercises three scenarios against the running mock:

1. **Subtoken captured** → Atlas forwards it as `X-Token`; the mock authorizes.
2. **No subtoken** (header absent) → nothing forwarded; the mock rejects the call.
3. **Subtoken rotation** → a new value forwards the new `X-Token` (the cached
   per-conversation client is rebuilt).

The mock's `/log` endpoint is the source of truth, so the assertions verify the
header the external service actually received — not just the Atlas side.
