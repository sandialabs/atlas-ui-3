# Agent Portal threat model

## Intent

The Agent Portal is a developer-facing preview feature. The expected
deployment is a single developer running Atlas on their own machine,
bound to loopback, with `DEBUG_MODE=true`. Any authenticated user who
reaches the API can launch any command the backend process itself can
run.

Because the blast radius of a successful exploit is "arbitrary command
execution on the dev box," the feature intentionally does not attempt to
look production-grade. Instead, it sets a hard gate so it cannot be
turned on outside that narrow context.

## Out of scope

The Agent Portal does **not** attempt to defend against:

- A malicious OS user on the dev box. A local user with shell access can
  already run anything the backend can; the portal is not a new
  escalation vector.
- A fully compromised dev machine. If the box is owned, so is the
  portal.
- Deployment to a multi-user or production environment. The startup
  guard refuses to boot in that case rather than trying to harden its
  way through.
- Cross-user IDOR on per-process endpoints. See
  [design-considerations.md](./design-considerations.md) graduation
  checklist — in a single-developer dev-only context every authenticated
  caller is the same person, so per-object ownership checks are deferred
  until the feature graduates.

## In scope

The feature **does** defend against:

- **Drive-by CSRF from an untrusted browser tab.** A developer visits a
  page in another tab that runs JavaScript pointed at
  `http://localhost:<port>/api/agent-portal/*`. The tab tries to launch
  a process or open a stream without the developer noticing.
- **Accidental enablement in a non-dev environment.** Someone flips
  `FEATURE_AGENT_PORTAL_ENABLED=true` in a staging or production config
  and ships it. The startup guard catches this and refuses to boot.

## Mitigations

### Startup guard (primary)

At app startup, if `FEATURE_AGENT_PORTAL_ENABLED=true` and
`DEBUG_MODE=false`, the server logs a loud error and raises, refusing
to start. This makes "accidentally on in prod" a crash rather than a
silent foot-gun. See `atlas/modules/config/config_manager.py`
(validator on `AppSettings`).

### Implicit CSRF protection for REST routes

There is no dedicated CSRF middleware in the app. Protection on the
JSON REST surface is implicit and relies on three things:

- `/api/agent-portal/*` expects `Content-Type: application/json`, so a
  cross-origin `fetch` triggers a CORS preflight.
- The app does not configure a CORS middleware, so no `Access-Control-
  Allow-Origin` response header is emitted and the preflight fails in
  the browser.
- Authentication flows through a proxy-injected header
  (`X-User-Email`) in non-debug deployments, or through the test user
  / query parameter in debug mode — neither is forgeable by a
  cross-origin page because the browser will not attach custom headers
  without a successful preflight.

This is enough for the REST endpoints **given that the feature is
debug-only**, because the page the attacker controls cannot make a
preflighted JSON POST that the browser will allow.

The proxy-secret header check in `AuthMiddleware` is the main defense
in production, but it is disabled in debug mode — which is exactly the
mode the portal runs in. So in practice, the only thing standing
between an attacker tab and the portal's REST endpoints is the SOP /
preflight behavior above. That is acceptable for dev-only, but is the
reason this doc flags CSRF as an assumed-but-not-independently-
enforced mitigation.

### Environment isolation for child processes

Launched children no longer inherit the backend's full
`os.environ`. `atlas/modules/process_manager/manager.py` builds the
child env from a small allow-list (`HOME`, `USER`, `LOGNAME`,
`LANG`, `TERM`, `TZ`, `TMPDIR`, `LC_*`), pins `PATH` to
`/usr/local/bin:/usr/bin:/bin`, merges any caller-supplied extras,
and then strips any key matching the secret-shaped deny-list
(`*_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`/`_PASSWD`, `AWS_*`,
`GCP_*`, `ATLAS_*`, `ANTHROPIC_*`, `OPENAI_*`, `CONDA_*`,
`GOOGLE_APPLICATION_CREDENTIALS`, `LD_PRELOAD`, `LD_LIBRARY_PATH`,
`PYTHONPATH`, `VIRTUAL_ENV`, `NODE_PATH`). Dropped keys are logged.

This keeps provider API keys, database URLs, cloud credentials,
and other backend config out of user-launched processes. It does
not stop a user from reading those secrets from disk if the
filesystem sandbox is off — env isolation is one layer, not a
full sandbox.

### WebSocket Origin check (explicit)

WebSocket upgrades bypass the CORS preflight model entirely — the
browser will happily open a cross-origin WS and the server sees the
request. CSRF-style tokens do not apply because there is no request
body to attach them to.

The stream endpoint
(`/api/agent-portal/processes/{id}/stream`) therefore performs an
explicit `Origin` header check before `websocket.accept()`. Only
loopback origins (`http://localhost`, `http://127.0.0.1`,
`http://[::1]`, any port) are allowed; anything else is rejected with
close code `4403`. See
`atlas/routes/agent_portal_routes.py::stream_process_output`.

## Deferred items

These are known gaps. They are intentional for the dev-preview scope,
and each is tracked with a `TODO(graduation)` comment in code that
points back here.

- **Per-user ownership checks on per-process endpoints.** `GET`,
  `DELETE`, `PATCH`, and the WS stream on
  `/api/agent-portal/processes/{id}` currently accept any authenticated
  caller. In a multi-user deployment, user A could enumerate, cancel,
  or rename user B's processes. Not material while the feature is
  single-user dev-only. Must be implemented before graduation.
- **Path-root validation for `cwd` and `extra_writable_paths`.** These
  are currently taken as arbitrary absolute paths. See
  design-considerations.
