"""Tests for the isolation machinery in ``tests/conftest.py``.

The guards in conftest are the only thing keeping the suite order-independent
and off the developer's real state, so they need coverage of their own -- a
silently broken guard looks exactly like a suite that never needed one. Two of
the behaviors here are regressions that already happened once:

* ``_release`` dispatched by method name, so ``ProcessManager.cancel`` (which
  is ``async def cancel(self, process_id, *, sigkill_after=3.0)``) raised
  TypeError into a bare ``except`` and the singleton owning live subprocesses
  was never released.
* the snapshot skipped modules that were not imported yet, so a singleton
  created by an import *inside* a test escaped isolation entirely.
"""

import asyncio
import sys
import types

import pytest
from conftest import (  # the suite's own conftest
    _release,
    restore_singletons,
    snapshot_singletons,
)
from sqlalchemy import create_engine


class TestRelease:
    """``_release`` must free resources without ever calling a domain method."""

    def test_disposes_a_sqlalchemy_engine(self, tmp_path):
        engine = create_engine(f"duckdb:///{tmp_path / 'release.db'}")
        with engine.connect() as conn:
            assert conn is not None
        disposed = []
        engine.dispose = lambda *a, **k: disposed.append(True)

        _release(engine)

        assert disposed == [True]

    def test_cancels_a_pending_task(self):
        async def run():
            task = asyncio.ensure_future(asyncio.sleep(30))
            await asyncio.sleep(0)
            _release(task)
            assert task.cancelled() or task.cancelling()
            with pytest.raises(asyncio.CancelledError):
                # Bound rather than a bare ``await task`` statement, which
                # CodeQL reads as "statement has no effect".
                _ = await task

        asyncio.run(run())

    def test_leaves_a_finished_task_alone(self):
        async def run():
            task = asyncio.ensure_future(asyncio.sleep(0))
            assert await task is None  # drives the task to done
            _release(task)
            assert not task.cancelled()

        asyncio.run(run())

    def test_does_not_call_a_domain_cancel(self):
        """The ProcessManager regression: ``cancel`` here takes an argument.

        Dispatching by method name called it with none, raised TypeError, and
        the surrounding ``except`` hid the failure.
        """
        calls = []

        class FakeProcessManager:
            async def cancel(self, process_id, *, sigkill_after=3.0):
                calls.append(process_id)

            def dispose(self):  # a same-named method must not be enough either
                calls.append("dispose")

        _release(FakeProcessManager())

        assert calls == []

    def test_survives_a_raising_release(self):
        class Boom:
            def dispose(self):
                raise RuntimeError("nope")

        engine = create_engine("duckdb:///:memory:")
        engine.dispose = Boom().dispose

        _release(engine)  # must not raise; teardown cannot fail the suite


class TestSingletonSnapshot:
    """Snapshot/restore must also cover modules imported during a test."""

    def _fake_module(self, name):
        module = types.ModuleType(name)
        module._singleton = None
        return module

    def test_restores_a_value_the_test_replaced(self, monkeypatch):
        from atlas.modules.agent_portal import presets_store as ps_mod

        original = ps_mod._singleton
        saved = snapshot_singletons()
        ps_mod._singleton = "replaced-by-test"

        restore_singletons(saved)

        assert ps_mod._singleton is original

    def test_clears_a_singleton_created_by_an_import_inside_the_test(self):
        """A module absent at snapshot time must come back as ``None``.

        Simulated by removing a real entry's module from ``sys.modules`` before
        snapshotting, then putting a populated stand-in back -- the shape of a
        subset run where the first import happens inside the test.
        """
        name = "atlas.modules.agent_portal.presets_store"
        real = sys.modules[name]
        del sys.modules[name]
        try:
            saved = snapshot_singletons()

            stand_in = self._fake_module(name)
            stand_in._singleton = "created-during-test"
            sys.modules[name] = stand_in

            restore_singletons(saved)

            assert stand_in._singleton is None, (
                "a singleton created by an import inside the test must not "
                "survive into the next test"
            )
        finally:
            sys.modules[name] = real

    def test_snapshot_skips_attributes_a_module_does_not_have(self):
        """An entry naming a missing attribute must not invent one.

        ``_SINGLETON_GLOBALS`` is checked against reality by
        ``test_env_isolation.test_isolated_singletons_name_real_module_globals``;
        this covers the restore side not creating the attribute either.
        """
        name = "atlas.modules.agent_portal.presets_store"
        module = sys.modules[name]
        assert not hasattr(module, "_no_such_global")

        try:
            restore_singletons([(name, "_no_such_global", None)])

            assert not hasattr(module, "_no_such_global"), (
                "restore must not create a global the module never declared"
            )
        finally:
            # Only reached if the assertion above failed, but the cleanup has
            # to run regardless or the stray attribute outlives this test.
            if hasattr(module, "_no_such_global"):
                delattr(module, "_no_such_global")

