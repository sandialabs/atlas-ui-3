# Agent Portal docs

**This feature is a dev-only preview. Read [threat-model.md](./threat-model.md) before enabling it anywhere.**

The Agent Portal lets an authenticated user launch host subprocesses from the
UI, stream their output over a WebSocket, and apply optional isolation
(Landlock, Linux namespaces, cgroup limits).

The Agent Portal requires a Unix host. On Windows, Atlas treats the feature as
disabled even when `FEATURE_AGENT_PORTAL_ENABLED=true`, because the process
manager imports Unix-only modules (`fcntl`, `pty`, `termios`) that are absent on
Windows and would otherwise break startup. The feature runs on Linux/WSL and
macOS, but its isolation (Landlock, Linux namespaces, cgroup limits) is
Linux-only and degrades gracefully where the kernel does not provide it (for
example, on macOS the sandbox capabilities simply report as unsupported).

Because any authenticated user can run any command the backend process can
run, the feature is gated behind `FEATURE_AGENT_PORTAL_ENABLED` and a startup
guard that refuses to let it run outside debug mode.

## Contents

- [threat-model.md](./threat-model.md) — what we defend against, what we do
  not, and how the current mitigations are arranged.
- [design-considerations.md](./design-considerations.md) — implementation
  choices worth knowing about, plus the graduation checklist that must be
  worked through before the feature can come out of dev-preview.
- [presets.md](./presets.md) — the server-side preset library (CRUD
  endpoints, storage layout, migration from legacy localStorage).
- [cli.md](./cli.md) — the `atlas-portal` CLI for launching /
  inspecting / cancelling processes and managing presets from the
  terminal. Useful for debugging and for the e2e test suite.
