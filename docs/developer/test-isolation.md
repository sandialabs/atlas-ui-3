# Test Isolation

Last updated: 2026-08-18

The Python suite runs in a single process, in one pass, with no per-test
forking. Collection order is not a contract pytest makes -- today it happens to
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
| `APP_LOG_DIR` -> temp dir | Test spans and security-risk records landing in the repository's `logs/` |
| `CHAT_HISTORY_DB_URL`, `AGENT_PORTAL_DB_URL`, `AGENT_PORTAL_AUDIT_PATH`, `RUNTIME_FEEDBACK_DIR`, `RUNTIME_CAPTURE_DIR`, `MCP_TOKEN_STORAGE_DIR` -> temp dirs | Tests reading and writing the developer's real `data/`, `runtime/` and `config/secure/` state |
| `AUTH_GROUP_CHECK_URL` / `AUTH_GROUP_CHECK_API_KEY` cleared | Authorization tests calling a live external authorizer |
| `_isolate_config_cache` (autouse) | A test's env changes surviving in the `ConfigManager` singleton's lazily-built config cache |
| `_isolate_module_singletons` (autouse) | A pinned or lazily-created app singleton (process manager, portal store, hook manager, chat-history engine, ...) surviving into later tests |

The store redirects are deliberately unconditional assignments rather than
`setdefault`: an exported shell value must not be able to point the suite at a
real database.

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
