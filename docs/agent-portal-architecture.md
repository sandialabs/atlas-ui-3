# ATLAS Agent Portal -- Architecture and Findings

Date: 2026-03-29

## Quick Summary

ATLAS has been extended from a chat UI into an **agent orchestration portal** with
enterprise-grade access control. Three new infrastructure components run alongside
ATLAS on k3s:

- **Keycloak** -- OIDC identity/token management (replaces header-based auth)
- **Cerbos** -- Policy-as-code authorization engine (fine-grained RBAC/ABAC)
- **Prefect** -- Agent flow orchestration with K8s job execution (already existed, now wired in)

Agent state persists in PostgreSQL. The whole stack runs on a single k3s node.

---

## Architecture Diagram

```
                         ATLAS Agent Portal -- k3s Cluster
    ============================================================================

    [Browser / API Client]
           |
           | OIDC JWT (Bearer token from Keycloak)
           v
    +-------------------------------+
    |        Traefik Ingress        |  Port 8080
    |  - strips X-User-Email        |
    |  - /auth -> Keycloak          |
    |  - /prefect -> Prefect        |
    |  - /* -> ATLAS UI             |
    +------+--------+-------+------+
           |        |       |
           v        |       v
    +-----------+   |  +-----------+
    |  Keycloak |   |  |  Prefect  |
    |  (IAM)    |   |  |  Server   |
    |           |   |  |           |
    | Realm:    |   |  | Flows,    |
    |  atlas    |   |  | Deploys,  |
    |           |   |  | Runs      |
    | Clients:  |   |  |           |
    |  atlas-ui |   |  +-----+-----+
    |  agent-svc|   |        |
    |           |   |        | triggers
    | Users:    |   |        v
    |  admin    |   |  +-----------+
    |  operator |   |  |  Prefect  |
    |  user1    |   |  |  Worker   |
    |  viewer   |   |  |           |
    |           |   |  | Type: k8s |
    +-----+-----+   |  | Creates   |
          |         |  | K8s Jobs  |
     OIDC |         |  +-----+-----+
     tokens|         |        |
          |         |        | launches
          v         v        v
    +----------------------------------+     +----------+
    |          ATLAS UI Server         |     | K8s Jobs |
    |          (FastAPI + React)       |     | (Agent   |
    |                                  |     |  Sandbox)|
    |  Auth Middleware                 |     |          |
    |  +----------------------------+  |     | - Runs   |
    |  | 1. Keycloak JWT validation |  |     |   agent  |
    |  | 2. Header-based (fallback) |  |     |   loop   |
    |  | 3. Debug test user         |  |     | - MCP    |
    |  +----------------------------+  |     |   tools  |
    |                                  |     | - Scoped |
    |  Agent Routes (/api/agents/*)    |     |   token  |
    |  +----------------------------+  |     +----------+
    |  | Templates, Launch, Stop,   |  |
    |  | Monitor, Delete, Logs      |  |
    |  +---+------------------------+  |
    |      |                           |
    |      | authz check               |
    |      v                           |
    |  +----------+  +--------------+  |
    |  |  Cerbos  |  | Agent Store  |  |
    |  |  Client  |  | (asyncpg)   |  |
    |  +----+-----+  +------+------+  |
    |       |                |         |
    +-------+----------------+---------+
            |                |
            v                v
    +-----------+    +---------------+
    |  Cerbos   |    |  PostgreSQL   |
    |  PDP      |    |  (Prefect DB) |
    |           |    |               |
    | Policies: |    | Tables:       |
    |  agent    |    |  atlas_agents |
    |  mcp_tool |    |  (+ prefect   |
    |  hpc_job  |    |    tables)    |
    |  data_src |    |               |
    +-----------+    +---------------+
```

---

## Component Details

### 1. Keycloak (Identity and Token Management)

**What it does:** Issues and validates OIDC/OAuth2 tokens. Replaces the
simple header-based auth (`X-User-Email` from reverse proxy) with proper
JWT-based authentication.

