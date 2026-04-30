# Code Executor v2 — Planning Doc

**Status:** Draft / awaiting implementation
**Date:** 2026-04-30
**Owner:** TBD
**Target folder:** `atlas/mcp/code-executor-v2/` (sibling to existing `code-executor/`)
**Deployment target:** OpenShift pod (separate from Atlas main pod)

---

## 1. Goals

Build a v2 secure Python execution MCP server that:

1. Speaks **streamable HTTP** transport (per-conversation isolated sessions).
2. Is **stateful** within a session: persistent workspace across multiple tool calls until the session ends.
3. Uses **kernel enforcement** (Landlock + network namespace + rlimits) as the security boundary, not Python AST blocklists.
4. Refuses to start if the kernel does not support the required enforcements.
5. Exposes a richer tool surface: `python`, `upload_file`, `ls`, `read_file`, `write_file`, `delete_file`, `download_file`, `info`, `reset_session`, plus an experimental `git_clone` for pulling a repo via a user-supplied PAT.
6. Returns clean v2 artifacts (raw file bytes + correct MIME) — no synthesized HTML wrapper.
7. Ships with a broader data-science / engineering package set preinstalled.
8. **Wipes the workspace fully when the session ends.**

---

## 2. Non-goals (v2.0)

- Persistent REPL across calls. Each `python` call is a fresh subprocess. State survives via files only.
- Runtime `pip install`. The pod ships with a curated package set; the sandbox blocks network so pip cannot work anyway.
- Full multi-tenant isolation between concurrent calls in the same session (one session = one workspace; concurrent calls into the same session use file locks but are serialized at the call level by FastMCP).
- Seccomp-bpf filter. Defer to v2.1 — netns + Landlock + rlimits is a real boundary already.

---

## 3. Architecture overview

```
┌──────────────────────────────────────────────────────────────────┐
│  OpenShift pod: code-executor-v2                                  │
│                                                                   │
│   FastMCP HTTP server (streamable, port 8011)                     │
│       │                                                            │
│       ├─ session_state_store  (per-conversation isolation)        │
│       │     • workspace_dir   /workspaces/<session_id>/           │
│       │     • last_seen_mtime (artifact diff)                     │
│       │     • created_at, last_used                               │
│       │                                                            │
│       └─ on session close / TTL → shutil.rmtree(workspace_dir)    │
│                                                                   │
│   Each `python` tool call:                                        │
│     subprocess.run([                                              │
│         python, _sandbox_launch_v2.py,                            │
│         --mode strict --workdir <ws> --net none                   │
│         --mem-mb 2048 --cpu-s 30 --fsize-mb 256                   │
│         -- python -c <code>                                       │
│     ], timeout=wall_clock)                                        │
│                                                                   │
│   _sandbox_launch_v2.py (child process, before execvp):           │
│     1. unshare(CLONE_NEWUSER | CLONE_NEWNET)                      │
│        → write uid_map / gid_map → bring lo DOWN (default)        │
│     2. setrlimit(RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE, RLIMIT_NPROC)│
│     3. prctl(PR_SET_NO_NEW_PRIVS, 1)                              │
│     4. landlock_restrict_self(workdir RW + system roots R+X only) │
│     5. os.execvp("python", ["python", "-c", user_code])           │
└──────────────────────────────────────────────────────────────────┘
```

The pod is the existing isolation layer (already split out from main Atlas). Within the pod, Landlock + netns + rlinmits are a defense-in-depth layer so a compromise of the executor process cannot pivot to other workspaces in the same pod or reach the network.

---

## 4. Sandbox layers

In order of which catches what:

| Layer | What it blocks | Required? |
|---|---|---|
| OpenShift pod boundary | Cross-pod, host filesystem, host network policies | Already in place |
| Network namespace (`CLONE_NEWNET`) | All outbound IP traffic from the user code subprocess | **Required** |
| User namespace (`CLONE_NEWUSER`) | Lets us create the netns without CAP_NET_ADMIN | **Required** |
| Landlock (strict mode) | All FS access outside workspace, except R+X on `/usr /lib /lib64 /bin /etc /opt /proc /sys`, and R+W on `/dev` | **Required** |
| `PR_SET_NO_NEW_PRIVS` | setuid escape | **Required** (prereq for unprivileged Landlock) |
| `setrlimit(RLIMIT_AS)` | Memory bombs | Required (default 2 GiB) |
| `setrlimit(RLIMIT_CPU)` | CPU bombs | Required (default 30 s) |
| `setrlimit(RLIMIT_FSIZE)` | Single-file disk bombs | Required (default 256 MiB) |
| `setrlimit(RLIMIT_NPROC)` | Fork bombs | Required (default 64) |
| `subprocess.run(timeout=…)` | Wall-clock runaway | Required |
| Workspace size watcher | Cumulative disk bombs across many small files | Required (256 MiB hard cap) |

