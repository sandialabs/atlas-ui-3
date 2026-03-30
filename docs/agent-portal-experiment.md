# ATLAS Agent Portal -- Experiment Results

Date: 2026-03-28

## What was built

A proof-of-concept that extends ATLAS into an agent orchestration portal with
fine-grained, policy-based access control. The prototype is running on the local
k3s cluster alongside the existing ATLAS deployment.

### Components deployed

| Component | Location | Status |
|-----------|----------|--------|
| **Cerbos PDP** (v0.42.0) | k3s pod `cerbos` in `atlas` namespace | Running, healthy |
| **Keycloak** (v26.1.5) | k3s pod `keycloak` + `keycloak-postgres` | Running, ATLAS realm imported |
| **Prefect** (existing) | k3s pod `prefect-server` + `prefect-worker` | Running, agent flows wired |
| Cerbos policies | 4 resource policies (agent, mcp_tool, hpc_job, data_source) | Loaded, tested |
| Agent management API | `atlas/routes/agent_routes.py` | 11 endpoints, Cerbos + Prefect + Keycloak |
| Cerbos client | `atlas/core/cerbos_client.py` | Async, fail-open/fail-closed configurable |
| Keycloak client | `atlas/core/keycloak_client.py` | OIDC validation, role mapping, token exchange |
| Prefect executor | `atlas/core/prefect_agent_executor.py` | Flow creation, run management, cancellation |
| Agent store | `atlas/core/agent_store.py` | PostgreSQL persistence (asyncpg) |
| Cerbos authz layer | `atlas/core/cerbos_authz.py` | Wraps existing group RBAC |
| Agent Portal UI | `frontend/src/components/AgentManagement.jsx` | Full page at `/admin/agents` |
| Admin dashboard card | `frontend/src/components/admin/AgentPortalCard.jsx` | Integrated into admin grid |
| k3s manifests | `deploy/k3s/40-cerbos.yaml`, `50-keycloak.yaml`, `51-*.yaml`, `52-*.yaml` | All deployed |

### What works

