# Parallel conversation runs

Last updated: 2026-09-08

Issue #884.

A **run** is one history-mutating execution of a conversation: the agent loop
that a chat message kicks off. Before this feature the server tracked exactly
one run per WebSocket connection, which made navigation and execution the same
thing — starting a new chat, or opening a different conversation, cancelled
whatever was working.

With parallel runs:

- Different conversations execute at the same time.
- Navigation controls what is **visible**, not what is **allowed to execute**.
- Closing the browser no longer stops an agent. Reopening the conversation
  shows the progress it made while you were away.
- Stop applies to one run. Stopping conversation A leaves B alone.
- Conversations with a run in flight are marked in the history list, including
  while you are looking at a different conversation.

## When it applies

The feature is opt-in along three axes, and **all three** must hold for a turn.
Anything else keeps the previous one-run-at-a-time behaviour exactly as it was.

| Requirement | Why |
|---|---|
| `FEATURE_CHAT_HISTORY_ENABLED=true` | A run that outlives its socket is only useful if its transcript is written somewhere you can reopen. |
| The user's save mode is **Server** | `Local` and incognito keep the transcript in the browser, so a run that continued after the tab closed would produce work nobody could see. |
| The turn is an agent-mode turn with at least one tool selected | Plain completions are short; background execution is for loops that keep working. |

The orchestrator may still silently downgrade a turn out of agent mode (a model
without tool support, or tools that resolve to nothing). Such a run is admitted
but simply finishes as an ordinary turn, so it holds a run slot for the length
of one completion and nothing more.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MAX_CONCURRENT_RUNS_PER_USER` | `5` | Maximum simultaneous runs for one user. |
| `MAX_RUN_WALL_CLOCK_SECONDS` | `3600` | Hard limit on a single run's execution time. A run that exceeds it is stopped and recorded as **failed**. `0` disables it. |
| `TOOL_APPROVAL_TIMEOUT_SECONDS` | `300` | How long a run waits for a tool approval or elicitation response. `0` waits indefinitely. |

### The concurrency cap

The cap counts every run that has **not reached a terminal state**. A run paused
waiting for a tool approval is not terminal, so it counts. This is deliberate:
a paused run still holds a session, MCP clients, and conversation state.

A turn that would exceed the cap is refused with an `error` frame of type
`run_limit_exceeded` and a message naming the limit. It is not queued.

### Approvals and the timeout

`TOOL_APPROVAL_TIMEOUT_SECONDS` is the setting that decides whether an approval
survives the user going away. At the default of 300 seconds, a run paused on an
approval fails five minutes later whether or not anyone is watching. Set it to
`0` for deployments that want a run to sit paused until the user comes back and
answers; `MAX_RUN_WALL_CLOCK_SECONDS`, not the approval timeout, is then the
backstop against waiting forever.

Setting **both** to `0` leaves a run paused on an approval with no expiry at
all: it waits forever, holding a slot against the user's
`MAX_CONCURRENT_RUNS_PER_USER` cap and its conversation's one-run-at-a-time
lock for the life of the process. Atlas logs a warning at startup for that
combination rather than refusing to start, since an operator may have chosen it
deliberately — but leave at least one of the two non-zero unless you have.

An elicitation or approval can only be answered by the user it was created for.
A response arriving from any other account is rejected and logged, which matters
most when the wait is indefinite: the request id stays answerable for as long as
the process lives.

Pending approvals live in memory. They survive a disconnect but **not** a server
restart — see "Limitations" below.

## Run states

```text
queued -> running -> waiting_for_input -> running -> completed
                  \-> failed
                  \-> cancelled
```

A run stopped by the wall-clock limit is recorded as `failed`, not `cancelled`:
`cancelled` means a person stopped it.

`waiting_for_input` means the run is paused on a tool approval or an MCP
elicitation. It is the state that drives the amber "Needs approval" marker in
the conversation list.

Terminal runs stay queryable for 30 minutes so a browser reopened shortly after
a run finished can still show how it ended.

## Protocol

Client to server:

| Message | Purpose |
|---|---|
| `list_runs` | Ask for every run the user can still see. Sent on connect. |
| `stop_streaming` / `agent_control` (`action: "stop"`) | Now accept `run_id` and/or `conversation_id`. A frame with neither falls back to the connection's untracked turn, which is what older clients send. |
| `tool_approval_response` / `elicitation_response` | May carry `run_id` so the right run is resumed when several are paused. |

Server to client:

| Message | Purpose |
|---|---|
| `run_started` | A turn was admitted as a tracked run. |
| `run_status` | One run changed state. Sent to **every** connection belonging to the owner, which is how another open tab keeps its indicators current. |
| `runs_snapshot` | Reply to `list_runs`; also carries `max_concurrent_runs_per_user`. |

`run_started` also carries the conversation id. A brand-new chat has none of its
own yet — the client only learns one when the turn is saved — so the server
mints one at admission and reports it here. Without that, the first agent turn in
a new conversation could never be a background run, which is the most common case
of all.

### Pending approvals are replayed, not lost

An approval request that arrives while the user is looking at another
conversation is not shown as a modal there; the conversation is marked "Needs
approval" in the history list instead. The server keeps the request frame and
re-sends it when that conversation is opened (`restore_conversation`) or named on
`list_runs` after a reconnect, so it can still be answered. Without the replay
the request id and arguments would exist nowhere the client could reach, and the
run would sit blocked until it timed out.

Every event a tracked run emits — tokens, agent updates, tool rows, files,
canvas, completion, errors — carries `run_id` and `conversation_id`. Tagging
happens in the WebSocket connection adapter, driven by a context variable bound
inside each run's task: the agent loop publishes most of its output through a
connection-scoped event publisher rather than the turn's own callback, so
stamping at the one transport chokepoint is what keeps two concurrent runs
distinguishable. The client
uses them to route events, and to discard events for a conversation it is not
displaying rather than splicing them into the visible transcript.

## Resource ownership

A run owns its conversation's resources until it finishes. Neither starting a
new chat nor opening a different conversation releases the MCP sessions of a
conversation that still has a live run, and a disconnect leaves those sessions
in place. Each run also gets its **own** `Session` object, so two concurrent
conversations never write through the same history.

## Limitations

These are known and deliberate, not oversights:

- **Runs do not survive a server restart.** The registry is in-memory. A restart
  loses in-flight runs and any approval or elicitation waiting on one. Making
  them durable requires a persisted run store and persisted pending-request
  records.
- **A reconnected browser does not resume a live event stream.** It sees the run
  in `runs_snapshot` and, on reopening the conversation, the transcript the run
  has saved so far — not the tokens it missed. Live re-attach is issue #760.
- **Multi-process deployments track runs per process.** A user whose second
  connection lands on a different worker will not see the first worker's runs.
  Use a single worker, or sticky sessions, until the run store is shared.

## Troubleshooting

**"This conversation already has a run in progress."**
A second message for a conversation that is already running is steered into the
running agent (issue #824), not started as a second turn. This error appears
only when the running loop is not draining its steering channel — it is starting
up, or paused on an approval. Answer the approval, or stop the run.

**A conversation is stuck showing "Running".**
Check the server log for the run id. The sweeper stops runs past
`MAX_RUN_WALL_CLOCK_SECONDS` and marks them failed; if that is set to `0`,
nothing will.

**Runs are not being created at all.**
All three conditions under "When it applies" must hold. The most common cause is
a user whose save mode is Local or Incognito rather than Server.
