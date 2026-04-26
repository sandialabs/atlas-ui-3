# Agent Portal — Generalization Foundation

Last updated: 2026-04-21
Branch: `worktree-feat+agent-portal-scaffold`
Status: Design + scaffolding proposal. **Gated by `FEATURE_AGENT_PORTAL_ENABLED` (default off).**

This document proposes a generalized foundation for the Agent Portal that `AGENTS.md` already names as product direction: "a separate Agent Portal that lets users launch and control agents from a UI-friendly surface with governed controls suitable for enterprise and government use." The focus of this doc is the generalized substrate — the parts that do not depend on a specific runtime (local process, SSH+tmux, Kubernetes, SLURM) — and a first sandbox posture that ships with filesystem and network restrictions by default, plus an explicit developer escape hatch.

## 1. Product framing

Atlas today is a chat surface with MCP tools and RAG. The Agent Portal extends it with **governed, launchable agent sessions**: a user picks a launch template (scope, tools, budget, sandbox tier), clicks Launch, and the portal materializes a sandboxed workspace where an agent runs to completion. Every decision and every byte is auditable.

Key product properties:

- **Governance at launch time.** Scope, tool allow-list, sandbox tier, and budget are resolved and baked into the launch command before the agent starts. The agent is never trusted to enforce its own limits.
- **Kernel-level isolation by default.** Agents run behind Landlock (filesystem) plus network restriction (namespace or filtered proxy) plus optional syscall filtering. An unsandboxed mode exists but is opt-in and intended for developer debugging.
- **Runtime-agnostic control plane.** The portal speaks to a single `RuntimeAdapter` interface. A `local_process` adapter ships first; `ssh_tmux`, `kubernetes`, and `slurm` adapters can be added without UX churn.
- **Audit as system of record.** A SHA-256 chained JSONL stream captures lifecycle events, stdin/stdout frames, and tool calls. Terminal scrollback is never the source of truth.

Non-goals for v0:

- No multi-user fan-out beyond what current Atlas auth already provides.
- No browser-attached xterm.js session (deferred; audit log is the first consumer).
- No SSH / tmux / cluster adapters (stubs only; we build the shape, not the implementation).
- No agent runtime decisions (which LLM loop, which prompt). The portal launches an agent command; what that command is belongs to the caller.

## 2. Feature-flag gating

Everything in this design is introduced behind a feature flag so it can land on `main` without becoming a default.

- **Flag:** `FEATURE_AGENT_PORTAL_ENABLED`
- **Default:** `false`
- **Effect when false:**
  - `atlas/routes/agent_portal_routes.py` is not mounted on the FastAPI app.
  - `AgentPortalService` returns a disabled-stub that raises a typed domain error if called.
  - No background tasks (reaper, audit flusher) are started.
  - `/api/config/shell` exposes `features.agent_portal = false`; the frontend hides the portal entry point.
- **Effect when true:**
  - Routes are mounted under `/api/agent-portal/*`.
  - The session manager, audit log, and at least one adapter become available.
  - Admin-only endpoints (`/api/agent-portal/admin/*`) are gated additionally by the existing admin group check.

Additional sub-flags (all default-safe):

- `AGENT_PORTAL_DEFAULT_SANDBOX_TIER` — `restrictive` | `standard` | `permissive`, default `standard`.
- `AGENT_PORTAL_ALLOW_PERMISSIVE_TIER` — boolean, default `false`. When false, a launch spec requesting `permissive` is rejected at validation even if the feature flag is on.
- `AGENT_PORTAL_SANDBOX_BACKEND` — `landlock+netns` | `bubblewrap` | `none`, default `landlock+netns`. `none` is the developer escape hatch and requires `AGENT_PORTAL_ALLOW_PERMISSIVE_TIER=true` to select.

## 3. Concepts and data model

```
LaunchSpec ─▶ SessionManager ─▶ RuntimeAdapter.launch ─▶ AdapterHandle
                │
                ▼
            AuditStream (append-only, SHA-256 chain)
```

### 3.1 LaunchSpec (user-supplied, policy-validated)

```python
class LaunchSpec(BaseModel):
    template_id: str                # admin-curated launch template
    scope: str                      # free-text task description, audited
    tool_allowlist: list[str]       # MCP server_tool names subset
    sandbox_tier: SandboxTier       # restrictive | standard | permissive
    budget: Budget                  # token ceiling, wall-clock seconds, tool-call cap
    workspace_hint: str | None      # optional requested workspace path
    agent_command: list[str]        # what to run inside the sandbox
```