### Startup precondition

`atlas/modules/process_manager/landlock.is_supported()` must return `True`. If not, the server **refuses to boot** with a clear error:

```
FATAL: Landlock unavailable on this kernel. Code Executor v2 requires
CONFIG_SECURITY_LANDLOCK (Linux >= 5.13). Set ALLOW_UNSAFE_NO_SANDBOX=1
to override (development only).
```

We also probe `unshare(CLONE_NEWUSER|CLONE_NEWNET)` once at boot in a child process and refuse to start if it fails (some hardened kernels disable user namespaces).

---

## 5. Tool surface (stateful, HTTP MCP)

All tools operate on the per-session workspace at `/workspaces/<session_id>/`. The session ID is the FastMCP HTTP session, scoped per-conversation by the existing PR #559 plumbing.

### `python(code: str, timeout?: int = 30) → ToolResult`
- Spawns a fresh sandboxed subprocess.
- Snapshots workspace mtimes before, diffs after.
- Returns:
  - `results`: `{stdout, stderr, returncode, summary}`
  - `meta_data`: `{execution_time_sec, sandbox: {fs:"landlock", net:"none", mem_mb, cpu_s, fsize_mb}, workspace_bytes_used}`
  - `artifacts`: only files newly created or modified, base64'd with correct MIME
  - `display`: `{open_canvas: bool, primary_file: <first image or first artifact>, mode: "append"}`
- Auto-savefig of any open matplotlib figures (kept from v1).
- No HTML visualization wrapper.

### `upload_file(filename: str, file_data_base64?: str, file_url?: str) → ToolResult`
- Same loader logic as v1 (`_load_file_bytes` handles base64 + backend `/api/files/download/...` URLs + arbitrary http(s) URLs).
- Writes to workspace under sanitized basename. Reject if would exceed workspace cap.

### `ls(path: str = "") → ToolResult`
- Lists workspace contents (or sub-path under workspace). Returns `[{name, size, mtime, is_dir}]`.

### `read_file(path: str, max_bytes: int = 1_000_000, encoding?: str) → ToolResult`
- Read text or binary from workspace. Refuses paths outside workspace.
- `encoding=None` returns base64; otherwise tries the given encoding (default utf-8).

### `write_file(path: str, content?: str, content_base64?: str) → ToolResult`
- Write text or binary into workspace. Refuses paths outside workspace, refuses if would exceed workspace cap.

### `delete_file(path: str) → ToolResult`
- Delete a single file in workspace.

### `download_file(path: str) → ToolResult`
- Returns the named file as a v2 artifact (base64 + MIME) for the user to download.

### `info() → ToolResult`
- `{installed_packages: [...], kernel: {landlock_abi, netns_supported}, limits: {...}, workspace: {path, bytes_used, bytes_cap}, session_id, created_at}`.

### `reset_session() → ToolResult`
- `shutil.rmtree(workspace)` then `mkdir`. Clears `last_seen_mtime`. Returns `{cleared: true}`.

### `git_clone(repo_url: str, pat?: str, ref?: str = "HEAD", subdir?: str) → ToolResult` *(experimental, behind feature flag)*
- This is the **only** tool that needs network. It runs in a separate subprocess that **does not** apply the netns layer (Landlock + rlimits + workspace-only writes still apply).
- Clones into `<workspace>/<subdir or repo-basename>/`.
- PAT injected into the URL only inside the child process via env var, never logged.
- Hard caps: 100 MiB clone size, 60 s timeout, single shallow clone (`--depth 1`).
- Disabled by default; enable via `CODE_EXECUTOR_V2_ENABLE_GIT_CLONE=1`.
- After clone, all subsequent `python` calls still run with full netns isolation against the cloned tree.

---

## 6. Workspace lifecycle & cleanup

| Event | Action |
|---|---|
| First tool call in a session | Create `/workspaces/<session_id>/`, store metadata in session state |
| Each tool call | Touch `last_used` timestamp |
| `reset_session()` called | `shutil.rmtree(ws); mkdir(ws)` |
| FastMCP session close (HTTP session ends) | `shutil.rmtree(ws)`, drop session state keys |
| TTL exceeded (default 1 h idle) | Background reaper task: `shutil.rmtree(ws)`, drop state |
| Pod shutdown / SIGTERM | Graceful: walk all live sessions, `rmtree` each |
| Pod cold start | `/workspaces/` is wiped on boot — workspaces are not persistent across restarts |

The reaper is a single asyncio task started with the FastMCP server; it scans every 5 min for sessions whose `last_used` exceeds TTL. Cleanup is best-effort; failures are logged but do not block the reaper.

