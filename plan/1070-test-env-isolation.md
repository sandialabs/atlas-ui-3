# Plan: Isolate backend tests from exported shell environment variables (#1070)

Issue: https://github.com/sandialabs/atlas-ui-3/issues/1070
Branch: `fix/1070-test-env-isolation`

## Problem

`atlas/tests/conftest.py` isolates the suite from the developer's `.env`
(`AppSettings.model_config["env_file"] = None`) and from a handful of
specifically-pinned variables, but any settings-derived variable **exported in
the invoking shell** still reaches `AppSettings`, because pydantic-settings
reads the process environment for every field that is not passed explicitly.
A terminal that inherits a deployment's environment (tmux server started from a
deployment context, sourced env file, IDE terminal) therefore changes test
results with the code under test untouched.

## Evidence (reproduced on this branch, 2026-10-27)

```bash
export AUTH_STATIC_GROUPS="somegroup:user@example.com"
export ADMIN_USERS="deployadmin@example.com"
export ADMIN_GROUP="deploy_admins"
export APP_CONFIG_DIR="/tmp/deploy-config"
export ATLAS_ENV_FILE="/tmp/deploy.env"
bash test/atlas_tests.sh
```

The two tests the issue names fail:

```
FAILED tests/test_core_auth.py::test_admin_users_sugar_targets_the_configured_admin_group
FAILED tests/test_core_auth.py::test_warns_when_no_authorization_source_is_configured
```

(the exported `AUTH_STATIC_GROUPS`/`ADMIN_USERS` leak into the `AppSettings`
instances those tests construct). Full-suite baseline with the same exports is
captured in `/tmp/atlas_tests_exported_before.log` during development; the same
run without exports passes.

A second, related crash found while reproducing: an exported-but-empty
`DEBUG_MODE=` (empty string) fails **collection** outright with
`ValidationError: debug_mode / Input should be a valid boolean` — pydantic
cannot parse `""` as a bool.

## Chosen isolation point

`atlas/tests/conftest.py`, at import time, before any app module import and
before conftest's own forced pins. This is where every other session-level
isolation guard lives, it runs before test-module collection in every runner
(`test/atlas_tests.sh`, `test/run_tests.sh`, IDEs, pytest-xdist workers), and
the existing regression tests in `atlas/tests/test_env_isolation.py` lock
conftest guards in place.

## Fix

1. **Derived scrub (conftest).** Pop from `os.environ` every environment
   variable name that maps to an `AppSettings` field, derived from the model
   itself (`model_fields[*].validation_alias`, else upper-cased field name) so
   newly added settings cannot silently reopen the channel. Tests opt back in
   with `monkeypatch.setenv`, matching the existing conftest-guard model.
   Factor the scrub into a module-level helper so the guard is unit-testable.

1b. **Foreign-dotenv import-window guard (conftest).** Found while verifying in a
   clean worktree: `litellm/__init__.py` calls bare `load_dotenv()` at import,
   and python-dotenv's `find_dotenv` walks up parent directories — a worktree
   without its own `.env` picked up an ancestor directory's `.env`
   (containing a placeholder `CAPABILITY_TOKEN_SECRET`), which refilled scrubbed variables
   *after* the scrub and failed conftest import with a pydantic ValidationError
   (exit 4, zero tests collected). The first cut (blocking ALL dotenv loads
   during conftest's imports) broke the load-bearing in-checkout channel: CI
   bakes `/app/.env` and the app-import chain inside conftest reads values like
   `DEBUG_MODE` from it during those imports (AppSettings itself has env-file
   loading disabled), so a total block flipped `debug_mode` to its default and
   failed the mock-admin tests. The guard is therefore selective: a
   `load_dotenv` whose resolved path is outside the checkout under test is
   refused; the checkout's own `.env` passes through exactly as before. The
   real function is restored before the first test module imports. The main
   checkout masked the foreign-walk-up bug because `find_dotenv` found the
   checkout's own `.env` first; CI is safe for the same reason (`/app/.env`
   exists).

