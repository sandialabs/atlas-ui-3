# Multi-Replica Staging on Kubernetes/OpenShift

Last updated: 2026-11-04

This guide deploys Atlas to a staging cluster as **three independent API
replicas** sharing one Redis, one PostgreSQL and one S3 store, then checks
that the replicas start, authenticate, serve traffic and resume a user's
session on a different replica. It also covers scaling, rolling updates and
failure drills. It belongs to issue
[#1129](https://github.com/sandialabs/atlas-ui-3/issues/1129).

This is staging and deployment validation, not a per-PR gate. Changes to run
coordination are tested per PR by the multi-process harness
(`bash test/multireplica_tests.sh`, see AGENTS.md "Horizontal Scaling").

## What gets deployed

Everything lives under `deploy/kubernetes/`:

| Path | Contents |
|------|----------|
| `base/` | The `atlas-api` Deployment, its Service (`sessionAffinity: None`) and PodDisruptionBudget, and the non-secret settings (`api.env`). It can't be deployed on its own. |
| `overlays/staging/` | Three replicas, plus staging-only Redis (`atlas-redis`), PostgreSQL (`atlas-postgres`, StatefulSet with a 5Gi PVC) and MinIO (`atlas-minio`, 5Gi PVC), and the `atlas-secrets` Secret generated from `secrets.env` |
| `overlays/staging-openshift/` | The same overlay without fixed UIDs on the bundled services, for the restricted-v2 SCC |
| `staging.sh` | The only supported entry point: secrets, apply, scale, restart, validate, failover drill, teardown |
| `validate_replicas.py` | Checks each replica separately (see [Validation](#validation)) |
| `podman_rehearsal.py` | Runs the rendered overlay with podman when no cluster is available |

The bundled Redis, PostgreSQL and MinIO run as one unreplicated instance each,
sized for validation. Production uses managed or replicated services. To
use them, point `secrets.env` at those services and remove the bundled ones
in an overlay of your own.

### The API pods

- **No affinity.** The pods set no affinity, anti-affinity, topology spread
  or node selector, and the Service has no session affinity. Any replica must
  serve any user on any node. If you add spreading for availability in
  production, it must not become a routing requirement.
- **Migrations run once, from the first pod.** Every pod's `migrate` init
  container runs `python -m atlas.modules.chat_history.schema_migration`. It
  brings the chat-history database to the image's Alembic head while holding
  a PostgreSQL advisory lock, so when several pods start together one applies
  the pending migrations and the rest wait, then find nothing to do. The
  database URL comes from the Secret. Two other cases:
  - A database at a revision the image doesn't know, such as a rollback
    after a newer release migrated, is left unchanged with a warning.
    Migrations are additive and are never downgraded automatically.
  - A database whose tables were created by an Atlas startup has no
    `alembic_version` table. The init container refuses it and exits with
    status 2, so the pod stays in `Init` and a rollout stalls with the old
    pods still serving. Check that the schema matches the current release,
    then record it once with `alembic -c /app/alembic.ini stamp head`, for
    example from `kubectl exec` into a running API pod.

  The init container is needed because the app's own `create_all` at startup
  can't run concurrently: when replicas start together against an empty
  database, all but one fail with `DuplicateTable` and come up with chat
  history disabled. The image ships `alembic.ini`, `alembic/` and the
  `alembic` CLI (the `migrations` extra) for this.
- **Probes.**
  - *Startup* reads `/api/health` and passes only when `status` is
    `healthy`, chat history is `ok` and the Redis session store is `ok`. A
    replica whose chat history failed to start is restarted instead of
    serving without history. It allows 3 minutes.
  - *Readiness* (every 5s) and *liveness* (every 20s) use `/api/heartbeat`.
    Once started, a replica stays in rotation through a Redis or PostgreSQL
    blip and reports `degraded` on `/api/health` for alerting. Pulling every
    replica would turn a shared outage into a full one.
- **Resources.** The API container requests 500m CPU and 1Gi of memory, with
  limits of 2 CPUs and 2Gi. The bundled MCP servers run as child processes
  inside this limit. Measure under your own load (`kubectl top pods`) before
  you change these numbers. Every container in the overlay has requests and
  limits.
- **Rollouts.** `RollingUpdate` with `maxSurge: 1` and `maxUnavailable: 0`,
  and a PDB with `maxUnavailable: 1`. Before uvicorn gets SIGTERM, a 10s
  `preStop` sleep lets endpoint removal reach the Service and ingress. Uvicorn
  then closes WebSockets with code 1012, and browsers reconnect and resume on
  another replica. `terminationGracePeriodSeconds` is 60.
- **Security.** The pods run non-root under the `RuntimeDefault` seccomp
  profile, with no privilege escalation, all capabilities dropped and no
  service-account token. The API pins no UID, so OpenShift can assign one;
  `HOME=/tmp` keeps libraries from needing a writable home directory. Each
  pod's `/tmp`, `/data`, `/app/logs` and `/app/runtime` are `emptyDir`
  volumes.
- **Configuration.** Changes to `api.env`, `staging.env` or `secrets.env`
  change the generated ConfigMap or Secret name, which rolls the replicas.

### Connection budgets

Each replica runs one uvicorn worker.

| Pool | Per replica | Replicas (3 + 1 surge) | Server limit |
|------|-------------|------------------------|--------------|
| PostgreSQL: `CHAT_HISTORY_DB_POOL_SIZE` + `CHAT_HISTORY_DB_MAX_OVERFLOW` | 5 + 5 = 10 | 40, plus one migration connection per starting pod | `max_connections=100` |
| Redis: `REDIS_MAX_CONNECTIONS` | 20 | 80 | `maxclients 1000` |

If you scale beyond about 8 replicas, raise `max_connections` or add
PgBouncer (see [Chat History](chat-history.md#postgresql-connection-pool)).
`test/k8s_render_checks.sh` fails when the rendered numbers no longer fit.

## Prerequisites

- A Kubernetes 1.27+ or OpenShift 4.14+ cluster with a default StorageClass,
  and a namespace you may create objects in. On OpenShift, create the
  project first if you can't (`oc new-project atlas-staging`).
- `kubectl` 1.27+ (or `oc`; set `KUBECTL=oc`), `openssl`, `curl`.
- An Atlas image in a registry the cluster can pull from, built from this
  repository's `Dockerfile` (it must contain `/app/alembic.ini`). Refer to it
  by tag or digest; `:latest` is refused.
- Python with `httpx` and `websockets` for validation. The repository's
  `.venv` has both; `staging.sh` uses it when present.

## Deploy

```bash
cd deploy/kubernetes
export ATLAS_KUBE_CONTEXT=my-staging-cluster      # required; the current context is never used
export ATLAS_IMAGE=quay.io/<org>/atlas-ui-3:v0.11.0  # or ...@sha256:<digest>
# export ATLAS_FLAVOR=openshift                   # on OpenShift
# export ATLAS_NAMESPACE=atlas-staging            # default

./staging.sh init-secrets    # writes overlays/staging/secrets.env (mode 600, git-ignored)
./staging.sh render | less   # optional: inspect, offline
./staging.sh apply           # creates the namespace, applies, waits for every rollout
./staging.sh validate
```

`apply` layers a throwaway kustomization over the overlay that pins the
image, the replica count (`ATLAS_REPLICAS`, default 3) and the namespace, so
you edit no tracked files. Running it again with the same variables changes
nothing. Keep the `secrets.env` you deployed with; teardown and validation
read it.

### Models and MCP servers

The image's default `/app/config` is used. To give staging its own LLM or
MCP configuration, mount a ConfigMap over the file in an overlay of your own,
for example:

```yaml
# my-overlay/kustomization.yaml
resources: [../staging]
configMapGenerator:
  - name: atlas-llmconfig
    files: [llmconfig.yml]
patches:
  - target: {kind: Deployment, name: atlas-api}
    patch: |-
      - op: add
        path: /spec/template/spec/volumes/-
        value: {name: llmconfig, configMap: {name: atlas-llmconfig}}
      - op: add
        path: /spec/template/spec/containers/0/volumeMounts/-
        value: {name: llmconfig, mountPath: /app/config/llmconfig.yml, subPath: llmconfig.yml}
```

Put provider API keys in a Secret, never in the ConfigMap.

### Browser access

Atlas trusts a reverse proxy to authenticate users. Every request must carry
`X-User-Email` and `X-Proxy-Secret` (the `PROXY_SECRET` from `secrets.env`).
Without them requests get `401` and WebSockets are refused, so exposing the
Service directly is safe but useless. Put your authenticating proxy (for
example oauth2-proxy or your site's SSO gateway) in front of the
`atlas-api` Service. The proxy must:

- strip any client-supplied `X-User-Email` and set its own, and add
  `X-Proxy-Secret`;
- pass WebSocket upgrades on `/ws`. Atlas pings every 30s
  (`WEBSOCKET_KEEPALIVE_INTERVAL_SECONDS`), so the usual 60s idle timeouts
  are enough;
- **not** pin users to a replica: no cookie affinity on the ingress (for
  ingress-nginx, don't set `nginx.ingress.kubernetes.io/affinity`) and on
  OpenShift Routes set
  `haproxy.router.openshift.io/disable_cookies: "true"`.

Proxy secret checks and header auth are documented in
[Authentication](authentication.md).

## Validation

`./staging.sh validate` port-forwards to **each ready API pod separately**
and runs `validate_replicas.py` against them. Requests go to the replica the
check chooses, never through load balancing, so the cross-replica checks
really cross replicas:

| Check | Proves |
|-------|--------|
| `health[pod]` | Each pod is `healthy`, chat history `ok`, Redis session store `ok` |
| `auth[pod]` | Each pod rejects a missing or wrong proxy secret (HTTP 401 and WebSocket handshake) and serves a properly proxied user |
| `durable-state` | A workspace created on pod 1 is listed on every other pod and deleted through the last one (shared PostgreSQL) |
| `session-reconnect` | A WebSocket session opened on pod 1 (New Chat) is resumed on pod 2, then pod 3, without sticky sessions (shared Redis); another user's resume is refused |

The validator stands in for the proxy, so run it only from a trusted
workstation. Expected output ends with `8 passed, 0 failed` for three
replicas: 3 health, 3 auth, 2 cross-replica.

### Failure, scaling and rollout drills

Run `./staging.sh validate` after each step.

| Drill | Command | Expected |
|-------|---------|----------|
| Replica lost under a live session | `./staging.sh failover-drill` | Opens a session on one pod and holds the socket, then deletes the pod (graceful). The client sees the server close the socket, and the session resumes on another pod. The Deployment replaces the pod. |
| Scale out / in | `./staging.sh scale 5`, then `./staging.sh scale 3` | New pods pass the init container (`already at head`) and the startup probe, and join the Service endpoints. The drill is reproducible because the count is part of the apply. |
| Rolling restart | `./staging.sh restart` | One pod at a time; ready capacity never drops below 3 |
| New image | `ATLAS_IMAGE=...:v0.12.0 ./staging.sh apply` | The first new pod's init container applies any pending migration under the lock; the rest find head |
| Rollback | `ATLAS_IMAGE=...:v0.11.0 ./staging.sh apply` | The init container warns that the schema is newer and leaves it unchanged |
| Redis outage | `kubectl --context $ATLAS_KUBE_CONTEXT -n atlas-staging scale deploy/atlas-redis --replicas=0`, then back to 1 | Replicas stay ready. `/api/health` reports `session_store.status: degraded`, and reconnects start fresh sessions that reload history from PostgreSQL. See [Session Store](session-store.md#outages). After Redis returns, health goes back to `ok`. Sessions are stored again on their next write. |
| PostgreSQL restart | `kubectl ... rollout restart statefulset/atlas-postgres` | Pool pre-ping replaces dead connections. Requests during the restart fail and are logged; no replica restarts |

`./staging.sh status` shows pods, the Service's endpoint slices (one ready
endpoint per replica) and recent events.

### Without a cluster

`python deploy/kubernetes/podman_rehearsal.py --image <image>` renders the
staging overlay and runs it with podman. It starts the same images, commands,
environment and startup-probe command, with three API containers and the
`migrate` init containers started concurrently. It then runs the validator
and a failover drill: SIGTERM to a replica with a live socket, and the
session resumes on another replica. It can't show scheduling, Service load
balancing, ingress, PVC binding or SCC admission.
`test/pr-validation/test_pr1165_k8s_staging.sh` runs it.

`bash test/k8s_render_checks.sh` (run in CI) renders both flavors offline
and checks the properties above: replicas, no affinity or sticky sessions,
probes, limits, the migration init container, shared-state settings,
connection budgets, no committed credentials, and no fixed UIDs on OpenShift.

### Teardown

```bash
./staging.sh teardown           # keeps the PostgreSQL PVC
./staging.sh teardown --purge   # also deletes it
```

## Not shared between replicas (yet)

These are per-replica in this deployment. They are known gaps, not
configuration mistakes:

- **Tracked agent runs**, their approvals, stop/steer routing and the per-user
  run cap are process-local until distributed run coordination
  ([#1128](https://github.com/sandialabs/atlas-ui-3/issues/1128)) lands. A
  user who reconnects to another replica doesn't see a run executing
  elsewhere until it finishes. Validate run operations across replicas once
  #1128 is done.
- **Per-user MCP tokens** (`MCP_TOKEN_STORAGE_DIR=/data/tokens`) are stored on
  each pod's own `emptyDir`. A token entered through one replica is unknown to
  the others and lost when the pod is replaced.
- **Rate limiting** (`RATE_LIMIT_RPM`, default 600 per minute) counts per
  process and per client IP. Behind a proxy every user shares the proxy's
  address, so size the limit for the proxy's traffic per replica.
- **Feedback files** (`RUNTIME_FEEDBACK_DIR`) and **logs** are written to the
  pod's own volumes. Collect logs from stdout.

## Not covered here

The remaining items of #1129 still need a staging cluster and are not
recorded yet: load tests with hundreds to thousands of connected clients and
concurrent agent runs, observability and alerting for Redis or PostgreSQL
outages, and cross-replica run operations after #1128. Record the observed
capacity and limits here when they are measured.

## Related

- [Session Store](session-store.md): Redis sessions, reconnect, outages
- [Chat History](chat-history.md): PostgreSQL, pool settings, Alembic
- [Parallel Conversation Runs](parallel-conversation-runs.md): process-local run limits
- `test/multireplica/README.md`: the per-PR multi-process harness