**Hard caps:**
- Per-workspace disk: `256 MiB` (configurable via `CODE_EXECUTOR_V2_WS_CAP_MB`).
- Single artifact returned in a tool call: `10 MiB` (anything larger is referenced but not inlined; user fetches via `download_file`).
- Concurrent sessions per pod: `100` (configurable). Beyond this, `python` rejects with a clear error.

---

## 7. Result / artifact format

Drop the v1 HTML wrapper entirely. Every tool returns the standard v2 envelope:

```json
{
  "results": { ... small primary payload ... },
  "meta_data": { "is_error": false, ... },
  "artifacts": [
    {
      "name": "plot.png",
      "b64": "...",
      "mime": "image/png",
      "size": 14523,
      "description": "Generated plot",
      "viewer": "image"
    }
  ],
  "display": {
    "open_canvas": true,
    "primary_file": "plot.png",
    "mode": "append",
    "viewer_hint": "image"
  }
}
```

MIME is decided by extension. Existing canvas viewers cover image / code / html / text. No bespoke styling injected by the executor.

---

## 8. Package set

Preinstalled in the pod's Python venv (defined as a `code-executor-v2` extra in `pyproject.toml`, but for the pod they're the base set):

**Core data:** numpy, pandas, polars, pyarrow, duckdb, openpyxl, xlsxwriter
**Stats / ML:** scipy, statsmodels, scikit-learn, sympy, joblib, tqdm
**Plotting:** matplotlib, seaborn, plotly, kaleido (for plotly→png)
**Graphs / geo (light):** networkx, shapely
**Image:** pillow, opencv-python-headless
**Parsing:** beautifulsoup4, lxml, html5lib, python-dateutil, pytz, jsonschema
**Templating:** jinja2

Heavy / optional (decide before merge): geopandas, rasterio, torch, transformers — likely **out** for v2.0 to keep image small; gated behind a separate "heavy" image variant.

---

## 9. New code layout

```
atlas/mcp/code-executor-v2/
    main.py                 # FastMCP HTTP server, all @mcp.tool definitions
    session.py              # Workspace lifecycle, mtime diffing, reaper
    sandbox/
        launcher.py         # Builds the subprocess argv, applies wall-clock timeout
        _sandbox_launch_v2.py  # Standalone, stdlib-only. Run via execvp.
                               # Adds netns + rlimits to the existing
                               # _sandbox_launch.py logic. Self-contained.
        kernel_probe.py     # is_landlock_supported() + can_create_netns()
    artifacts.py            # mtime snapshot/diff, base64+MIME encoding
    file_ops.py             # safe path resolution, ls/read/write/delete bounded to workspace
    git_clone.py            # gated git_clone tool implementation
    config.py               # env-var driven config, caps, TTL
```

Reuses (no duplication):
- `atlas/mcp_shared/server_factory.py` pattern (but creates an HTTP-flavor factory variant).
- `atlas/mcp/common/state.get_state_store()`.
- `atlas/modules/process_manager/landlock.is_supported()` for the boot probe.

The new `_sandbox_launch_v2.py` is its own file (does not import the existing one) for the same reason the existing one is standalone: the child runs with the user's cwd, not the project root, so it must be self-contained stdlib.

---

## 10. Configuration

Env vars consumed at startup:

| Var | Default | Meaning |
|---|---|---|
| `MCP_CODE_EXECUTOR_V2_PORT` | `8011` | HTTP port |
| `MCP_CODE_EXECUTOR_V2_HOST` | `0.0.0.0` | Bind host |
| `CODE_EXECUTOR_V2_WORKSPACES_DIR` | `/workspaces` | Workspace root inside pod |
| `CODE_EXECUTOR_V2_WS_CAP_MB` | `256` | Per-workspace disk cap |
| `CODE_EXECUTOR_V2_ARTIFACT_CAP_MB` | `10` | Max single-artifact inline size |
| `CODE_EXECUTOR_V2_SESSION_TTL_S` | `3600` | Idle TTL before reaper wipes |
| `CODE_EXECUTOR_V2_MAX_SESSIONS` | `100` | Concurrent session cap |
| `CODE_EXECUTOR_V2_MEM_MB` | `2048` | RLIMIT_AS for child |
| `CODE_EXECUTOR_V2_CPU_S` | `30` | RLIMIT_CPU for child |
| `CODE_EXECUTOR_V2_FSIZE_MB` | `256` | RLIMIT_FSIZE for child |
| `CODE_EXECUTOR_V2_NPROC` | `64` | RLIMIT_NPROC for child |
| `CODE_EXECUTOR_V2_WALL_S` | `60` | Outer wall-clock timeout |
| `CODE_EXECUTOR_V2_ENABLE_GIT_CLONE` | `0` | Enable `git_clone` tool |
| `CODE_EXECUTOR_V2_ALLOW_UNSAFE_NO_SANDBOX` | `0` | Boot without kernel enforcement (dev only) |