**How it works in ATLAS:**
1. User authenticates with Keycloak (username/password or SSO)
2. Keycloak issues a JWT access token containing:
   - `email` -- user identity
   - `realm_roles` -- e.g. `["atlas-admin", "atlas-user", "hpc-user"]`
   - `groups` -- e.g. `["admin", "operators"]`
3. Browser sends `Authorization: Bearer <token>` on every request
4. ATLAS middleware (`middleware.py`) validates the JWT using Keycloak's JWKS endpoint
5. Extracts user email and Keycloak claims, stores on `request.state`
6. Falls back to header-based auth if no Bearer token is present

**ATLAS realm configuration:**

| User | Password | Roles | Groups |
|------|----------|-------|--------|
| admin | admin | atlas-admin, atlas-operator, atlas-user, hpc-user, classified-access | admin |
| operator | operator | atlas-operator, atlas-user, hpc-user | operators |
| user1 | user1 | atlas-user | users, mcp_basic |
| viewer | viewer | atlas-viewer | users |

**OIDC clients:**
- `atlas-ui` -- public client for browser auth (standard flow + direct grant)
- `atlas-agent-service` -- confidential client for backend token exchange
- `atlas-prefect` -- service account for Prefect integration

**Key file:** `atlas/core/keycloak_client.py`

**Token exchange flow for agents:**
```
User token (atlas-ui client)
    --> exchange_token_for_agent()
        --> Keycloak token exchange endpoint
            --> Scoped agent token (atlas-agent-service client)
                --> Injected into K8s Job env
```

---

### 2. Cerbos (Policy-Based Access Control)

**What it does:** Makes authorization decisions based on YAML policies.
Every agent action (launch, stop, delete, monitor) and every MCP tool
invocation goes through Cerbos before execution.

**How it works in ATLAS:**
1. Agent route handler calls `cerbos.check_action()`
2. Sends: principal (user email + roles + attributes) + resource (agent/tool + attributes) + action
3. Cerbos evaluates policies and returns ALLOW or DENY
4. Handler either proceeds or returns 403

**Four policy types:**

| Policy | Resource | Controls |
|--------|----------|----------|
| `agent` | Agent instances | Who can launch/stop/delete/monitor agents |
| `mcp_tool` | MCP server tools | Who can invoke which tools at what compliance level |
| `hpc_job` | HPC compute jobs | Queue access, job ownership, sandbox constraints |
| `data_source` | RAG data stores | Classification-level gating (unclassified, CUI) |

**Role hierarchy (from Keycloak roles):**

```
admin       -- full access to everything
operator    -- launch/stop/monitor agents, invoke/approve tools
user        -- launch approved templates, invoke authorized tools
viewer      -- read-only status
agent       -- scoped tool access within sandbox constraints
```

**Key design decisions:**
- Fail-open by default (for dev); set `CERBOS_FAIL_CLOSED=true` for production
- Stateless -- no database needed, policies are YAML in Git
- Sub-5ms decision latency from within the cluster
- Hot-reloadable -- update the ConfigMap and Cerbos picks up changes

**Key file:** `atlas/core/cerbos_client.py`, `atlas/core/cerbos_authz.py`

---

### 3. Prefect (Agent Orchestration)

**What it does:** Manages the lifecycle of agent execution. When a user
clicks "Launch Agent", ATLAS creates a Prefect flow run that is picked up
by the Kubernetes worker and executed as a K8s Job.

**How it works:**

```
User clicks "Launch"
    --> POST /api/agents/launch
        --> Create agent record in PostgreSQL
        --> prefect_executor.launch_agent_flow()
            --> Ensure flow exists (atlas-agent-{template})
            --> Ensure deployment exists (targets kubernetes-pool)
            --> Create flow run with agent parameters
                --> Prefect Worker picks it up
                    --> Creates K8s Job in atlas namespace
                        --> Agent runs in sandboxed container
```

**Work pool:** `kubernetes-pool` (K8s worker creates Jobs in `atlas` namespace)

