# Agent Runs REST API

Last updated: 2026-10-29

The agent-runs API lets one trusted external service -- a scheduler, a CI job, a webhook
relay -- start an ATLAS agent conversation on behalf of a user, with no browser or CLI session,
then poll it, send it follow-up messages, and stop it (issue #1105). It is the REST form of the
sub-conversation tools a conversation already has (`atlas_discover_launch_options`,
`atlas_launch`, `atlas_get_runs`, `atlas_result`, `atlas_follow_up`), and runs on the same
machinery: the same workspace and model checks, the same re-filtering of tools and data sources
through the user's ACLs, and the same run registry and concurrency caps.

## Enabling it

```bash
# Off by default. Also requires chat history and agent mode.
FEATURE_AGENT_RUNS_API_ENABLED=true
# The master key, at least 32 characters. Generate one with:
#   python -c "import secrets; print(secrets.token_urlsafe(48))"
AGENT_RUNS_API_KEY=...
```

- **One key, one trusted service, any user.** The key is a service credential, not a user
  token: whoever holds it can launch runs as *any* user, using that user's workspaces, models,
  tools and data sources. Treat it like a root credential, keep it in a secret store, and rotate
  it by changing the variable and restarting. Per-user tokens, scopes and delegation allowlists
  are not part of this first version.
- **Fail closed.** With the feature off, every `/api/agent-runs` path returns 404. With it on
  but the key unset or shorter than 32 characters, every request returns 503 and the server logs
  an error; nothing is served unauthenticated.
- **The key is the only credential.** `/api/agent-runs` is exempt from the browser
  authentication middleware: the identity header, the proxy secret and an OIDC session neither
  grant access nor are required. A signed-in user's browser therefore cannot call these
  endpoints. If a reverse proxy enforces its own login on every path, exempt `/api/agent-runs`
  from it (as for `/mcp/files/download/`) so the service can reach the backend with its bearer
  key. The key is compared in constant time and never logged.
- **The email domain whitelist still applies, to the target user.** With
  `FEATURE_DOMAIN_WHITELIST_ENABLED`, the browser path checks the signed-in user in middleware;
  the service has no signed-in user, so the whitelist is applied to the `user` a request acts as
  instead, and a user outside it is refused with `403 user_not_allowed`.
- `AGENT_RUNS_API_KEY` is masked in the admin panel's configuration view like other keys.

## Endpoints

All requests carry `Authorization: Bearer <AGENT_RUNS_API_KEY>`. The full schema is in the
server's OpenAPI document (`/openapi.json`, browsable at `/docs`) under the `agent-runs` tag.

| Method and path | Mirrors | Purpose |
|---|---|---|
| `GET /api/agent-runs/options?user=` | `atlas_discover_launch_options` | Workspaces and models the user may launch with |
| `POST /api/agent-runs` | `atlas_launch` | Start a run in a new top-level conversation |
| `GET /api/agent-runs?user=` | `atlas_get_runs` | API-started runs still retained (optionally one user's) |
| `GET /api/agent-runs/{run_id}` | `atlas_result` | Status, timestamps, error and final reply |
| `POST /api/agent-runs/{run_id}/follow-up` | `atlas_follow_up` | Next message in that run's conversation |
| `POST /api/agent-runs/{run_id}/cancel` | Stop in the UI | Stop the run and anything it launched |

### Launch

```bash
curl -sS -X POST https://atlas.example.com/api/agent-runs \
  -H "Authorization: Bearer $AGENT_RUNS_API_KEY" -H "Content-Type: application/json" \
  -d '{
        "user": "user@example.com",
        "workspace": "Build triage",
        "model": "gpt-4o",
        "message": "Analyze the failed build and summarize the failures",
        "compliance_level": "Internal",
        "idempotency_key": "build-1234"
      }'
```

Returns `202 Accepted` as soon as the run is admitted, with a `Location` header and a handle:
`run_id`, `conversation_id`, `status`, `status_url`, the resolved `workspace`, `model`, `tools`
and `data_sources`, and `tools_needing_approval`. The run keeps going after the response.

- `workspace` is one of the user's saved workspaces, by id or name. It is the run's whole
  capability surface: its tools (re-filtered through the user's ACLs now) and, when the
  workspace has RAG switched on, its data sources (authorized for the user and compliance level).
- `model` must be one the user may use; an unknown model and a forbidden one get the same answer.
- `compliance_level` is the conversation's classification. It is validated, and the model, tools
  and data sources are checked against it, before the run is admitted; it is then fixed for the
  conversation -- follow-ups always run at it.
- The run is a top-level conversation in the user's history (no parent), titled by its message,
  and shows in their sidebar while it runs. The user can open it, watch it, and continue it.

### Poll, follow up, cancel

`GET /api/agent-runs/{run_id}` returns `status` (`queued`, `running`, `waiting_for_input`,
`completed`, `failed`, `cancelled`), `waiting_on`, `created_at` / `updated_at` / `ended_at` (Unix
seconds), `error`, and once completed `result` with `result_status` (`available`,
`available_truncated` past 20,000 characters, `empty`, `unavailable`).

`POST /api/agent-runs/{run_id}/follow-up` with `{"message": "..."}` starts the next turn of that
run's conversation, as the conversation's owner, on the workspace, model and compliance level it
was launched with -- each re-checked against the owner's access now. The conversation's stored
history is loaded first, so the model sees the whole transcript. A follow-up is refused while the
conversation has a run in progress or is waiting for the user, and for a conversation whose
launch never completed. Any of the conversation's run ids may be used; the newest one's
capabilities are the ones carried forward.

`POST /api/agent-runs/{run_id}/cancel` stops the run (and any sub-conversations it launched with
`atlas_launch`), exactly like the stop button. `cancelled` says whether anything was still live.
As with follow-ups, any of the conversation's run ids may be used: if the run named has already
finished, the conversation's in-progress API run (a later follow-up) is stopped instead, and the
response describes that run. A turn the user started themselves in ATLAS is never stopped.

### Errors

Refusals are JSON `{"detail": {"code": "...", "message": "..."}}`:

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `invalid_request` | Missing or oversized field |
| 401 | `unauthorized` | Missing or wrong bearer key |
| 403 | `user_not_allowed` | The target user is outside the email domain whitelist |
| 404 | `not_found` | The API is disabled |
| 404 | `workspace_not_found` | No such workspace *for that user* (another user's workspace looks the same) |
| 404 | `run_not_found` | Unknown run, expired, or not started through this API |
| 409 | `approval_required` | The workspace has approval-mandatory tools (see below) |
| 409 | `conversation_busy` | The conversation already has a run in progress |
| 409 | `waiting_for_input` | The conversation is paused on the user |
| 409 | `not_continuable` | Nothing to follow up on |
| 409 | `idempotency_conflict` / `idempotency_in_progress` | Key reused with a different body / first request still admitting |
| 422 | `model_not_available` / `no_authorized_tools` / `classification_refused` | The user may not use the model, has no usable tools in the workspace, or the classification does not allow a component |
| 429 | `concurrency_limit` | The user is at `MAX_CONCURRENT_RUNS_PER_USER` |
| 503 | `not_configured` / `persistence_unavailable` / `unavailable` | Key missing or weak, chat history unavailable, workspaces or a dependency unavailable |

A body that fails schema validation (a wrong type, a missing `message`, a body that is not JSON) is answered
before any of the above with FastAPI's standard `422`, whose `detail` is a **list** of
`{"loc", "msg", "type"}` objects rather than a `{code, message}` object. Clients should branch on
the type of `detail`.

## Approvals

Ordinary tool approvals are given by the service: the request that names the task is the
approval, the same way a user's approval of an `atlas_launch` call covers the sub-conversation's
own tool calls. Tools an administrator has made approval-mandatory (`FORCE_TOOL_APPROVAL_GLOBALLY`
or per-tool configuration) are **never** auto-approved:

- By default a launch or follow-up whose tools include one is refused with `409
  approval_required`, naming the tools, before anything runs.
- With `"allow_approval_pause": true` the run is admitted, every tool call prompts, and the run
  pauses as `waiting_for_input` until the user opens the conversation in ATLAS and answers. The
  handle's `tools_needing_approval` lists the mandatory ones. Paused runs count toward the user's
  concurrency cap, and `TOOL_APPROVAL_TIMEOUT_SECONDS` applies as usual.

## Idempotency

Send an `idempotency_key` (any caller-chosen string up to 200 characters) to make retries safe.
A repeat with the same key and the same body, for the same user, returns the original handle
with HTTP 200 and `idempotent_replay: true` rather than launching again; the same key with a
different body is a 409. Two simultaneous requests with one key launch once. A request that was
refused does not consume its key. Keys are remembered for 24 hours, in the memory of the replica
that accepted them. A replay of a follow-up still works after the run it addressed has been
cleaned up. When the replayed run itself is no longer tracked, the handle's `status` is
`expired` (a value only replays carry); its conversation is still in the user's history.

## Limits, replicas and restarts

- `MAX_CONCURRENT_RUNS_PER_USER` and `MAX_RUN_WALL_CLOCK_SECONDS` apply to API runs as to any
  other; the `atlas_launch` depth and fan-out caps do not apply to the API run itself (it is a
  root, depth 0) but do apply to anything it launches. The global HTTP rate limiter applies to
  these endpoints. Messages are limited to 20,000 characters.
- **Run state is in memory.** Status, follow-up, cancel and idempotency are served by the
  replica that accepted the launch, and only while the registry retains the run (30 minutes
  after it ends). Behind several replicas, route `/api/agent-runs` to one replica or use sticky
  routing; a status request that reaches another replica gets `404 run_not_found`. A restart
  loses run state: a run that was executing is stopped, and its ids return 404. Saved transcripts
  are unaffected -- every completed turn is in the user's conversation history, where the user
  can read and continue it.
- The API can see and act only on runs it started. A run the user started in the browser, or one
  started by `atlas_launch`, answers `404 run_not_found` here even by its correct id.

## Audit

Every launch, follow-up and cancel logs one INFO line from
`atlas.application.chat.runs.external_runs` with the actor (`service`), target user, run and
conversation ids, workspace id, model and classification -- never the message, the result or
the key. Failed authentication logs a warning without the presented value. The run's launch
spec records `source: agent_runs_api` for the life of the run.
