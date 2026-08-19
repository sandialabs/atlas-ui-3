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
from pathlib import Path

import pytest
from conftest import (  # the suite's own conftest
    _release,
    restore_singletons,
    snapshot_singletons,
)
from sqlalchemy import create_engine


async def _pending_task():
    """Start a long task on the *running* loop and hand it back."""
    return asyncio.ensure_future(asyncio.sleep(30))


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

    def test_survives_a_task_whose_loop_is_closed(self):
        """The common case at teardown: asyncio.run() has closed the loop.

        ``cancel()`` then raises "Event loop is closed" and the task stays
        PENDING -- teardown must swallow that rather than fail the test, and the
        comment in ``_release`` must not claim the task ends up cancelled.
        """
        loop = asyncio.new_event_loop()
        task = loop.run_until_complete(_pending_task())
        loop.close()
        # Nothing will ever run this task again; keep asyncio's destructor from
        # printing "Task was destroyed but it is pending!" into suite output.
        task._log_destroy_pending = False
        assert task.get_loop() is loop and not task.done()

        # Pin the behavior the comment describes, rather than asserting
        # something that would hold either way: cancelling really does raise
        # here, and the task really is left PENDING.
        with pytest.raises(RuntimeError, match="Event loop is closed"):
            task.cancel()

        # A *second* task, untouched: the first cancel() sets _must_cancel, so
        # cancelling that one again returns quietly and would not exercise
        # _release's swallow at all -- deleting the `except` body would keep
        # this test green.
        other_loop = asyncio.new_event_loop()
        other = other_loop.run_until_complete(_pending_task())
        other_loop.close()
        other._log_destroy_pending = False

        _release(other)  # the raising call, swallowed

        assert not other.cancelled() and not other.done()
        assert not task.cancelled() and not task.done()

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


class _FakeItem:
    """Minimal stand-in for a pytest item: the hook only reads ``nodeid``."""

    def __init__(self, nodeid):
        self.nodeid = nodeid


class TestOrderingPluginUsageErrors:
    """A misconfigured ordering run must read as usage, not as a test failure.

    Bare ``SystemExit`` from a collection hook produces a ~50-line
    ``INTERNALERROR`` traceback and exit 3; ``pytest.UsageError`` produces one
    line and exit 4. The hook is a plain function, so this pins the contract
    without spawning a subprocess.
    """

    @pytest.fixture(autouse=True)
    def _no_ambient_ordering_env(self, monkeypatch):
        """Start from a clean slate for both ordering variables.

        Without this the tests inherit whatever the developer exported: with
        ``ATLAS_TEST_ORDER_SCOPE=sideways`` set, two of them failed. Depending
        on ambient environment is precisely the leak this series removes, so
        the tests for it should not do it either.
        """
        monkeypatch.delenv("ATLAS_TEST_ORDER", raising=False)
        monkeypatch.delenv("ATLAS_TEST_ORDER_SCOPE", raising=False)

    def _hook(self):
        """Load the plugin by path.

        ``scripts/`` is only on ``PYTHONPATH`` when the ordering leg runs, so a
        plain ``import pytest_test_order`` would fail in an ordinary suite run.
        """
        import importlib.util

        plugin_path = (
            Path(__file__).resolve().parents[2] / "scripts" / "pytest_test_order.py"
        )
        if not plugin_path.exists():
            pytest.skip(f"ordering plugin not present at {plugin_path}")

        spec = importlib.util.spec_from_file_location("_pytest_test_order", plugin_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.pytest_collection_modifyitems

    def test_invalid_order_value_raises_usage_error(self, monkeypatch):
        monkeypatch.setenv("ATLAS_TEST_ORDER", "nonsense")

        with pytest.raises(pytest.UsageError, match="ATLAS_TEST_ORDER='nonsense'"):
            self._hook()(session=None, config=None, items=[])

    @pytest.mark.parametrize("order", ["7", "reverse"])
    def test_invalid_scope_raises_usage_error(self, order, monkeypatch):
        """Including the ``reverse`` case, which is the point of the change.

        Scope is validated before the order branch, so the same typo fails the
        same way whichever ordering is asked for. With validation left on the
        shuffle path only, ``reverse`` exited 0 and a seed exited 4.
        """
        monkeypatch.setenv("ATLAS_TEST_ORDER", order)
        monkeypatch.setenv("ATLAS_TEST_ORDER_SCOPE", "sideways")

        with pytest.raises(pytest.UsageError, match="ATLAS_TEST_ORDER_SCOPE='sideways'"):
            self._hook()(session=None, config=None, items=[])

    def test_seeded_shuffle_is_deterministic_and_scope_aware(self, monkeypatch):
        """The shuffle branch: it really reorders, repeatably, and honors scope.

        Seed 6 over four modules, because seed 11 happens to be the identity
        permutation -- with that seed the test passed even with ``rng.shuffle``
        deleted, which is the failure mode a shuffle test exists to catch.
        """
        original = [
            _FakeItem(f"tests/test_{f}.py::test_{i}") for f in "abcd" for i in range(3)
        ]

        def run(order, scope, source):
            monkeypatch.setenv("ATLAS_TEST_ORDER", order)
            monkeypatch.setenv("ATLAS_TEST_ORDER_SCOPE", scope)
            copy = list(source)
            self._hook()(session=None, config=None, items=copy)
            return [i.nodeid for i in copy]

        nodeids = [i.nodeid for i in original]

        by_module = run("6", "module", original)
        assert by_module != nodeids, "seed 6 must actually reorder the modules"
        assert by_module == run("6", "module", original), "same seed must repeat"
        assert sorted(by_module) == sorted(nodeids), "no item may be lost"

        # Module scope keeps each file's tests contiguous.
        files = [n.split("::")[0] for n in by_module]
        assert len(set(files)) == 4
        assert [f for i, f in enumerate(files) if i == 0 or f != files[i - 1]] == list(
            dict.fromkeys(files)
        ), f"module scope must not interleave files: {files}"

        by_test = run("6", "test", original)
        assert by_test != nodeids, "seed 6 must actually reorder the items"
        assert sorted(by_test) == sorted(nodeids)
        assert by_test != by_module, "test scope shuffles differently from module scope"

    def test_unset_leaves_order_untouched(self, monkeypatch):
        monkeypatch.delenv("ATLAS_TEST_ORDER", raising=False)
        items = ["a", "b", "c"]

        self._hook()(session=None, config=None, items=items)

        assert items == ["a", "b", "c"]

    def test_reverse_reverses(self, monkeypatch):
        monkeypatch.setenv("ATLAS_TEST_ORDER", "reverse")
        items = ["a", "b", "c"]

        self._hook()(session=None, config=None, items=items)

        assert items == ["c", "b", "a"]


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

