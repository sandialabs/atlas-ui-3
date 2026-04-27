# Agent Portal — Action Plan

**Guiding principle:** Don't become tmux. Win on **governance** (per-pane sandbox/cgroup/audit) and on **discoverability** (command palette, not prefix keys). Every feature must either (a) strengthen the governance story or (b) make the easy path more discoverable than tmux. If it does neither, it doesn't ship.

## Target & deployment trajectory

- **Primary target today:** single user, Atlas running on the user's own machine. All design decisions optimize for this case first.
- **Future-proof for:** (a) each agent in its own single-instance container on the same host, (b) Atlas + agents running on a remote server with the user's browser as the only client.
- **Implication:** treat process spawning as a pluggable backend behind `ProcessManager`. Today it's local `execvp` + Landlock + cgroups. Tomorrow it's `docker run` / `podman run` per agent, or an SSH-tunneled spawn on a remote host. Keep the `ProcessManager.launch(...)` signature stable; vary the **executor** behind it.
- **What this changes now:**
  - Don't bake host-only assumptions into the WS stream protocol — frame everything as `{stream, bytes, ts, exit?}` so a remote/container executor can produce the same envelope.
  - Don't assume cwd is a host path the browser knows about — render breadcrumbs from server-supplied strings, never from `path.join(window.location, …)`.
  - Group budgets (Phase 3) need to be expressible as either cgroup limits *or* container `--memory` / `--cpus` flags. Schema stays the same; enforcement layer differs.
  - Audit log entries (Phase 4) include an `executor` field (`local | container | remote`) from day one so future log readers don't have to guess.
- **What this does NOT change:** every phase below ships first against the local executor. Container/remote executors are additive, not a rewrite. No speculative abstraction beyond the `ProcessManager.launch` seam that already exists.

## Palette key binding — DECIDED: `Ctrl-Shift-P`

VS Code-native. Two-modifier chord is slightly more awkward to type but conflicts with almost nothing inside a terminal — `Ctrl-B` would be shadowed by `less`/`vim`/etc.; `Ctrl-K` collides with the readline "kill-to-end-of-line" that bash users hit constantly. `Ctrl-Shift-P` is clean.

Implementation note: still gate on `document.activeElement` so a focused xterm gets first dibs in the rare case some app rebinds it; only the portal chrome should swallow the chord by default.

---

## Phase 1 — Multi-pane layout (frontend only)

**Goal:** show >1 process at once without losing scrollback.

- [ ] Add layout modes to the right pane: `single`, `2x2`, `3x2`, `focus+strip`. Plain CSS grid; no docking lib.
- [ ] Cells are slot-keyed (key on slot index, not process id) so dragging a process between slots doesn't remount xterm.
- [ ] One `ResizeObserver` per cell wired to that cell's `FitAddon`. Call `fit()` *after* the grid template recomputes (avoid one-frame garbled rows).
- [ ] Backgrounded panes keep WS open and append to an offscreen xterm (or a ring buffer) so switching them into a slot shows full scrollback, not a reconnect.
- [ ] Soft cap **6** live xterm cells, hard cap **9**. Soft cap → banner. Hard cap → refuse, require swap.
- [ ] Persist current layout per-user in `localStorage` (`atlas.agentPortal.layout.v1`).
- [ ] Fullscreen is just `single` with chrome hidden. `F` toggles, `Esc` exits.
- [ ] **Survive browser refresh (F5).** On mount: (1) fetch current process list, (2) restore layout + slot→process mapping from `localStorage`, (3) reopen WS streams for visible panes, (4) repaint scrollback from the server's per-process output buffer. Confirm the buffer holds at least one full screen of output; extend on the server side if not.

**Done when:** can launch 4 processes, see them all live in 2x2, fullscreen one, return to grid with no scrollback loss, **and** F5 brings the same layout back with the same panes still streaming.

---

## Phase 1.5 — Server-side `PortalStore` (server + frontend)

