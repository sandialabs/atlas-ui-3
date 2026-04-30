# Code Executor v2

A self-contained MCP server that runs user-supplied Python in a kernel-enforced
sandbox: **Landlock** (filesystem) + **user/network namespace** (network blackhole)
+ **rlimits** (memory / CPU / file size / process count) + an outer wall-clock
timeout. Each conversation gets its own persistent workspace; state survives
across tool calls via files.

This module is intended to be deployed as its own pod (a sibling to the main
Atlas pod). It does **not** import Atlas application code beyond the shared
`atlas.mcp.common.state` helper.

The design rationale lives in [`docs/planning/code-executor-v2-2026-04-30.md`](../../../docs/planning/code-executor-v2-2026-04-30.md).

## Tools

| Tool | Purpose |
|---|---|
| `python(code, timeout?)` | Execute Python in the session workspace; returns stdout/stderr + any new files as v2 artifacts |
| `upload_file(filename, file_data_base64? \| file_url?)` | Drop a file into the workspace |
| `ls(path?)` | List workspace contents |
| `read_file(path, max_bytes?, encoding?)` | Read a workspace file |
| `write_file(path, content? \| content_base64?)` | Write a workspace file |
| `delete_file(path)` | Delete a workspace file or sub-tree |
| `download_file(path)` | Return a workspace file as a v2 artifact |
| `info()` | Installed packages, sandbox status, limits, session info |
| `reset_session()` | Wipe the workspace and clear state |
| `git_clone(repo_url, pat?, ref?, subdir?)` *(gated)* | Shallow-clone into the workspace; only tool that runs with network |

State persists across tool calls *within a session* via files in the workspace.
There is **no** persistent Python REPL — every `python` call is a fresh
subprocess (state lives in files, not Python globals). Workspaces are wiped on
session close, idle TTL, explicit `reset_session()`, or pod shutdown.

## Sandbox layers

Per `python` call, the user code runs as a subprocess that, before `execvp`:

1. `unshare(CLONE_NEWUSER | CLONE_NEWNET)` — fresh user + network namespace
   (`lo` is down by default — no DNS, no routes, all IP traffic blackholed).
2. `setrlimit(RLIMIT_AS / CPU / FSIZE / NPROC)` — bound memory, CPU,
   per-file write size, fork bombs.
3. `prctl(PR_SET_NO_NEW_PRIVS, 1)` — prerequisite for unprivileged Landlock
   and good hygiene against setuid escape.
4. Landlock ruleset:
   * Full R/W under the session workspace.
   * R + W on `/dev` (so `/dev/null`, `/dev/urandom`, `/dev/tty` work).
   * R + X on `/usr /lib /lib64 /bin /sbin /etc /opt /proc /sys` plus the
     directory containing the resolved python binary (so the interpreter's
     stdlib is reachable).
5. `landlock_restrict_self`.
6. `os.execvp(python, ...)`.

Outside the child, an outer `subprocess.run(timeout=...)` enforces wall clock
and a workspace-size watcher rejects `write_file` / `upload_file` calls that
would push the workspace over the cap.

## Boot precondition

The server **refuses to start** unless `probe_kernel()` reports both Landlock
and unprivileged user/net namespace support. Override with
`CODE_EXECUTOR_V2_ALLOW_UNSAFE_NO_SANDBOX=1` for local development on a
restricted kernel — that mode skips kernel enforcement and is **not** safe for
production.

Notable hosts where the namespace probe fails:
- Ubuntu 24.04 with `kernel.apparmor_restrict_unprivileged_userns=1` (default).
- Hardened kernels with `kernel.unprivileged_userns_clone=0`.
- Kernels older than 5.13 (no Landlock).

The intended target — RHEL 9 / OpenShift — supports both.

## Configuration

All knobs are environment variables; defaults in [`config.py`](./config.py).

| Var | Default | Meaning |
|---|---|---|
| `MCP_CODE_EXECUTOR_V2_HOST` | `0.0.0.0` | Bind host |
| `MCP_CODE_EXECUTOR_V2_PORT` | `8011` | HTTP port |
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
| `CODE_EXECUTOR_V2_ENABLE_GIT_CLONE` | `0` | Enable the `git_clone` tool |
| `CODE_EXECUTOR_V2_ALLOW_UNSAFE_NO_SANDBOX` | `0` | Skip kernel preconditions (dev only) |
| `CODE_EXECUTOR_V2_REAPER_INTERVAL_S` | `300` | Reaper sweep interval |

## Build & run (container)

```bash
podman build -t atlas-code-executor-v2:dev -f Dockerfile .
podman run --rm -p 8011:8011 atlas-code-executor-v2:dev
```

Then register with Atlas via [`atlas/config/mcp-example-configs/mcp-code_executor_v2.json`](../../config/mcp-example-configs/mcp-code_executor_v2.json).

## Local development

```bash
pip install -e '.[dev]'
pytest                                           # unit tests
CODE_EXECUTOR_V2_ALLOW_UNSAFE_NO_SANDBOX=1 \
    CODE_EXECUTOR_V2_WORKSPACES_DIR=/tmp/ws \
    python main.py                               # smoke run on a kernel without netns
```

The 10 sandbox-enforcement tests in `tests/test_sandbox.py` are skipped
on hosts without full kernel support (they run in CI on RHEL9).
