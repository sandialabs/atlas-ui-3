# PytestUnraisableExceptionWarning investigation

Branch: `investigate/unraisable-py313-warning` — shipped as
[sandialabs/atlas-ui-3#603](https://github.com/sandialabs/atlas-ui-3/pull/603).

## Symptom
CI on Python 3.12.13 and 3.13.2 emits ~3–5
`PytestUnraisableExceptionWarning` lines that all trace back to
`BaseSubprocessTransport.__del__` ending in
`RuntimeError: Event loop is closed`. Tests still pass. Different runs
attribute the warning to different tests (the user-reported set spans
`test_agent_portal_groups`, `test_agent_portal_polish`,
`test_process_manager::test_get_missing_raises`,
`test_websocket_auth_header`, `test_rag_citations`,
`test_streaming_token_flow`) — i.e. the named test is whichever one
happened to be running when GC ran, not the one that leaked.

## Reproduction
Running the named tests in isolation never emits the warning. Running
the full suite under `PYTHONDEVMODE=1 PYTHONWARNINGS=always` on Python
3.13.12 surfaces 7 `ResourceWarning: unclosed transport
<_UnixSubprocessTransport pid=… running stdout=… stderr=…>` entries plus
a clutch of follow-on pipe / file warnings — this is the same leak the
CI run trips on, just before GC has had a chance to convert it to the
unraisable form.

## Root cause
`atlas.modules.process_manager.manager.ProcessManager.launch` spawns
each subprocess via `asyncio.create_subprocess_exec` and creates two
background tasks per process: `_pump_stream`/`_pump_pty` and
`_wait_and_finalize` (which awaits `proc.wait()` and pops the entry
from `_asyncio_procs` once the child exits). `cancel` additionally
schedules a fire-and-forget `_kill_if_still_alive` task.

Several tests — both bare `pm = ProcessManager()` cases and the HTTP
fixture in `test_agent_portal_polish.py` / `test_agent_portal_groups.py`
— end while a child is still running. pytest-asyncio's per-test event
loop is then torn down: pending tasks receive `CancelledError`,
`asyncio_proc.wait()` aborts, and the asyncio subprocess transport
never has `_process_exited` invoked. Result: the
`_UnixSubprocessTransport` is reachable (still referenced through the
about-to-be-dropped `ProcessManager`) but `_closed` is still `False`
and `_loop` points at the now-closed loop.

When a later test triggers a GC pass, `BaseSubprocessTransport.__del__`
fires:
* On Python 3.12 it unconditionally calls `proto.pipe.close()`, which
  schedules `loop.call_soon(self._call_connection_lost, exc)` on the
  closed loop → `RuntimeError: Event loop is closed` → pytest converts
  it to `PytestUnraisableExceptionWarning`. CPython
  [gh-114177](https://github.com/python/cpython/issues/114177) added a
  loop-is-closed guard to that branch in 3.13 which kills most of the
  3.13 occurrences, but residual code paths plus the fact that we
  leave subprocess pipe transports half-open still produces warnings
  in some 3.13 runs.

So this is *both* a Python-version interaction and a real cleanup bug
in our ProcessManager — the test-time leak is what gives `__del__`
anything to run against.

## Fix
`atlas/modules/process_manager/manager.py`: handle `CancelledError` in
the two long-lived tasks that wrap `asyncio_proc.wait()`
(`_wait_and_finalize` and the inner `_kill_if_still_alive` inside
`cancel`). When the loop is being torn down we now:
1. `asyncio_proc.kill()` (idempotent / tolerates `ProcessLookupError`),
2. call `asyncio_proc._transport.close()` while the loop is still
   alive — this flips `transport._closed` to `True` so the later
   `__del__` short-circuits before touching the closed loop,
3. pop the entry from `_asyncio_procs`,
4. re-raise `CancelledError` so the task ends cleanly.

This is intentionally surgical: no test code changes, no public-API
changes, no warning suppression — the underlying leak is what we
plug.

## Verification

Full suite, Python 3.13.12, `PYTHONDEVMODE=1 PYTHONWARNINGS=always`:

| Metric                                      | Before | After |
|---------------------------------------------|--------|-------|
| `ResourceWarning: ... _UnixSubprocessTransport` | 7      | **0** |
| Warnings reported by pytest                  | 33     | 2     |
| Tests                                        | 1469 passed, 17 skipped | 1469 passed, 17 skipped |

Full suite, Python 3.12.3, `PYTHONDEVMODE=1`:

| Metric                                                         | After |
|----------------------------------------------------------------|-------|
| `_UnixSubprocessTransport` leaks                              | **0** |
| `PytestUnraisableExceptionWarning` / "Event loop is closed"   | **0** |
| Tests                                                          | 1469 passed, 17 skipped |

The two residual warnings on 3.13 (and four on 3.12) are unrelated —
an OTEL JSONL exporter file kept open across the test session, a
`_UnixSelectorEventLoop` finalizer warning emitted by pytest-asyncio's
own loop teardown, and on 3.12 two `subprocess … is still running`
warnings from `subprocess.Popen.__del__` (not the asyncio transport
path that was the original symptom).

Spot-checking the five CI-named tests under 3.12 dev mode:
`test_group_max_panes_enforced_server_side`,
`test_http_pause_resume_records_audit`, `test_http_snapshot_endpoint`,
`test_process_manager::test_get_missing_raises`,
`test_websocket_uses_x_user_email_header` — all pass, zero warnings.

## Recommended next step
Land the manager.py change. Lower-priority follow-ups (out of scope
for this branch):
* The OTEL exporter file (`atlas/core/otel_config.py:271`) is never
  closed during pytest sessions — small leak that would be visible
  under `PYTHONDEVMODE=1` to anyone investigating.
* `cancel` is still fire-and-forget for the SIGKILL escalation; if a
  test wants deterministic teardown it currently has to poll status.
  Adding an `async def aclose(self)` to `ProcessManager` (drain
  `_asyncio_procs`, close transports, await waits) would let fixtures
  do `await pm.aclose()` instead of nulling singletons and hoping.