### 3.2 Session (portal-owned state)

```python
class Session:
    id: str                 # uuid
    user_email: str
    spec: LaunchSpec
    state: SessionState     # pending|authenticating|launching|running|ending|ended|failed|reaped
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime
    adapter_name: str
    adapter_handle: dict    # opaque to the manager; only the adapter interprets
    audit_path: Path
    termination_reason: str | None
```

### 3.3 SandboxProfile (built from tier + policy)

```python
class SandboxProfile:
    tier: SandboxTier
    fs_read_paths: list[str]    # Landlock read rules
    fs_read_write_paths: list[str]
    fs_exec_paths: list[str]
    network: NetworkPolicy       # denied | loopback_only | allowlist_proxy | unrestricted
    egress_allowlist: list[str]  # used when network=allowlist_proxy
    seccomp_profile: str | None  # path to BPF file; optional
    env_allowlist: list[str]     # env vars passed through; everything else cleared
```

Default profiles (see `atlas/modules/agent_portal/sandbox/profiles.py`):

| Tier | Filesystem | Network | Seccomp | Intended use |
| - | - | - | - | - |
| `restrictive` | `rw /workspace` only; host `/usr`, `/etc`, `/lib*` read-only | denied (loopback only) | deny-list (ptrace, mount, bpf, kexec, unshare-of-user) | Prompt with untrusted content, read-only analysis |
| `standard` | `rw /workspace`, `rw ~/.cache/pip`; host paths read-only | egress via filtering proxy with domain allowlist | standard deny-list | Normal dev work, needs pypi / LLM API |
| `permissive` | host `$HOME` read-write | unrestricted | warn-only | Developer debugging; requires `AGENT_PORTAL_ALLOW_PERMISSIVE_TIER=true` |

### 3.4 RuntimeAdapter (protocol)

```python
class RuntimeAdapter(Protocol):
    name: str
    def launch(self, session: Session, profile: SandboxProfile) -> AdapterHandle: ...
    async def attach(self, handle: AdapterHandle) -> AsyncIterator[bytes]: ...
    async def cancel(self, handle: AdapterHandle, reason: str) -> None: ...
    def status(self, handle: AdapterHandle) -> AdapterStatus: ...
    async def collect_artifacts(self, handle: AdapterHandle) -> list[Artifact]: ...
```

v0 ships a single `local_process` adapter that:

