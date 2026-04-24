# Agent Portal design considerations

Notes on implementation choices that are easy to miss reading the code,
and the graduation checklist that must be worked through before this
feature can run outside dev-preview.

## Landlock via wrapper script, not preexec_fn

The sandbox is applied by executing the child through a small Python
wrapper that installs Landlock rules and then `os.execvp`s the target
command. This is instead of the more typical `preexec_fn` hook on
`subprocess.Popen` or `asyncio.create_subprocess_exec`.

Reason: the event loop here is `uvloop`, and `uvloop`'s subprocess
implementation does not honor `preexec_fn`. Running the rule-install
code in-process before `execvp` was the only reliable way to get it to
execute in the forked child before the target binary took over. A side
benefit is that the rule set is expressed in normal Python, which keeps
the path logic easy to read.

## Environment isolation (implemented)

Launched processes no longer inherit `os.environ.copy()`. The spawn
path in `atlas/modules/process_manager/manager.py` builds the child
env via `_build_child_env()`:

- allow-list of benign keys copied from the parent
  (`HOME`, `USER`, `LOGNAME`, `LANG`, `TERM`, `TZ`, `TMPDIR`,
  plus every `LC_*` locale variable);
- `PATH` pinned to `/usr/local/bin:/usr/bin:/bin` so the backend's
  venv / project dirs do not leak;
- caller-supplied `extra` dict merged in (wired through
  `ProcessManager.launch(env=...)`, not yet exposed on the request
  schema);
- secret-shaped deny-list applied last, covering provider keys
  (`*_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`/`_PASSWD`), cloud
  creds (`AWS_*`, `GCP_*`, `GOOGLE_APPLICATION_CREDENTIALS`), Atlas
  config (`ATLAS_*`), ANTHROPIC/OPENAI/CONDA, and loader-level
  dangerous vars (`LD_PRELOAD`, `LD_LIBRARY_PATH`, `PYTHONPATH`,
  `VIRTUAL_ENV`, `NODE_PATH`). Denied keys are logged at INFO.

Because `_sandbox_launch.py` exec'vp's the target command with
whatever env it itself inherited from the backend, fixing the
single call site in `manager.py` is sufficient for both sandboxed
and plain launches.

## cwd and extra_writable_paths accepted verbatim

The launch endpoint accepts an absolute `cwd` and a list of
`extra_writable_paths`, and passes them through to the process manager.
There is no validation that these fall under a configured workspace
root. On a dev box this is the whole point — the developer wants to run
a tool in their own project directory.

For any non-dev deployment this must be constrained. The expected
shape is: administrator configures a workspace root (e.g.
`/srv/atlas/workspaces/$USER`), launch requests are rejected unless
`cwd` and every entry in `extra_writable_paths` resolve (after
symlink resolution) under that root. Without this, a user on a
multi-tenant deploy can launch a process rooted at another user's home
directory.

## Graduation checklist

Items that must be addressed before the feature can come out of
dev-preview and be turned on in any deployment other than a single
developer's own machine:

- **Path-root validation.** Require that `cwd` and every entry of
  `extra_writable_paths` resolve under a configured per-user workspace
  root. Reject absolute paths that escape via `..` or symlinks.
- **Per-user ownership checks.** Each of `GET`, `DELETE`, `PATCH`, and
  the WS stream on `/api/agent-portal/processes/{id}` must verify that
  the caller is the same user that launched the process. Current code
  has `TODO(graduation)` markers on each. Cross-references
  [threat-model.md](./threat-model.md).
- **Per-user quotas.** Cap the number of concurrent processes per
  user, and the cumulative CPU/memory allocated to them, so one user
  cannot starve the host.
- **Audit log.** Every launch/cancel/rename must produce a structured
  log event with user, command, args (redacted if needed), cwd,
  sandbox mode, and outcome. The log must be kept separately from
  normal application logs and be suitable for compliance review.
- **Real authorization.** A role or capability check in front of
  launch — not just "any authenticated user." The simplest viable
  version is an allow-list of emails in config; the next step up is
  integrating with the existing group / authorization manager.
- **Explicit CSRF.** The current REST-route protection is implicit
  (SOP + no CORS + JSON content type). A non-dev deployment should
  add a proper double-submit or header-based CSRF check rather than
  relying on browser behavior.

Only after every item above is done should `FEATURE_AGENT_PORTAL_
ENABLED` be allowed outside `DEBUG_MODE`.