2. **Keep-list.** `DEBUG_MODE`, `ENVIRONMENT`, `MCP_CONFIG_FILE` are
   deliberately provided by the test harness and must survive:
   - `ci.yml` runs the debug leg with `docker run -e DEBUG_MODE=true` and the
     xdist leg with `-e DEBUG_MODE=false` ("pydantic BaseSettings reads the OS
     env var with higher priority than .env, so `docker run -e DEBUG_MODE=...`
     overrides it without rebuilding");
   - `ci.yml`'s xdist leg comment: `run_tests.sh` provides
     `MCP_CONFIG_FILE=mcp-test.json`, which "keeps a worker entering the shared
     app lifespan from discovering and starting every bundled MCP server";
   - `test/run_tests.sh` exports `ENVIRONMENT` and `MCP_CONFIG_FILE` for the
     whole `all`/`atlas` run.
   Scrubbing these would change what the harness is deliberately configuring,
   so they are excluded. An exported-but-**empty** value for a keep-listed var
   (e.g. `DEBUG_MODE=`) is popped: the harness never sets empty values and the
   empty string crashes `AppSettings` for bool fields.

3. **Extras.** `ATLAS_ENV_FILE` (read by `server_cli`/`init_cli`, not an
   `AppSettings` field) is popped outright. `PYTHONPATH` is filtered, not
   popped: entries that resolve inside the project root are kept absolute
   (this preserves `PYTHONPATH=$ROOT:$ROOT/scripts` per
   `docs/developer/test-isolation.md` and CI's reverse-order leg), foreign
   checkout entries are dropped so test-spawned subprocesses cannot resolve
   `import atlas` to another checkout (the suite installs the checkout
   editable, so nothing else needs PYTHONPATH).

4. **Regression tests** (`atlas/tests/test_env_isolation.py`):
   - in-process: the scrub helper removes planted deployment-style vars
     (including empty keep-list values) and keeps harness values;
   - in-process: the derived name set covers the variables issue #1070 names
     and the keep-list stays exactly the three harness vars;
   - end-to-end subprocess: run the two issue-named tests under
     `python -m pytest` with the deployment exports present in the child
     environment and assert they pass — the acceptance criterion, encoded as a
     test. (Without the conftest scrub this child fails, so the guard cannot be
     silently removed.)

5. **`test/atlas_tests.sh`:** warn when `PYTHONPATH` is exported, per the
   issue's suggestion, since the value is visible to tooling outside pytest.

6. **Docs:** add the guard to the table in
   `docs/developer/test-isolation.md`; changelog fragment `changes/1070.fix.md`.

## Non-goals / known limits

- No product/runtime behavior changes: the scrub lives only in the test
  session's process environment.
- The scrub runs at conftest import; `atlas.main` later calls
  `load_dotenv("../.env", override=False)`, which refills keys that are absent
  from `os.environ` from a developer's `.env`. That pre-existing channel is
  independent of the shell (same result with or without exports, so the
  acceptance criterion holds) and is intentionally left alone here.
- Vars not consumed via `AppSettings` and not named by the issue (e.g.
  `OPENAI_API_KEY`, `AWS_*`) are out of scope.

## Acceptance check

`bash test/atlas_tests.sh` must pass identically with and without
`AUTH_STATIC_GROUPS`, `ADMIN_USERS`, `ADMIN_GROUP`, `APP_CONFIG_DIR`,
`ATLAS_ENV_FILE` exported. Verify both directions before opening the PR.

## Verification-environment notes (recorded 2026-10-27)

- Verification runs in detached worktrees (`atlas-ui-3-wt-1070-verify`,
  `atlas-ui-3-wt-1070-base`) with their own venvs, because the shared checkout
  is being concurrently modified by another task's in-progress work (issue
  #992 files).
- First clean run in the worktree (no `.env`) failed collection at BASE:
  litellm's bare `load_dotenv()` reached the foreign
  `.env` of an ancestor directory outside the checkout (placeholder
  `CAPABILITY_TOKEN_SECRET`) via
  `find_dotenv`'s parent-directory walk — the ambient condition that
  motivated guard 1b. CI is unaffected because `Dockerfile-test` bakes
  `/app/.env`; the worktree gets a copy of the checkout's `.env` for parity.

## Verification results (final, 2026-10-27)

Identical conditions: detached worktree, own venv, checkout's own `.env`
present (as CI's baked `/app/.env`), issue exports = `AUTH_STATIC_GROUPS`,
`ADMIN_USERS`, `ADMIN_GROUP`, `APP_CONFIG_DIR`, `ATLAS_ENV_FILE`.

| Run | Result |
| --- | --- |
| Base `d4b29b31`, with exports | **29 failed, 4820 passed**, exit 1 |
| This branch, clean | **4856 passed, 79 skipped**, exit 0 |
| This branch, with exports | **4856 passed, 79 skipped**, exit 0 — identical to clean |
| This branch, CI xdist path (`run_tests.sh atlas -n 2`) with exports + foreign-checkout `PYTHONPATH` | backend suite **4856 passed** |
| Base, `e2e_tests_live.sh` (auto-run by `run_tests.sh` when a dev `.env` has an API key) | 3 passed, 2 failed |
| This branch, same live script, same env | 3 passed, 2 failed — identical (the live script honors its environment by design; CI never runs it, its container `.env` has no API key) |

Base collected 4849 tests; this branch adds 7 regression tests → 4856, and
every one of base's 29 exported-env failures now passes.

## Review response (2026-10-28)

The #1132 review flagged two residual channels; both are closed here.

1. **Case-insensitive scrub bypass (P2).** pydantic-settings matches
   environment keys case-insensitively — verified live: an exported
   `auth_static_groups` was read exactly as `AUTH_STATIC_GROUPS`, and a
   lowercase `debug_mode=not-a-bool` failed `AppSettings()` like the
   upper-case form does — but the scrub popped only exact upper-case names.
   `_scrub_session_environment` now matches `os.environ` keys
   case-insensitively. The harness keep-list stays exact-case: the harness
   provides `DEBUG_MODE`/`ENVIRONMENT`/`MCP_CONFIG_FILE` spelled exactly as
   listed, so case variants of those names are foreign exports and are
   dropped, as are exported-but-empty values in any case. New unit test
   (`test_scrub_removes_case_variant_exports`) plants lower/mixed-case
   exports; the end-to-end subprocess test now carries its exports as case
   variants. Both fail when the scrub is reverted to exact-case matching
   (checked by stashing the conftest change).

2. **`PYTHONPATH` scrub happens after interpreter startup (P2).**
   `sys.path` is built from `PYTHONPATH` before conftest runs, so the
   session filter protects only test-spawned subprocesses, not the pytest
   process itself. `test/atlas_tests.sh` now applies the same
   checkout-local filter before Python starts (verified: foreign entries
   dropped, checkout root/`scripts`/`atlas` entries kept, unset stays
   unset), and `docs/developer/test-isolation.md` states the limitation for
   a bare `python -m pytest` run. Conftest's filter is unchanged and still
   authoritative for subprocesses.

Local verification of this response: `test_env_isolation.py` 16 passed,
`test_core_auth.py` 38 passed, config/compliance slice 123 passed — all
with lower/mixed-case deployment exports planted in the invoking shell;
ruff clean on both changed test files.