- builds a `bwrap ...` argv from the `SandboxProfile` (pure function; testable)
- spawns the child via `asyncio.create_subprocess_exec`
- captures stdout/stderr into the audit stream via an in-process tee (no external shim binary required for v0)
- does not attempt persistent-across-process sessions (that's what the `ssh_tmux` adapter is for in v1)

### 3.5 AuditStream

Append-only JSONL, one stream per session, at `{app_log_dir}/agent_portal/{session_id}.jsonl`. Each frame:

```json
{"ts": "...", "session": "...", "seq": 1, "prev": "0000...", "stream": "stdout", "data_b64": "..."}
```

`prev` is SHA-256 of the previous frame's canonical JSON. The v0 writer does synchronous `fsync` per frame; a later optimization can batch.

## 4. Sandbox implementation

### 4.1 Why three primitives (Landlock + netns + seccomp)

Landlock restricts what paths the process can touch but is indifferent to network and syscalls. A network namespace prevents the process from seeing any network interface but doesn't limit filesystem or syscall surface. Seccomp-BPF removes dangerous syscalls (e.g. `ptrace`, `mount`, `bpf`, `kexec`, `userns_create`) regardless of what Landlock or namespaces allow. Any one of the three alone leaves a large attack surface; layered, they cover each other.

Minimum kernel: Linux 5.15+ (Landlock ABI v2) is what v0 targets. 5.13 works for ABI v1 but is missing useful rules; 6.1+ enables ABI v3 (refer/truncate scoping) that we will use opportunistically.

### 4.2 Launcher shape (pseudocode)

```python
def build_sandbox_command(profile: SandboxProfile, agent_cmd: list[str]) -> list[str]:
    argv = ["bwrap"]
    for path in profile.fs_read_paths:
        argv += ["--ro-bind", path, path]
    for path in profile.fs_read_write_paths:
        argv += ["--bind", path, path]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    argv += ["--unshare-pid", "--unshare-uts", "--unshare-ipc"]
    if profile.network is NetworkPolicy.denied:
        argv += ["--unshare-net"]
    argv += ["--clearenv"]
    for k in profile.env_allowlist:
        if v := os.environ.get(k):
            argv += ["--setenv", k, v]
    if profile.seccomp_profile:
        argv += ["--seccomp", "10"]  # fd 10, wired by the caller
    argv += ["--"] + list(agent_cmd)
    return argv
```

The v0 module exposes `build_sandbox_command` as a pure function so it can be unit-tested without running anything.

For the `network=allowlist_proxy` case, the caller is responsible for starting a loopback-bound HTTP/HTTPS proxy on a known port, passing `HTTPS_PROXY=http://127.0.0.1:PORT` into the sandbox env allowlist, and binding loopback into the sandbox network. The proxy implementation is out of scope for v0; the profile records the allowlist so a follow-up PR can add the proxy.

### 4.3 Developer escape hatch

When `AGENT_PORTAL_SANDBOX_BACKEND=none` **and** `AGENT_PORTAL_ALLOW_PERMISSIVE_TIER=true`, `build_sandbox_command` returns `agent_cmd` unchanged (no wrapping). The session manager logs a `policy` audit frame recording the unsandboxed launch with user email, template id, and scope. This keeps the audit story intact even when isolation is intentionally disabled.

## 5. Session lifecycle

```
pending ──▶ launching ──▶ running ──▶ ending ──▶ ended
   │           │             │          │
   ▼           ▼             ▼          ▼
 failed      failed        failed    reaped
```

Transitions are owned by `SessionManager`. The adapter can only *report* status; the manager decides state changes.

Reaper: a single asyncio task started when the feature flag is on. Default policy:

- `idle_timeout_s = 3600` — no audit frames in 60 minutes
- `hard_ttl_s = 86400` — absolute cap of 24 hours
- Both configurable per-launch via `LaunchSpec.budget`

Reaping is graceful (`adapter.cancel(handle, reason)`), escalating to termination if the adapter reports `running` for >30 seconds after cancel.

## 6. API surface (v0)

All routes mount only when `FEATURE_AGENT_PORTAL_ENABLED=true`. All require auth; admin-only endpoints additionally require the existing admin group.

- `GET /api/agent-portal/templates` — list of launch templates the user may use.
- `POST /api/agent-portal/sessions` — submit a `LaunchSpec`; returns `{session_id, state}`.
- `GET /api/agent-portal/sessions` — list this user's sessions.
- `GET /api/agent-portal/sessions/{id}` — session detail including current state.
- `POST /api/agent-portal/sessions/{id}/cancel` — request graceful termination.
- `GET /api/agent-portal/sessions/{id}/audit` — download the chained JSONL (admin or owner).
- `GET /api/agent-portal/admin/config` — effective flags, enabled backends, default tier.

WebSocket attach (`/ws/agent-portal/{id}`) is deferred; v0 is file-audit only.

## 7. What we explicitly generalize (and why)

The inspiration for this work included a design assuming an SSH+tmux first adapter. That specific adapter is **not** v0. v0 builds only the substrate that every adapter shares. Specifically:

- The `RuntimeAdapter` protocol is defined with the smallest interface that still admits SSH+tmux, k8s, and SLURM implementations later. It does *not* carry SSH or tmux concepts in its types.
- Session state and audit are in-process + filesystem for v0. Any adapter that needs cross-process persistence (ssh+tmux, k8s) owns that concern inside its own handle.
- The sandbox profile model is adapter-agnostic. An adapter that runs the agent inside an existing cluster can ignore `fs_read_paths` and translate `NetworkPolicy` into a NetworkPolicy CRD.
- UI language stays neutral: "open session / end session / view transcript", not "attach / detach / pipe-pane". This keeps future adapter swaps invisible to the user.

## 8. What v0 deliberately leaves for follow-ups

Tracked here so we don't forget:

1. **`ssh_tmux` adapter** — persistent sessions across backend restarts, browser reattach, maintainer-workflow replacement. This is the compelling dogfood target.
2. **`kubernetes` adapter** — pod-per-session with NetworkPolicy + PodSecurityContext mirroring the sandbox profile.
3. **`slurm` adapter** — `sbatch` submission for HPC GPU nodes.
4. **Filtering egress proxy** — loopback-bound HTTP/HTTPS proxy with domain allow-list for `NetworkPolicy.allowlist_proxy`.
5. **Audit-shim binary** — out-of-process shim between the agent's stdio and the audit store, so adapter restarts don't drop bytes. For v0 we rely on the in-process tee.
6. **Browser attach UI** — xterm.js over WebSocket with keystrokes routed through the audit path. v0 is file-audit only.
7. **Launch template registry** — config file (`config/agent-portal-templates.json`) and UI to pick from curated templates. v0 accepts raw `LaunchSpec` for testing and expects the frontend to gate template choice.
8. **Credential injection via fd** — agents that need API keys must never see them in env vars visible to scrollback. v0 uses env allowlist; the follow-up wires a file descriptor mechanism.
9. **Artifact collection** — workspace tar + sha256 on end, downloadable via the API. v0 records artifact metadata only.
10. **Multi-user isolation on a single host** — tmux and bwrap do not isolate between UIDs on the same box. Any real multi-user deployment needs per-user workers or a per-session UID allocator. v0 assumes single-host, single-deployment.

## 9. Open questions

1. **Audit storage backend.** Local JSONL + fsync is fine for v0. When we gain a second adapter, should the audit store move to DuckDB (we already use it for chat history) or stay filesystem-first? Recommendation: stay filesystem-first; a later indexer can mirror to DuckDB.
2. **Session store.** In-memory is fine for v0. Is there appetite for persisting sessions across backend restarts (so an `ssh_tmux` adapter can reattach to its tmux)? Recommendation: yes, eventually, but not in v0 — the feature flag gives us space.
3. **Tier selection UX.** Should the user pick a tier, or does the template fix it? Recommendation: the template fixes a *maximum* tier; the user can downgrade but not upgrade.
4. **OPSEC constraints on audit content.** The existing `atlas/core/otel_config.py` hardens telemetry against leaking secrets. The audit log needs the same discipline — `preview()`/`safe_label()` semantics applied to tool call args and errors. To wire in as a v0 follow-up before any real agent stdin/stdout lands in the audit stream.
5. **Interaction with `FEATURE_AGENT_MODE_AVAILABLE`.** The in-chat agent loop already exists. Do we want to co-exist indefinitely, or is the portal supposed to eventually replace in-chat agent mode? Recommendation: co-exist; in-chat stays the "quick ask" path, the portal is for "governed launch".

## 10. File layout introduced by v0

```
atlas/
├── interfaces/
│   └── agent_portal.py                # Protocols: RuntimeAdapter, SandboxLauncher
├── modules/
│   └── agent_portal/
│       ├── __init__.py
│       ├── models.py                  # LaunchSpec, Session, SandboxProfile, enums
│       ├── session_manager.py         # state machine, in-memory repo
│       ├── audit.py                   # SHA-256 chained JSONL writer
│       ├── service.py                 # AgentPortalService (public facade)
│       ├── sandbox/
│       │   ├── __init__.py
│       │   ├── profiles.py            # default restrictive/standard/permissive
│       │   └── launcher.py            # build_sandbox_command() pure function
│       └── adapters/
│           ├── __init__.py
│           ├── base.py                # re-exports RuntimeAdapter
│           └── local_process.py       # v0 adapter (subprocess + in-process tee)
└── routes/
    └── agent_portal_routes.py         # /api/agent-portal/*  (mounted only if flag)
atlas/tests/
├── test_agent_portal_profiles.py
├── test_agent_portal_launcher.py
├── test_agent_portal_session_manager.py
├── test_agent_portal_audit.py
└── test_agent_portal_feature_flag.py
docs/planning/
└── agent-portal-2026-04-20.md         # this document
```

## 11. Acceptance for this PR (if merged)

This PR should NOT be merged without an explicit request. When it is considered:

- [ ] All new code is behind `FEATURE_AGENT_PORTAL_ENABLED=false` by default.
- [ ] With the flag off, the test suite passes with zero new endpoints mounted and zero background tasks started.
- [ ] With the flag on, unit tests for sandbox profile → argv construction, session state transitions, and audit chain verification all pass.
- [ ] `ruff check atlas/` is clean on the new files.
- [ ] `AGENTS.md` gains a one-paragraph "Agent Portal (experimental)" section linking here.
- [ ] `.env.example` adds the three new flags with default-safe values.
- [ ] `CHANGELOG.md` under `[Unreleased]` gets a single line describing the foundation landing behind a flag.
