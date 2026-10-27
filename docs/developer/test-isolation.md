# Test Isolation

Last updated: 2026-10-27

The Python suite runs in a single process, in one pass, with no per-test
forking, and is also supported under `pytest-xdist` for parallel execution
(see [Running in parallel](#running-in-parallel-pytest-xdist)).
Collection order is not a contract pytest makes -- today it happens to
be alphabetical, which is exactly why a leak can hide behind it. Everything a
test leaves behind -- an env var, a module global, a row in a database -- is
visible to every test that runs after it, and to the next run on the same
machine. This page states what `atlas/tests/conftest.py` guarantees, and what a
test author still has to do.

## What conftest guarantees

`atlas/tests/conftest.py` runs before any test module is imported, so its
guards are in place even for state that app code establishes at *import* time.

| Guard | What it prevents |
| --- | --- |
| `AppSettings.model_config["env_file"] = None` | The developer's `.env` changing results from machine to machine |
| Settings-derived env vars scrubbed (`_scrub_session_environment`) | Deployment-style variables exported in the invoking shell (`AUTH_STATIC_GROUPS`, `ADMIN_USERS`, `ADMIN_GROUP`, `APP_CONFIG_DIR`, `OIDC_*`, `FEATURE_*`, ...) changing test results (issue #1070) |
| `APP_LOG_DIR` -> temp dir | Test spans and security-risk records landing in the repository's `logs/` |
| `CHAT_HISTORY_DB_URL`, `RUNTIME_FEEDBACK_DIR`, `RUNTIME_CAPTURE_DIR`, `MCP_TOKEN_STORAGE_DIR` -> temp dirs | Tests reading and writing the developer's real `data/`, `runtime/` and `config/secure/` state |
| `AUTH_GROUP_CHECK_URL` / `AUTH_GROUP_CHECK_API_KEY` cleared | Authorization tests calling a live external authorizer |
| `FEATURE_PROXY_SECRET_ENABLED=false` | An exported/`.env` proxy secret turning every `TestClient(app)` request into a 401/503 |
| `_isolate_config_cache` (autouse) | A test's env changes surviving in the `ConfigManager` singleton's lazily-built config cache |
| `_isolate_module_singletons` (autouse) | A pinned or lazily-created app singleton (hook manager, chat-history engine, ...) surviving into later tests |

The store redirects are deliberately unconditional assignments rather than
`setdefault`: an exported shell value must not be able to point the suite at a
real database.

**The redirects are per session, not per test.** One temp directory backs the
whole run, so a test that asserts "no conversations exist" or counts rows still
sees whatever earlier tests in the same session wrote. The redirects stop the
suite from touching real developer state and from carrying data across *runs*;
they do not make each test's store empty. A test that needs a store of its own
should build one against `tmp_path` and pin it for the duration, or reset the
relevant singleton (see `reset_engine` in
`atlas/modules/chat_history/database.py`).

### Shell-environment scrub

The `.env` guard only stops the *file*; pydantic-settings also reads the
process environment, so a terminal that inherited a deployment's environment
(a tmux server launched from a deployment context, a sourced env file) would
otherwise change results with the code under test untouched. At import time
conftest pops every variable that maps to an `AppSettings` field, derived from
the model itself so newly added settings cannot silently reopen the channel
(issue #1070). Three variables are keep-listed because the test harness
provides them deliberately: `DEBUG_MODE` (CI's per-leg `docker run -e
DEBUG_MODE=...`), `ENVIRONMENT`, and `MCP_CONFIG_FILE` (`run_tests.sh` points
it at an absent `mcp-test.json` so the shared app lifespan does not start
every bundled MCP server). `ATLAS_ENV_FILE` is popped as well, and
`PYTHONPATH` is filtered to entries inside the checkout so test-spawned
subprocesses cannot resolve `import atlas` to another checkout. Tests opt
back in per test via `monkeypatch.setenv`, as with every other session-level
pin.

**The scrub is per session, not per test.** Like the `.env` guard, it runs
once when conftest is imported; it cannot undo variables a test sets, and
nothing a test does re-triggers it.

## Rules for test authors

- **Mutate the environment only through `monkeypatch`.** `os.environ[...] = ...`
  at module scope runs during collection and is never undone; a manual
  `del os.environ[...]` at the end of a test body does not run when an
  assertion above it fails.
- **Never replace an entry in `sys.modules`.** Import-time module swapping binds
  the fake into every module imported afterwards, for the rest of the session,
  and no fixture can reliably undo it. Patch the attribute instead
  (`monkeypatch.setattr(some_module, "SomeClass", FakeClass)`).
- **Reset a singleton by its real name.** If you pin a module global, assign the
  attribute that actually exists -- a typo'd name silently creates a new
  attribute and isolates nothing. `_isolate_module_singletons` restores the
  globals it knows about; add new ones to `_SINGLETON_GLOBALS` when app code
  grows another one.
- **Write only under `tmp_path`.** If the code under test resolves a path
  relative to the project root, give it an override (env var or injected
  config) rather than letting the test write into the checkout.
- **Start mocks in a fixture or a `with` block**, not with a bare
  `patcher.start()` before a `try`. A failure between `start()` and the
  caller's `finally` leaves a global patch active for the whole session.
- **Parametrize from ordered collections.** A `set`/`frozenset` parametrization
  iterates differently in every process, which breaks deterministic collection
  under `pytest-xdist`. Sort it first -- see
  [Running in parallel](#running-in-parallel-pytest-xdist).
- **Prefer a dedicated app over mutating the shared one.** Edits to
  `app.user_middleware`/`app.middleware_stack` leak process-wide; build a small
  `FastAPI()` for the test instead (as `test_middleware_auth.py` does).

## Checking for order dependence

`scripts/pytest_test_order.py` reorders collected tests so a leak that the
alphabetical order hides becomes a failure. It is inert unless
`ATLAS_TEST_ORDER` is set, so passing `-p` to a normal run changes nothing.

```bash
cd atlas
ROOT=$(git rev-parse --show-toplevel)
export PYTHONPATH="$ROOT:$ROOT/scripts"

python -m pytest tests -q                                            # normal order
ATLAS_TEST_ORDER=reverse python -m pytest tests -q -p pytest_test_order
ATLAS_TEST_ORDER=1 python -m pytest tests -q -p pytest_test_order    # shuffle files
ATLAS_TEST_ORDER=1 ATLAS_TEST_ORDER_SCOPE=test \
    python -m pytest tests -q -p pytest_test_order                   # shuffle tests
python -m pytest tests/<one_file>.py -q                              # file in isolation
```

A failure that appears only under reordering is a leak, not a flake: find the
state the earlier test left behind rather than adding a retry.

Reordering *collected items* does not reorder *module imports* -- those follow
collection order. Import-time side effects therefore need a pairwise run
instead: the suspect file first, then the file you think it affects.

```bash
python -m pytest tests/<suspect>.py tests/<victim>.py -q
```

## Running in parallel (pytest-xdist)

`pytest-xdist` is in the `dev` extra, so `uv pip install -e ".[dev]"` (or
`".[dev,mcp-demos]"`) provides it. Run the backend suite with two or more
workers for a much faster wall-clock time. Set the same environment the CI run
uses so a local command behaves like CI:

```bash
# one-time, from the repo root
uv venv && source .venv/bin/activate && uv pip install -e ".[dev,mcp-demos]"

cd atlas
export MCP_CONFIG_FILE=mcp-test.json      # intentionally absent: no bundled MCP servers
export PYTHONPATH="$PWD/..:$PWD/../scripts"
python -m pytest tests -q -n 2           # representative run (CI uses this)
python -m pytest tests -q -n auto        # one worker per CPU
```

CI runs the backend suite at `-n 2` with `DEBUG_MODE=false`. Running the
parallel command in debug mode (`DEBUG_MODE=true`) is fine, but debug mode
skips `AuthMiddleware`'s proxy-secret check, so it is a weaker guard against a
shared-app middleware regression. To run through the same wrapper CI uses,
return to the repo root (the block above ends in `atlas/`) and pass the pytest
options via `PYTEST_ARGS`, which `test/atlas_tests.sh` forwards to pytest:

```bash
cd ..                                    # back to the repo root
PYTEST_ARGS="-n 2 -p no:cacheprovider" bash test/run_tests.sh atlas
```

The wrapper sets `MCP_CONFIG_FILE`, `PROJECT_ROOT`, and its own timeout, so the
invocation matches CI's production-mode pass.

### Why collection must be deterministic

xdist requires every worker to collect the *same* sequence of test IDs. Every
worker is a separate process with its own hash randomization, so a
parametrization sourced from an unordered container changes order from worker
to worker and xdist aborts with "Different tests were collected between gw0
and gw1". Parametrize from an explicitly ordered list/tuple, and sort anything
that arrives as a `set`/`frozenset` at the point of parametrization:

```python
# Non-deterministic across worker processes
@pytest.mark.parametrize("secret", UNSAFE_SECRETS)          # a set

# Deterministic
@pytest.mark.parametrize("secret", sorted(UNSAFE_SECRETS))
```

Do **not** mask this by pinning `PYTHONHASHSEED`: the suite must collect
identically regardless of seed. To re-check, compare collection output under
different seeds -- the node-ID list must be byte-identical:

```bash
for seed in 0 1 42; do
    PYTHONHASHSEED=$seed python -m pytest tests --collect-only -q | grep '::' | md5sum
done
```

### Parallel-execution rules

- **Never mutate the shared `app`'s middleware declaratively.** Starlette
  caches `app.middleware_stack` on the first request. Changing
  `app.user_middleware[i].kwargs` bakes the change into the process-wide app for
  every later test (and, under xdist, every other test on that worker) -- and
  because the stack is captured on first use, a "build it first" workaround only
  hides the edit until something rebuilds the stack. Build a dedicated app for
  the test instead; `test_middleware_auth.py` is the model. The suite no longer
  mutates the shared app (issue #1057), and the autouse
  `_guard_shared_app_middleware` fixture in `conftest.py` fails any test that
  reintroduces it.
- **Give each test its own resources.** Ports, temp files, and databases must
  be per-test (`tmp_path`) or worker-unique. The session-scoped temp redirects
  in `conftest.py` are *worker-local*, not shared across workers: every worker
  imports `conftest.py` and runs `tempfile.mkdtemp()` itself, so each worker
  gets its own directories. They are shared only among the tests that run
  inside a single worker, which is where cross-test leakage can hide.
- **Run resource-heavy or subprocess-heavy tests serialized if they need it.**
  Preference order: (1) fix the isolation so the test is parallel-safe;
  (2) group the tests that share a resource with
  `@pytest.mark.xdist_group("name")` so xdist keeps them on one worker -- the
  marker is only honored by the `loadgroup` scheduler, so run the grouped set
  with `--dist loadgroup` (`python -m pytest tests --dist loadgroup -n auto`);
  the default `load` scheduler ignores the marker and may still run the group
  concurrently; (3) keep a whole file on one worker with `--dist loadfile`.
  Never disable xdist globally (`-p no:xdist`) to work around one test.

### Files that need special care

There is currently no test that must be forced serial; the full suite passes
at `-n 2` and `-n auto`. What makes that safe is the session-level isolation in
`conftest.py` (env pins, config/singleton snapshots) plus the rule that tests
build their own app rather than editing the shared one. The files most likely
to regress are those that touch the shared `app` middleware, module singletons,
or the worker-local temp stores. CI runs the backend suite at `-n 2` on every
PR so such a regression fails there rather than only on a developer's machine.

