# `atlas-portal` CLI

The `atlas-portal` entry point is a dep-free (stdlib `urllib`) REST
client for the Agent Portal. It exists so a developer can launch,
list, cancel, inspect processes and manage presets without the
browser — useful for debugging launch failures, and as the driver
for the end-to-end integration test suite.

## Installation

Registered as a `[project.scripts]` entry in `pyproject.toml`, so it
is available on the PATH after a local editable install:

```
uv pip install -e .
atlas-portal --help
```

Or invoke it as a module without installing:

```
PYTHONPATH=. python -m atlas.portal_cli --help
```

## Authentication

In dev mode the backend's auth middleware accepts an `X-User-Email`
header (or whatever the server is configured to use) and falls back
to the configured test user when the header is absent. Override with
`--user` or `ATLAS_USER`:

```
ATLAS_USER=me@example.com atlas-portal list
```

The server URL is read from `--url` or `ATLAS_URL`, defaulting to
`http://localhost:8000`.

## Subcommands

| Command | Purpose |
|---|---|
| `launch CMD [...]` | Launch a new process. Flags belong **before** the command; args go after a literal `--`. |
| `list`             | List the current user's processes. |
| `get ID`           | Fetch one process's summary as JSON. |
| `cancel ID`        | DELETE the process (SIGTERM then SIGKILL). |
| `stream ID`        | Poll until the process exits (REST polling; for live stdout/stderr use the browser UI). |
| `capabilities`     | Show host isolation capabilities (Landlock, namespaces, cgroups). |
| `presets list`     | List saved presets. |
| `presets show ID`  | Show one preset. |
| `presets delete ID`| Delete a preset. |

## Examples

```
# Launch a short job
atlas-portal launch sh -- -c "echo hello"

# Launch with a working directory and the workspace-write sandbox
atlas-portal launch --cwd /home/me/project --sandbox workspace-write \
    claude -- --help

# Launch with resource limits and wait for exit
atlas-portal launch --memory-limit 512M --cpu-limit 50% \
    --pids-limit 200 --stream sh -- -c "sleep 2; echo done"

# Isolate network too
atlas-portal launch --namespaces --isolate-network \
    sh -- -c "curl https://example.com"

# Inspect and cancel
atlas-portal list
atlas-portal cancel abc123...

# Preset housekeeping
atlas-portal presets list
atlas-portal presets delete pst_...
```

## Design notes

- **Why a separate CLI?** `atlas_chat_cli.py` is LLM-focused and its
  flag surface is already crowded. A dedicated `atlas-portal` entry
  point keeps the two concerns separate.
- **Why stdlib `urllib`?** The CLI is dev-only and we wanted to
  avoid pulling `httpx` or `requests` into the base install just for
  this.
- **Why no WebSocket streaming?** `urllib` does not do WebSockets, and
  adding a dep for a dev CLI is not worth it. For richer streaming
  use the browser UI or the `/processes/{id}/stream` WS directly.
- **Why `argparse.REMAINDER` for command args?** So that `atlas-portal
  launch claude -- --help` passes `--help` through to `claude`
  without argparse intercepting it. Put portal flags before the
  command, use `--` before command args.