**Goal:** move config/UI state off the browser. `localStorage` becomes a first-paint cache only; the server is the source of truth. Pre-requisite for Phase 3 (group definitions can't live in browser).

- [ ] Add `PortalStore` using **DuckDB via `duckdb-engine` + SQLAlchemy**, mirroring the pattern already in `atlas/modules/chat_history/database.py`. Default URL `duckdb:///data/agent_portal.db` (resolve relative to `atlas_root` like chat_history does), overridable via env var `AGENT_PORTAL_DB_URL`. Reuse the existing engine/sessionmaker conventions — no new persistence stack.
- [ ] Tables / collections (per-user keyed):
  - `presets` — already exists in `presets_store.py`; either migrate into `PortalStore` or keep co-existing behind one read interface. Don't rewrite for the sake of it.
  - `launch_history` — was localStorage `atlas.agentPortal.launchHistory.v1`.
  - `launch_configs` — was localStorage `atlas.agentPortal.launchConfigs.v1`.
  - `layouts` — last-known layout mode + slot→process_id mapping per user.
  - `groups` *(stub schema; Phase 3 fills it in)*.
- [ ] REST endpoints under `/api/agent-portal/state/*`. Keep them boring: `GET` returns the user's blob, `PUT` replaces. No diffing, no optimistic concurrency for v1 — single-user target.
- [ ] Frontend: replace `localStorage.getItem/setItem` for these keys with HTTP. On mount, kick off the server fetch in parallel with reading `localStorage` so the cached value paints first and gets reconciled when the server responds.
- [ ] Migration: on first server-side fetch returning empty, the client uploads its existing `localStorage` blob once. After that, browser cache is read-only-on-startup, write-through-to-server otherwise.

**Process state stays in-process for now.** This phase is explicitly about config/UI state. The `ProcessManager` registry stays where it is; restart-survival is *not* in scope. See Q7 in Open questions for the daemon-vs-no-daemon reasoning.

**Done when:** clearing browser `localStorage` and reloading shows the same presets, history, configs, and last layout. Two browser tabs see the same presets without one needing to be reloaded.

---

## Phase 2 — Command palette (frontend only)

**Goal:** discoverability without prefix keys or memorized bindings.

- [ ] Add `cmdk` (Vercel, headless, ~5 KB). Style with existing Tailwind.
- [ ] `Ctrl-Shift-P` opens, `Esc` closes. Gate on `document.activeElement` so a focused xterm can opt out. Fuzzy match over a flat action list.
- [ ] Action shape: `{ id, title, hint, scope, run, when }`. `scope` ∈ Process / Layout / Group / Global. `when` gates by state.
- [ ] Seed actions:
  - New launch
  - Launch from preset…
  - Switch to pane 1–9
  - Move pane to slot N
  - Toggle fullscreen
  - Layout: single / 2x2 / 3x2 / focus+strip
  - Broadcast input to group… *(stub until Phase 5)*
  - Cancel pane
  - Cancel group *(stub until Phase 3)*
  - Rename pane
  - Save current as preset
  - Open audit log *(stub until Phase 4)*
- [ ] Base bindings (flat, not chorded):
  - Numbers `1`–`9` → jump to slot. **Gate on `document.activeElement` so terminal input still wins.**
  - `Ctrl-Shift-Arrow` → move focus between panes.
  - `F` → fullscreen toggle.
- [ ] Inline hint strip at bottom of cell when idle >5 s, no output: "Ctrl-Shift-P · F fullscreen · 1–9 jump". Auto-hides on next output.

**Done when:** can do every existing portal action via Ctrl-K, never need a mouse, base bindings discoverable from inside the terminal.

---

## Phase 3 — Groups (server + frontend)

**Goal:** make "group" a server-enforced object, not a UI fiction.

- [ ] Server schema: `{ id, name, owner, max_panes, mem_budget_bytes, cpu_budget_pct, idle_kill_seconds, audit_tag }`.
- [ ] `ProcessManager.launch` accepts optional `group_id`. Reject if adding the process would exceed pane count or sum-of-cgroup budgets.
- [ ] Parent cgroup per group; child cgroups nest under it. Defense-in-depth: even if a child misbehaves, the parent caps it.
- [ ] REST: `POST /api/agent-portal/groups`, `GET /api/agent-portal/groups`, `DELETE /api/agent-portal/groups/{id}` (idempotent, SIGTERM all members → SIGKILL after grace).
- [ ] Frontend: left rail groups processes by `group_id`. Per-group header shows used/budget for mem & CPU.
- [ ] Per-pane breadcrumb in cell header: `cwd · sandbox-mode · group-name`.

**Done when:** launching a 5th pane into a group with `max_panes: 4` is rejected by the server (not the client), and group cancel reaps all members.

---

## Phase 4 — Preset bundles + audit log (server)

**Goal:** one-click multi-agent launch, with auditability that stands up to compliance review.

- [ ] Bundle schema: `{ name, group_template, members: [{preset_id, display_name_override?}, …] }`. Stored in `presets_store.py`.
- [ ] `POST /api/agent-portal/bundles/{id}/launch` instantiates the group, then launches each member with `group_id` set. Atomic-ish: if any member fails, tear down the group.
- [ ] Audit log (extends graduation-checklist item):
  - Sink: separate JSONL file from app logs.
  - Events: launch, cancel, rename, group create, group budget change, pane-to-group move, sync-input toggle, sync-input keystroke summary (count + bytes, not raw — admin opt-in for raw).
  - Schema: `{ ts, user, event, group_id?, process_id?, …event-specific }`.
- [ ] Shareable URL: `/agent-portal?preset=<id>` and `/agent-portal?bundle=<id>`. Server validates auth + ownership at launch, not at URL parse.
- [ ] "What ran here last" recall: selecting an exited process shows exit code, duration, **Re-launch** button that pre-fills the form.

**Done when:** a teammate clicks a bundle URL, auths, and gets 3 sandboxed agents launched into a budgeted group. Every action shows up in the audit JSONL.

---

## Phase 5 — Synchronize-input (server-heavy)

**Goal:** tmux's killer feature, done safer.

- [ ] Server-side `BroadcastSession`: stdin from one WS fans out to all group members' stdin pipes. **Server-side (not client mirroring)** so audit captures one event with N recipients.
- [ ] Per-group toggle in the header strip. Off by default.
- [ ] Visible affordance: colored border around grouped panes when sync is on. Don't repeat tmux's `synchronize-panes`-is-invisible footgun.
- [ ] Audit event per broadcast (count + bytes; raw only with admin opt-in).

**Done when:** typing `pwd\n` in one synced pane shows `pwd` in all members, border is unmistakable, audit log shows one broadcast event with N recipients.

---

## Phase 6 — Polish & enforcement (server + UX)

- [ ] Idle-kill: per-group `idle_kill_seconds` actually fires (timer resets on stdout/stderr activity).
- [ ] Mem/CPU budget enforcement verified end-to-end (oom kill in a child surfaces a clear error in the pane, not a silent exit).
- [ ] Breadcrumb + inline hint strip refinement based on actual use.
- [ ] Pause group (SIGSTOP) and Snapshot group (tarball of every pane's scrollback) actions in the palette.

---

## Explicitly NOT building

- **Drag-resize splits.** CSS grid presets cover 95% of real layouts. Drag-resize triples complexity (refit storms, persistence edge cases, mobile breakage).
- **Nested groups.** Flat is enough. Refuse until two real users hit the limit.
- **Custom keybinding editor.** Palette + fixed base layer. Custom bindings are a long-tail bug source; palette makes them unnecessary.
- **Mobile parity.** Declare desktop-only. A 6-pane PTY grid on mobile will eat months for zero users.

---

## Biggest risk

Scope creep dressed as "we're already 80% of the way to tmux, let's add splits / nesting / custom binds." Every one of those moves off the governance differentiator and into a fight against a 20-year-old C program. **The test:** does this feature strengthen governance OR make the easy path more discoverable than tmux? If neither, it doesn't ship.

---

## Open questions — resolutions

1. **Group ownership transfer** → **NO.** Owner is fixed at create. No transfer endpoint, no shared-owner field in the schema. Keeps the ownership check in Phase 3 trivial: `group.owner == request.user`, full stop. If incident-response sharing is ever needed, add it then; do not pre-build it.

2. **Cross-conversation context** → **scope = "session must survive F5".** Originally asked whether a group should be pinned to a chat `conversation_id` (like the recent MCP per-conversation work). Resolution: groups are **independent of chat conversations**. The actual requirement is that hitting **browser refresh (F5) does not lose the running processes or their scrollback** — that's a frontend reconnect problem, not a conversation-binding one.
   - **Implementation:** processes already live on the server (`ProcessManager`), so they survive F5 by definition. The work is on the frontend: on mount, fetch the current process list, restore selected slot/layout from `localStorage`, reopen WS streams to each visible pane. Server already buffers recent output (`StreamView` chunks); confirm the buffer is large enough to repaint a full screen on reconnect, and if not, extend it.
   - **Out of scope:** survival across server restarts (see #3) and survival across browser tabs / different machines (see #5/#6).

3. **Persistence across server restarts** → **NO, not for now.** Processes die when Atlas restarts. Doing otherwise means offloading state to a separate supervisor service (systemd units, a sidecar, etc.) and that complexity is not worth paying yet. Document the limitation in the portal UI ("processes do not survive Atlas restart") so users aren't surprised.

4. **Multi-tenant deploy / "roots"** → **N/A for now.** "Roots" was shorthand for the graduation-checklist item about per-user workspace roots (e.g. `/srv/atlas/workspaces/$USER`) used to validate `cwd` and `extra_writable_paths`. Since the target is single-user-on-own-machine, **all child processes run as the same OS user that's running Atlas**. No per-user OS isolation, no per-tenant roots. The graduation checklist still applies *if and when* the feature ever moves off the user's own machine — but that's a future-Atlas problem, not a Phase-3 problem.

5. **Container/remote rollout order** → **deferred — folds into the state-management question (#7).**

6. **Where state lives when Atlas is remote** → **deferred — folds into the state-management question (#7).**

7. **State management — RESOLVED by splitting into two questions.**

   The original framing ("do we need a stateful daemon like tmux?") collapsed two very different problems. Pulling them apart:

   **Q-A — Where does configuration/UI state live?** (presets, history, layout, slot→process mapping, group definitions, audit log)
   - **Decision: server-side, DuckDB-backed `PortalStore` (reusing the existing `duckdb-engine` + SQLAlchemy stack from `chat_history/database.py`). Do this now (new Phase 1.5 below).**
   - `localStorage` demoted to a first-paint cache only.
   - Cheap to build, removes the existing split-state mess, and is a hard pre-requisite for Phase 3 (group definitions can't live in browser if they're to be server-enforced).

   **Q-B — Where do running processes live, and do they survive Atlas restart?**
   - **Decision: in-process today (Atlas owns the children, as now). Defer the daemon.**
   - Build the *interface* such that `ProcessManager` looks like "talk to a process registry that may not be in this address space." Keep the implementation in-process for now. If/when restart-survival becomes a requirement, lift the implementation into a separate `atlas-portald` over a unix socket without rewriting callers.
   - Same executor-seam discipline as the container/remote question: design the seam, don't pre-build the abstraction.

   **Why not build the daemon now:**
   - Lifecycle complexity (auto-start? systemd unit? tmux-style attach/detach?) is real overhead for single-user-on-own-machine.
   - IPC schema + version skew is a forever-maintenance commitment.
   - You already get F5-survival without a daemon (Phase 1 work) because Atlas owns the processes.
   - Restart-survival is explicitly off the near-term list (Q3).

   **Why not `systemd-run --user` as a free daemon:**
   - Locks the executor to Linux + systemd-as-PID-1; kills containers that don't run systemd inside, and any future macOS dev-machine support.
   - Streaming raw PTY through journald is a fight.
   - Inherits systemd's lifecycle opinions whether or not they match Atlas's.

   **The third hidden question — multi-client attach** (two browser tabs / two devices on the same session): not a daemon question. It's a "WS supports multiple subscribers per process" question. Server-side fan-out from one stdout reader to N WS clients. Defer until actually needed; doesn't constrain anything now.