Atlas-side `mcp.json` registration:

```json
"code_executor_v2": {
  "url": "http://code-executor-v2:8011/mcp",
  "transport": "http",
  "groups": ["users"],
  "description": "Stateful Python execution sandbox: per-session workspace, Landlock + network namespace isolation, broad data-science package set",
  "short_description": "Code executor v2 (HTTP, stateful, sandboxed)",
  "compliance_level": "Public"
}
```

Example config also dropped at `atlas/config/mcp-example-configs/mcp-code_executor_v2.json`.

---

## 11. Rollout plan

1. **Phase 1 — scaffold.** New folder, `info()` tool only, HTTP transport, kernel probe. No `python` execution yet. Verify session state isolation between two conversations.
2. **Phase 2 — sandbox launcher.** Implement `_sandbox_launch_v2.py` (netns + rlimits + Landlock + execvp). Unit-test it directly: a sandboxed `bash -c 'echo > /tmp/escape'` must fail; `curl https://1.1.1.1` must fail; reading `/etc/passwd` must succeed; writing under workspace must succeed.
3. **Phase 3 — `python` tool + artifact diff.** Wire end-to-end: upload a CSV, run pandas, get a PNG back.
4. **Phase 4 — file ops.** `ls`, `read_file`, `write_file`, `delete_file`, `download_file`, `reset_session`.
5. **Phase 5 — lifecycle.** Reaper task, TTL cleanup, session-close cleanup, pod-shutdown cleanup. Soak test: run 100 sessions, kill the pod, verify no leftover workspaces survive a clean restart.
6. **Phase 6 — pod / image.** Build the OpenShift image with the curated package set. Smoke-test end-to-end against a dev cluster.
7. **Phase 7 (optional) — `git_clone`.** Behind feature flag. Add per-PAT logging suppression.
8. **Phase 8 — deprecate v1.** Mark v1 example config as legacy. Keep v1 around for one release for fallback.

Each phase is a separate PR.

---

## 12. Test plan

- **Sandbox unit tests** (must fail in red, pass in green):
  - `socket.create_connection(("1.1.1.1", 80))` → fails with EHOSTUNREACH or similar inside sandbox.
  - `urllib.request.urlopen("https://example.com")` → fails.
  - `open("/etc/shadow", "w")` → PermissionError (Landlock).
  - `open("/etc/passwd", "r")` → succeeds.
  - `open("<workspace>/x.txt", "w")` → succeeds.
  - `:(){:|:&};:` style fork bomb → bounded by RLIMIT_NPROC, killed by wall-clock.
  - 4 GiB allocation → MemoryError before OOM-killer.
- **Lifecycle tests:**
  - Two HTTP sessions: workspace A is invisible to session B (`ls` confirms).
  - Idle session: reaper wipes after TTL.
  - Pod SIGTERM: graceful cleanup runs.
- **Integration test:** upload a 1 MB CSV, run pandas describe + matplotlib plot, get the PNG artifact back, verify no HTML wrapper present.
- **Boot precondition test:** in CI, run with `ALLOW_UNSAFE_NO_SANDBOX=0` on a kernel without Landlock and assert exit code 1 with a clear message.

---

## 13. Open questions for follow-up

1. Does the OpenShift PSP allow `CLONE_NEWUSER` in the pod? If unprivileged user namespaces are disabled at the host, we can't create the netns. Need to check `/proc/sys/user/max_user_namespaces` and `/proc/sys/kernel/unprivileged_userns_clone` on the target cluster before phase 2.
2. Where should workspaces live — emptyDir (cleared on restart, cheap) or a PVC (survives restart, but conflicts with "wipe everything when session ends" — we'd want emptyDir).
3. Should `git_clone` proxy through an internal git mirror instead of letting the child reach external GitLab directly? More complex, but tighter network policy.
4. Do we want a per-user (vs. per-conversation) workspace as a future option, e.g. for users who want to keep state across chats? Out of scope for v2.0.

---

## 14. Things explicitly carried over from v1

- The `_load_file_bytes` loader logic (base64 / backend URL / direct URL).
- Auto-savefig for open matplotlib figures.
- v2 artifact envelope shape.
- The 30 s default per-call timeout (now configurable).

## 15. Things explicitly removed from v1

- AST security checker (`security_checker.py`). The kernel is the boundary now.
- `safe_open` builtin monkey-patch (`script_generation.py`). Same reason.
- The synthesized HTML visualization wrapper (`create_visualization_html`). Returns raw artifacts only.
- The "allowed modules" import allow-list. Any preinstalled package is fair game.
- Single-call statelessness (every call wiping the dir).