**Job configuration:**
- Image: `localhost/atlas-ui:latest` (same ATLAS image)
- Service account: `prefect-worker` (limited RBAC)
- TTL: 1 hour after completion (auto-cleanup)
- Resources: 256Mi-1Gi memory, 100m-1000m CPU
- Tags: `atlas-agent`, `template:{id}`, `owner:{email}`

**Key file:** `atlas/core/prefect_agent_executor.py`

---

### 4. Agent Store (PostgreSQL Persistence)

**What it does:** Persists agent state (status, owner, config, Prefect
flow run IDs) across restarts. Uses the existing Prefect PostgreSQL instance.

**Schema:**
```sql
atlas_agents (
    id              VARCHAR(12) PRIMARY KEY,
    template_id     VARCHAR(64),
    name            VARCHAR(256),
    owner           VARCHAR(256),
    status          VARCHAR(32),     -- running, scheduled, stopped, error
    mcp_servers     JSONB,
    max_steps       INTEGER,
    loop_strategy   VARCHAR(32),
    sandbox_policy  VARCHAR(32),
    prefect_data    JSONB,           -- flow_run_id, deployment_id, state
    has_token       BOOLEAN,
    created_at      TIMESTAMPTZ,
    stopped_at      TIMESTAMPTZ,
    stopped_by      VARCHAR(256),
    ...
)
```

**Fallback:** If PostgreSQL is unreachable, falls back to in-memory dict
(agents lost on restart, but the system keeps working).

**Key file:** `atlas/core/agent_store.py`

---

## Request Flow (End to End)

```
1. User visits /admin/agents
2. Browser loads AgentManagement.jsx
3. React fetches GET /api/agents/templates
      |
      v
4. Middleware: validates Bearer JWT via Keycloak
      - Extracts email, roles, groups
      - Stores on request.state
      |
      v
5. Route handler: list_agent_templates()
      - Gets user roles (from Keycloak claims or group RBAC)
      - For each template, calls Cerbos: can this user launch it?
      - Returns templates with can_launch flag
      |
      v
6. User clicks "Launch Agent" -> selects "Code Review" template
      |
      v
7. POST /api/agents/launch {template_id: "code-review"}
      |
      v
8. Route handler: launch_agent()
      a. Cerbos check: can user launch this template? --> ALLOW
      b. MCP check: is user authorized for ["filesystem", "code-executor"]? --> YES
      c. Create agent record (uuid, owner, config)
      d. Prefect: create flow run targeting kubernetes-pool
      e. Keycloak: exchange user token for scoped agent token
      f. Store: save agent to PostgreSQL
      g. Return agent record to frontend
      |
      v
9. Prefect Worker: picks up flow run from kubernetes-pool
      - Creates K8s Job in atlas namespace
      - Job runs agent loop with MCP tools
      - Agent token injected as env var
      |
      v
10. Frontend: polls GET /api/agents/ every 10 seconds
      - Shows agent status, Prefect flow state, step count
      - User can stop (cancels K8s Job + Prefect run) or delete
```

---

## What's Running on k3s Right Now

```
$ kubectl get pods -n atlas

NAME                                READY   STATUS
atlas-auth-*                        1/1     Running    (existing auth service)
atlas-ui-*                          1/1     Running    (ATLAS main app)
cerbos-*                            1/1     Running    (NEW: policy engine)
keycloak-*                          1/1     Running    (NEW: IAM)
keycloak-postgres-*                 1/1     Running    (NEW: Keycloak DB)
minio-*                             1/1     Running    (existing object store)
prefect-postgres-*                  1/1     Running    (existing)
prefect-server-*                    1/1     Running    (existing)
prefect-worker-*                    1/1     Running    (existing, now wired to agents)
```

---

## Files Created/Modified

### New files (backend)

| File | Purpose |
|------|---------|
| `atlas/core/cerbos_client.py` | Async Cerbos PDP client (check actions, batch checks) |
| `atlas/core/cerbos_authz.py` | Cerbos-enhanced MCP tool authorization wrapper |
| `atlas/core/keycloak_client.py` | OIDC validation, role mapping, token exchange |
| `atlas/core/prefect_agent_executor.py` | Prefect flow/deployment/run management, K8s job config |
| `atlas/core/agent_store.py` | PostgreSQL agent persistence (asyncpg, auto-migration) |
| `atlas/routes/agent_routes.py` | 11 API endpoints for agent lifecycle |