1. **Cerbos is running in k3s** and responding to policy checks. Tested admin
   (all actions allowed) and regular user (only approved templates, no
   stop/delete on others' agents). Sub-5ms decision latency from within the
   cluster.

2. **Keycloak is running in k3s** with the ATLAS realm fully configured:
   - 4 sample users (admin, operator, user1, viewer) with different roles
   - OIDC token issuance with realm_roles and groups in JWT claims
   - Role mapping: `atlas-admin` -> Cerbos `admin`, `atlas-user` -> `user`, etc.
   - Service account client (`atlas-agent-service`) for backend-to-backend calls
   - Token exchange support for issuing scoped agent credentials
   - Tested: admin user gets `["atlas-admin","classified-access","atlas-user","atlas-operator","hpc-user"]`
   - Tested: user1 gets `["atlas-user"]` with groups `["mcp_basic","users"]`

3. **Prefect integration** for agent orchestration:
   - Agent launches create Prefect flow runs with template parameters
   - Flow runs tagged with `atlas-agent`, `template:{id}`, `owner:{email}`
   - Agent stops cancel the corresponding Prefect flow run
   - Status endpoint shows flow run state, task counts, timing
   - Uses existing Prefect server + worker already running in k3s

4. **Agent persistence** via PostgreSQL (asyncpg):
   - `atlas_agents` table in the Prefect Postgres instance
   - Automatic table creation on first use
   - Falls back to in-memory dict when DB unavailable
   - Indexed by owner, status, and template for fast lookups

5. **Policy-as-code** for four resource types:
   - `agent` -- who can launch/stop/delete/monitor agents
   - `mcp_tool` -- per-tool invocation control with compliance level + sandbox constraints
   - `hpc_job` -- queue-level access, owner-based management
   - `data_source` -- classification-level gating for RAG stores

6. **Agent lifecycle API** (11 endpoints):
   - `GET /api/agents/templates` -- list available templates with per-user `can_launch` flag
   - `POST /api/agents/launch` -- launch from template, creates Prefect flow run
   - `GET /api/agents/` -- list agents (admins see all, users see own)
   - `GET /api/agents/{id}` -- detail view with user's available actions
   - `POST /api/agents/{id}/stop` -- stop and cancel Prefect flow run
   - `DELETE /api/agents/{id}` -- delete with authorization check
   - `GET /api/agents/{id}/logs` -- audit log view
   - `GET /api/agents/cerbos/status` -- Cerbos health check (admin)
   - `GET /api/agents/prefect/status` -- Prefect info + recent runs (admin)
   - `GET /api/agents/keycloak/status` -- Keycloak health (admin)
   - `GET /api/agents/infrastructure/status` -- combined status of all components (admin)

7. **Frontend** with infrastructure status dashboard (Cerbos/Prefect/Keycloak),
   template selection, launch modal, live agent monitoring with Prefect flow
   run details, and security architecture overview panel.

8. **Graceful degradation**: each component fails independently. Cerbos
   falls back to group RBAC. Prefect falls back to in-memory agent tracking.
   Keycloak falls back to header-based auth. Configurable via `CERBOS_FAIL_CLOSED`.

### What is prototype / not yet production

1. **Agent registry is in-memory** -- agents disappear on restart. Production
   needs PostgreSQL or the existing Prefect database.

2. **Agents don't actually execute** -- the launch/stop lifecycle is tracked but
   no real sandbox or persistent process is created. This is where NemoClaw or
   a custom container-based executor would plug in.

3. **Role mapping is simplified** -- `_get_user_roles()` maps ATLAS groups to
   Cerbos roles. Production needs proper identity provider integration (OIDC
   claims -> Cerbos roles).

4. **User attributes are hardcoded** -- compliance levels, allowed queues, and
   clearance levels are static. Production needs an attribute service or IdP
   integration.

5. **No real sandbox enforcement** -- the sandbox_policy field is metadata only.
   NemoClaw's Landlock/seccomp/netns would provide actual enforcement.

---

## Architecture assessment

### Is this a good idea?

**Yes, with caveats.**

**Strengths:**
- ATLAS already has the MCP infrastructure, agent loop strategies, and RBAC
  foundation. Adding Cerbos on top is non-disruptive and additive.
- Cerbos is lightweight (64MB RAM, <5ms decisions), stateless, and deploys as a
  sidecar or service. No database needed -- policies are YAML files in Git.
- The existing tool-approval system and compliance levels map naturally to Cerbos
  attributes. This is evolution, not revolution.
- Air-gapped deployment works: Cerbos runs locally, no external dependencies.
- Policy changes are hot-reloadable (update ConfigMap, Cerbos picks it up).

**Risks to manage:**
- **Complexity budget**: Adding Cerbos + agent sandboxing + NemoClaw is three
  new subsystems. Recommend phasing: Cerbos first (done), then agent lifecycle
  with Prefect (already in k3s), then sandbox enforcement.
- **NemoClaw maturity**: It's in early preview/alpha (as of March 2026). For
  production gov/HPC use, Sandia may want to fork or replicate the sandbox
  primitives (Landlock/seccomp are Linux kernel features, not NemoClaw-specific).
- **Token management**: Infisical or OpenBao should be added for dynamic secret
  injection into agent sandboxes. The current token_storage.py is per-user
  encrypted JSON -- adequate for user tokens but not for agent credential lifecycle.

### Recommended next steps

**Short-term (this branch):**
1. Persist agent registry to the Prefect PostgreSQL (already running in k3s)
2. Wire agent launch to actually start a Prefect flow with MCP tool access
3. Add the `CERBOS_URL` env var to the atlas-ui deployment and redeploy

**Medium-term:**
1. Integrate NemoClaw's OpenShell sandbox (or replicate Landlock/seccomp policies)
   as the agent execution environment
2. Add Infisical for dynamic secret injection (LLM API keys, MCP tokens)
3. Build a proper role/attribute service backed by Sandia's identity infrastructure
4. Add WebSocket events for real-time agent status updates in the UI

**Long-term:**
1. Agent marketplace with pre-approved templates vetted by security
2. Federated agent execution across HPC clusters (SLURM integration via MCP)
3. Audit dashboard with Cerbos decision logs for compliance reporting
4. Multi-tenant isolation for cross-lab agent sharing

---

## Files created/modified

### New files
```
atlas/core/cerbos_client.py           -- Async Cerbos PDP client
atlas/core/cerbos_authz.py            -- Cerbos-enhanced authz layer
atlas/core/keycloak_client.py         -- Keycloak OIDC client, role mapper, token exchange
atlas/core/prefect_agent_executor.py  -- Prefect flow management for agents
atlas/core/agent_store.py             -- PostgreSQL-backed agent persistence
atlas/routes/agent_routes.py          -- Agent management API (11 endpoints)
frontend/src/components/AgentManagement.jsx       -- Agent portal page
frontend/src/components/admin/AgentPortalCard.jsx -- Admin dashboard card
deploy/k3s/40-cerbos.yaml            -- Cerbos k3s deployment
deploy/k3s/50-keycloak.yaml          -- Keycloak + Postgres k3s deployment
deploy/k3s/51-keycloak-realm.yaml    -- ATLAS realm ConfigMap (users, roles, clients)
deploy/k3s/52-keycloak-ingress.yaml  -- Traefik IngressRoute for Keycloak
deploy/k3s/cerbos-policies/          -- Policy YAML files (4 resources)
docs/agent-portal-experiment.md       -- This document
```

### Modified files
```
atlas/main.py                        -- Added agent_router import and registration
frontend/src/App.jsx                 -- Added /admin/agents route
frontend/src/components/AdminDashboard.jsx -- Added AgentPortalCard
deploy/k3s/01-secrets.yaml           -- Added CERBOS_URL, FEATURE_CERBOS_ENABLED
```

---

## How to test

```bash
export KUBECONFIG=~/.kube/config

# Verify all infrastructure pods
kubectl get pods -n atlas -l 'app in (cerbos,keycloak,prefect-server)'

# Test Cerbos policy check
kubectl run curl-test --rm -it --restart=Never --image=curlimages/curl -n atlas -- \
  curl -s -X POST http://cerbos:3592/api/check/resources \
  -H "Content-Type: application/json" \
  -d '{"requestId":"test","principal":{"id":"admin@test.com","roles":["admin"],"attr":{}},"resources":[{"resource":{"kind":"agent","id":"test","attr":{"owner":"admin@test.com","template_approved":true}},"actions":["launch","stop","delete"]}]}'

# Test Keycloak token issuance (admin user)
kubectl run kc-test --rm -it --restart=Never --image=curlimages/curl -n atlas -- \
  curl -s -X POST http://keycloak:8080/auth/realms/atlas/protocol/openid-connect/token \
  -d "grant_type=password&client_id=atlas-ui&username=admin&password=admin"

# Test Keycloak token issuance (regular user -- different roles)
kubectl run kc-test2 --rm -it --restart=Never --image=curlimages/curl -n atlas -- \
  curl -s -X POST http://keycloak:8080/auth/realms/atlas/protocol/openid-connect/token \
  -d "grant_type=password&client_id=atlas-ui&username=user1&password=user1"

# Test Prefect API
kubectl run pf-test --rm -it --restart=Never --image=curlimages/curl -n atlas -- \
  curl -s http://prefect-server:4200/api/health

# Build and deploy ATLAS with the new code
cd frontend && npm run build && cd ..
# Then rebuild the container image and redeploy to k3s

# Or run locally with port-forwarding for dev testing
kubectl port-forward -n atlas svc/cerbos 3592:3592 &
kubectl port-forward -n atlas svc/keycloak 8180:8080 &
kubectl port-forward -n atlas svc/prefect-server 4200:4200 &
source .venv/bin/activate
CERBOS_URL=http://localhost:3592 \
  KEYCLOAK_URL=http://localhost:8180/auth \
  PREFECT_API_URL=http://localhost:4200/api \
  FEATURE_KEYCLOAK_ENABLED=true \
  python -m atlas.main
# Then visit http://localhost:8000/admin/agents
```

### Keycloak test users

| Username | Password | Roles | Groups |
|----------|----------|-------|--------|
| admin | admin | atlas-admin, atlas-operator, atlas-user, hpc-user, classified-access | admin |
| operator | operator | atlas-operator, atlas-user, hpc-user | operators |
| user1 | user1 | atlas-user | users, mcp_basic |
| viewer | viewer | atlas-viewer | users |

### Keycloak admin console
Port-forward and visit `http://localhost:8180/auth/admin/` (login: admin/admin).
