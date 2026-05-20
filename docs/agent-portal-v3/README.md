# Agent Portal V3 -- Kubernetes-Job-backed Agents

Last updated: 2026-05-20

A third iteration of the Agent Portal that runs each agent as a one-shot
Kubernetes Job in your cluster, fully feature-flagged and independent of
the v1/v2 host-process portal.

## What's in the box

- **Backend**: `atlas/modules/agent_portal_v3/`
  - `models.py` -- SQLAlchemy tables `agent_portal_v3_runs`, `agent_portal_v3_run_events`
  - `database.py` -- DuckDB engine factory (overridable via `AGENT_PORTAL_V3_DB_URL`)
  - `store.py` -- user-scoped repository
  - `k8s_client.py` -- async wrapper around the `kubernetes` python client
  - `job_template.py` -- Job + NetworkPolicy manifest builders
  - `runner.py` -- orchestrator (launch / cancel / delete / watcher loop)
- **Routes**: `atlas/routes/agent_portal_v3_routes.py` at prefix `/api/agent-portal-v3`
- **Frontend**: `frontend/src/components/AgentPortalV3.jsx` mounted at `/agent-portal-v3`
- **Agent image**: `atlas/agent_runner_v3/` (Dockerfile + minimal Python ReAct loop)

## Feature flag

In `.env`:

```bash
FEATURE_AGENT_PORTAL_V3_ENABLED=true
```

That flag is what flips the `agent_portal_v3` boolean on `/api/config` and
`/api/config/shell`, which gates the route registration server-side and the
Header button / React Router route client-side.

## One-time setup (local k3s)

```bash
# 1. Build the agent image and import it into the k3s containerd
bash atlas/agent_runner_v3/build_and_load.sh

# 2. (re)start atlas
bash agent_start.sh
```

`build_and_load.sh` builds `localhost/atlas-agent-runner:dev` with podman,
saves the tarball, and `sudo k3s ctr images import`s it so the cluster can
launch pods using `imagePullPolicy=IfNotPresent`.

## How a launch flows

1. UI POSTs `/api/agent-portal-v3/runs` with `{prompt, mcp_servers[], llm_model, llm_provider}`.
2. Backend resolves the requested MCP servers, dropping stdio ones (a sealed
   pod can't run host binaries).
3. `AgentRunner.launch_run` creates a `AgentRunRecord`, applies a per-run
   `NetworkPolicy`, then submits a `batch/v1 Job` to the configured namespace
   (default: `atlas`). Labels include `atlas.run-id=<uuid>` so the
   NetworkPolicy's `podSelector` binds to exactly that pod.
4. The watcher coroutine (started in `atlas/main.py` lifespan) polls each
   active Job every 5s and transitions the DB row through `launching ->
   running -> succeeded|failed|cancelled`.
5. The UI polls `GET /api/agent-portal-v3/runs` every 3s for the list,
   plus `/runs/{id}/events` and `/runs/{id}/logs` for the detail pane.

## What runs inside the pod

`atlas/agent_runner_v3/runner.py` is a small Python ReAct loop:

- reads `ATLAS_PROMPT`, `ATLAS_MCP_CONFIG`, `ATLAS_LLM_PROVIDER`,
  `ATLAS_LLM_MODEL` and the provider API key from env
- connects to each MCP server over Streamable HTTP (`initialize`, `tools/list`)
- runs up to `ATLAS_MAX_ITERATIONS` LLM turns (Anthropic or OpenAI), executing
  `tools/call` against the appropriate MCP for each tool_use block
- prints structured JSON to stdout so the backend can show meaningful events

This is intentionally a thin runtime so you can swap it for `cline`,
`opencode`, `claude-code` headless mode, or any other agent CLI by:
1. editing `atlas/agent_runner_v3/Dockerfile` to install the new CLI
2. updating the `ENTRYPOINT` to invoke it with the env-provided prompt/config
3. rebuilding the image and `build_and_load.sh`-ing it into k3s

## Network isolation

`build_network_policy` writes a `NetworkPolicy` per run that:

- denies all egress by default (only `policyTypes: ["Egress"]` is set,
  and we never list `Ingress`)
- allows UDP/TCP 53 to the `kube-system` namespace (DNS resolution)
- allows TCP 80/443 to `0.0.0.0/0` *except* RFC1918 / link-local --
  so the pod can reach the LLM API and configured MCP hosts but not
  other pods or the host's metadata endpoints

For a stricter posture (FQDN allowlist, per-pod proxy), drop in a Cilium
L7 policy or front the runner with an envoy sidecar that only proxies to
the resolved hosts.

## REST surface

```
GET    /api/agent-portal-v3/capabilities          # cluster reachable, ns, image, keys
GET    /api/agent-portal-v3/mcp-servers           # selectable MCP servers
GET    /api/agent-portal-v3/models                # LLM models + provider availability
GET    /api/agent-portal-v3/runs                  # this user's runs
POST   /api/agent-portal-v3/runs                  # launch a new run
GET    /api/agent-portal-v3/runs/{id}             # one run
GET    /api/agent-portal-v3/runs/{id}/logs?tail=N # pod stdout tail
GET    /api/agent-portal-v3/runs/{id}/events      # append-only event log
POST   /api/agent-portal-v3/runs/{id}/cancel      # delete the k8s Job
DELETE /api/agent-portal-v3/runs/{id}             # cancel + drop record
```

## Screenshots

| | |
|---|---|
| Portal overview          | `screenshots/01-portal-overview.png` |
| Run detail / events tab  | `screenshots/02-run-detail-events.png` |
| Run detail / pod logs    | `screenshots/03-pod-logs.png` |
| In-flight launch         | `screenshots/04-launched-running.png` |
| Pod log haiku            | `screenshots/05-pod-logs-haiku.png` |
| `kubectl` cluster state  | `screenshots/cluster-state.txt` |

## Known limits / next steps

- The watcher polls; replace with a `watch.Watch` long-poll for snappier
  status transitions when the run list grows.
- Logs are fetched with `read_namespaced_pod_log` on each tick. A WebSocket
  endpoint that streams `stream_pod_logs` would feel more interactive.
- The LLM API key is injected inline into the Job env. For real deployments,
  switch to `llm_api_key_secret_ref` (already a parameter on
  `build_job_manifest`) and create a per-namespace `Secret`.
- MCP server selection is restricted to http/sse. If you need stdio MCPs,
  package them into the agent image alongside the runner.
- Tests: this iteration does not yet ship pytest coverage; the e2e was
  performed live against the cluster.