### New files (frontend)

| File | Purpose |
|------|---------|
| `frontend/src/components/AgentManagement.jsx` | Full agent portal page at `/admin/agents` |
| `frontend/src/components/admin/AgentPortalCard.jsx` | Admin dashboard card with status |

### New files (infrastructure)

| File | Purpose |
|------|---------|
| `deploy/k3s/40-cerbos.yaml` | Cerbos deployment, service, config, policies |
| `deploy/k3s/50-keycloak.yaml` | Keycloak + Postgres deployment |
| `deploy/k3s/51-keycloak-realm.yaml` | ATLAS realm config (users, roles, clients) |
| `deploy/k3s/52-keycloak-ingress.yaml` | Traefik route for Keycloak |
| `deploy/k3s/cerbos-policies/*.yaml` | 4 resource policy files |

### Modified files

| File | Change |
|------|--------|
| `atlas/main.py` | Added `agent_router` |
| `atlas/core/middleware.py` | Added Keycloak JWT validation as primary auth |
| `frontend/src/App.jsx` | Added `/admin/agents` route |
| `frontend/src/components/AdminDashboard.jsx` | Added AgentPortalCard |
| `deploy/k3s/01-secrets.yaml` | Added env vars for Keycloak, Cerbos, Prefect, DB |

---

## Key Findings

### What works well

1. **Cerbos is an excellent fit for ATLAS.** Its policy-as-code model maps
   directly to MCP tool authorization. Policies are YAML in Git -- testable,
   reviewable, and deployable via ConfigMap updates. The existing group-based
   RBAC translates cleanly to Cerbos roles.

2. **Keycloak solves the token lifecycle problem.** The current header-based auth
   (`X-User-Email` injected by reverse proxy) has no token rotation, no scoped
   credentials for agents, and no standard OIDC flow. Keycloak provides all of
   this while staying fully on-prem.

3. **Prefect + K8s worker = real agent isolation.** Each agent runs in its own
   K8s Job with resource limits, a scoped service account, and auto-cleanup.
   This is a production-grade sandbox without needing NemoClaw.

4. **Graceful degradation at every layer.** Keycloak down? Falls back to
   header auth. Cerbos down? Falls back to group RBAC. Prefect down? Agents
   still tracked in-memory. PostgreSQL down? In-memory fallback. No single
   component failure takes down the portal.

### Risks and gaps

1. **Agent code execution is stubbed.** The K8s Jobs are created but the actual
   agent loop code (calling MCP tools, running the think-act/react/agentic
   strategy) isn't wired into the Job entrypoint yet. Next step: create a
   `prefect_agent_flow.py` that the Job runs.

2. **Keycloak passwords are hardcoded in the realm import.** Fine for
   experimentation but production needs LDAP/SAML federation with Sandia's
   identity infrastructure.

3. **No network egress policy.** K8s Jobs can currently reach any service in
   the cluster. Production needs NetworkPolicy resources to restrict agent
   containers to only their approved MCP servers and the Prefect API.

4. **asyncpg needs to be added to pyproject.toml** for production builds.
   Currently installed manually via `uv pip install asyncpg`.

### Recommended next steps

| Priority | Task | Effort |
|----------|------|--------|
| High | Create `prefect_agent_flow.py` that runs the agent loop inside K8s Jobs | 1-2 days |
| High | Add K8s NetworkPolicy to restrict agent egress | Hours |
| Medium | Add asyncpg to pyproject.toml dependencies | Minutes |
| Medium | Federate Keycloak with LDAP/SAML for real user management | 1 day |
| Medium | Add Cerbos decision logging to audit pipeline | Hours |
| Low | Add agent marketplace (template registry with versioning) | Days |
| Low | Build real-time WebSocket updates for agent status | Hours |
