# Consolidating the built-in tools into one ATLAS server (#855)

**Date:** 2026-08-27
**Issue:** [#855](https://github.com/sandialabs/atlas-ui-3/issues/855)

## The problem

ATLAS grew three built-in, non-MCP "pseudo-servers" one at a time:

| Server | Tool(s) | Added by |
| --- | --- | --- |
| `canvas` | `canvas_canvas` | original UI |
| `atlas_agent` | `atlas_agent_sleep` | #779 |
| `atlas_rag` | `atlas_rag_discover_data_sources`, `atlas_rag_query` | RAG tool work |

In the tools panel that read as three separate servers for four tools, and each
one carried its own copy of the "this is a pseudo-server, special-case it"
logic — discovery, schema building, the tool index, the execution dispatch, the
authorization allow-list and the `/api/config` payload each branched on server
name in a slightly different way.

Separately, the chat input carried a magnifying-glass button that toggled RAG
on and off. It did two unrelated things at once (enable RAG, and open the data
source panel), and it duplicated the tools panel: whether ATLAS searches is a
question about which *tool* is on, not a second switch beside the text box.

## What changed

One built-in server, `atlas`, with three tools:

| Tool | Arguments |
| --- | --- |
| `atlas_canvas` | `content` |
| `atlas_sleep` | `seconds`, optional `reason` |
| `atlas_search` | `query` |

`atlas/modules/mcp_tools/atlas_server.py` is the single source of truth: the
server name, the three schemas, the legacy-name alias table and the helpers
(`normalize_tool_name`, `is_atlas_tool`, `atlas_tool_schemas`) that the
discovery, execution, authorization and config layers all call. The frontend
mirror lives in `frontend/src/constants/atlasTools.js`.

The magnifying-glass button is gone. The data source panel is still reachable
from the header ("Sources"), which is where selecting sources belonged.

### `atlas_search` takes only a query

The old `atlas_rag_query` accepted `data_sources` and `mode` as well. Both are
now server-side decisions:

* **Sources** come from the user's current selection in the RAG panel. If
  nothing is selected, the turn falls back to every source the user is
  authorized for — the frontend marks the turn RAG-activated whenever
  `atlas_search` is among the selected tools, so "search on, nothing picked"
  means "everything I can reach" rather than "nothing".
* **Mode** is always `raw`. A tool call should hand the model evidence to
  reason over, not a backend-written answer.

Model-supplied `data_sources`/`mode` on an `atlas_search` call are ignored
outright. This is defence in depth — the authorization gate already intersects
any requested source with the user's discovered set — but a model should not be
able to reach past the user's selection to another source the user happens to
be authorized for.

`atlas_rag_discover_data_sources` is no longer advertised: with search reading
the UI selection there is nothing left for a discover step to feed. It still
executes, so a saved conversation that replays it does not come back broken.

### Backwards compatibility

Tool selections live in `localStorage` and in saved conversations, so the old
fully-qualified names are still accepted everywhere and normalized to the new
ones at the edges:

```
canvas_canvas      -> atlas_canvas
atlas_agent_sleep  -> atlas_sleep
atlas_rag_query    -> atlas_search
```

A browser holding `['canvas_canvas', 'math_add']` gets `atlas_canvas` selected
on first render, with no reset and no lost selection.

### Gating

The built-in server is authorized for every user, but its individual tools are
gated the way they were before — the difference is that a disabled tool now
drops out of the tool list instead of the whole server disappearing:

* `atlas_sleep` — requires `AGENT_SLEEP_MAX_SECONDS > 0`
* `atlas_search` — requires `feature_rag_enabled` **and**
  `feature_atlas_rag_tools_enabled`
* `atlas_canvas` — always available

A disabled built-in is omitted from the schema rather than advertised and then
refused: agent mode reaches the loop without ACL filtering, so a tool in the
schema costs a step before execution can reject the call.

Omission is not the only gate, though. A replayed conversation or a non-UI
client can name a tool the schema never advertised, so `execute_tool` refuses a
disabled `atlas_sleep` or `atlas_search` outright with an error naming the
switch that turned it off.

### Two things the rename could have broken quietly

* **Hook matchers.** `matcher` in `config/hooks.json` is an operator-written
  regex over the tool name, and a `PreToolUse`/`PermissionRequest` hook may be
  a deny or approval policy. `HookConfig.matches` now tests the legacy spelling
  as well, so a hook targeting `canvas_canvas` keeps firing on `atlas_canvas`.
  The aliasing runs one way — a matcher on the *new* name does not suddenly
  match the old one — so it cannot widen a hook's reach.
* **A configured server named `atlas`.** The built-in names short-circuit the
  tool index, `/api/config` and execution dispatch, so an `mcp.json` server
  called `atlas` (or `canvas`/`atlas_agent`/`atlas_rag`) would connect, get
  discovered, and then be unreachable. Those names are now reserved:
  `_drop_reserved_servers` removes them at config load and logs an error naming
  the collision.

## Open question

The issue text trails off after "The `search` tool should take a single
argument — `query`." Two judgement calls were made in that gap and are worth a
second look:

1. **Empty selection means "all authorized sources"**, matching what the old
   RAG toggle did when it was on with nothing picked. The alternative reading —
   empty means search nothing — would make a freshly-enabled search tool return
   nothing until the user opens the sources panel.
2. **`atlas_rag_discover_data_sources` was dropped from the advertised set**
   rather than kept as a fourth tool. It has no job left once search reads the
   UI selection.
