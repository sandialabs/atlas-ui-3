# Built-in agent sleep tool (issue #779)

Date: 2026-08-18

## Problem

An agent that kicks off long-running external work -- a simulation, a submitted
batch job, a remote process -- has no way to wait for it. It can only keep
calling tools, and the natural "poll again in a few minutes" step does not
exist.

A sleep implemented as an ordinary MCP server does not solve this. Tool calls
go through `MCPToolManager` with `MCP_CALL_TIMEOUT` (120s by default), so any
wait longer than that is killed by the transport rather than by policy, and the
waits that matter here are 5-20 minutes, sometimes hours.

## What was added

`atlas_agent_sleep`, a built-in pseudo-tool that runs in process, alongside the
existing `canvas_canvas` and `atlas_rag_*` pseudo-tools. There is no MCP server,
no subprocess, and no HTTP round trip -- `MCPToolManager.execute_tool`
short-circuits on the tool name and awaits `asyncio.sleep`, so no transport
timeout applies.

- **Implementation**: `atlas/modules/mcp_tools/sleep_tool.py` (schema, validation,
  the wait itself).
- **Schema/index**: `mcp_discovery.py` returns the schema from
  `get_tools_schema` and maps the tool to the `atlas_agent` pseudo-server.
- **Dispatch**: `mcp_execution.py` routes the call before any server lookup.
- **ACL**: `tool_authorization.py` allows it whenever it is enabled -- there is
  no MCP server behind it to authorize against.
- **Tools panel**: `config_routes.py` exposes an `atlas_agent` pseudo-server so
  the tool is selectable like any other.

Arguments: `seconds` (required, > 0) and an optional `reason` note that is
echoed back in the result.

## The three decisions

**Maximum wait.** `AGENT_SLEEP_MAX_SECONDS` (default 7200 = 2 hours) caps a
single call. Requests above the cap are clamped rather than rejected, and the
result says so explicitly ("call this tool again to keep waiting"), which keeps
a polling agent moving instead of ending its turn on a tool error. Invalid
durations -- zero, negative, non-numeric, NaN, and `true` -- are tool errors.

That per-call cap bounds nothing on its own, precisely because the clamp
message invites another call: with the shipped defaults a turn could take
`AGENT_MAX_STEPS` x 7200s of held connection, session, and MCP-client state.
So `AGENT_SLEEP_MAX_TURN_SECONDS` (default 7200) bounds the *turn*. The agent
loop creates one scratchpad dict per turn and passes it down through
`session_context` -> `execute_tool`'s context; the tool accumulates slept
seconds in it. A call that would exceed what is left is clamped to the
remainder, and once the budget is spent the tool returns an error worded
without the "call again" invitation. The scratchpad is a local in
`_run_steps`, not an attribute on the loop: `AgentLoopFactory` caches one loop
object and reuses it across turns, so state on `self` would leak one turn's
budget into the next. Outside the agent loop (tools mode, a direct call) no
scratchpad is supplied and the per-call cap is the only bound.

**Who enforces "disabled".** Agent mode is the mode this tool exists for, and
it never runs `filter_authorized_tools` -- the orchestrator applies that only
on the non-agent branch. So the gate that decides whether a disabled tool is
advertised to the model is `get_tools_schema`, which omits the schema when the
cap is 0; the ACL check still covers tools mode and execution refuses the call
regardless. Without the schema gate, `AGENT_SLEEP_MAX_SECONDS=0` would still
cost a step before anything refused.

**Client-supplied step counts.** `agent_max_steps` arrives verbatim in the
WebSocket payload and was never bounded by the configured `AGENT_MAX_STEPS`.
That predates this tool, but a 7200s in-process wait per step turns it into a
cheap way to pin server-side state, so `ChatOrchestrator._bounded_agent_steps`
now clamps it at the routing choke point (covering the CLI and API callers,
not just the WebSocket).

**Kill switch.** `AGENT_SLEEP_MAX_SECONDS=0` disables the tool entirely: it
disappears from the tools panel, is dropped by ACL filtering, and is refused at
execution. One knob is both the cap and the switch.

**Cancellation.** Nothing was threaded through the agent loop. Stopping a run
cancels the asyncio task driving the turn, and `asyncio.sleep` raises
`CancelledError` at the await point, so an in-flight sleep aborts with the rest
of the turn. `test_sleep_aborts_when_the_run_is_cancelled` pins this behavior.

## Known limits

- The turn holds its WebSocket connection open for the duration of the sleep. A
  proxy with an idle timeout shorter than the wait can drop the connection
  before the sleep returns; no keepalive or progress event is emitted during the
  wait. The UI shows a static `CALLING` badge for the duration, with no elapsed
  time, which is hard to tell apart from a hung backend.
- A turn parked in a long sleep delays graceful shutdown, and Uvicorn runs
  without `timeout_graceful_shutdown`, so a rolling deploy will SIGKILL it.
  Sleeping in chunks against a shutdown flag would fix both this and the
  missing progress signal; it is the obvious follow-up and is deliberately not
  in this change.
- The sleep occupies one agent step out of `AGENT_MAX_STEPS`, so a long poll
  loop spends its step budget on waiting.
- Approval, if enabled for the tool, is requested before the wait starts and
  keeps its usual 300s approval timeout.
