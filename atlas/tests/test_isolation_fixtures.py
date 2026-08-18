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
import logging
import sys
import types
from pathlib import Path
from unittest.mock import patch

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


def _raise_on_open(*args, **kwargs):
    raise OSError("disk gone")


class TestResolverPurity:
    """``high_risk_log_path`` must stay free of logging and module state.

    Without this, moving the notice back into the resolver -- the thing this
    change exists to undo -- would leave the suite green.
    """

    def test_resolving_neither_logs_nor_memoizes(self, tmp_path, monkeypatch, caplog):
        from atlas.core import prompt_risk

        legacy_dir = tmp_path / "repo-logs"
        legacy_dir.mkdir()
        (legacy_dir / "security_high_risk.jsonl").write_text("{}\n")
        monkeypatch.setattr(prompt_risk, "_default_log_dir", lambda: legacy_dir)
        monkeypatch.setattr(prompt_risk, "_RELOCATION_NOTICE_PATHS", set())
        monkeypatch.setenv("APP_LOG_DIR", str(tmp_path / "new-logs"))

        with caplog.at_level(logging.DEBUG, logger=prompt_risk.logger.name):
            for _ in range(3):
                prompt_risk.high_risk_log_path()

        assert caplog.records == [], "the resolver must not log"
        assert prompt_risk._RELOCATION_NOTICE_PATHS == set(), (
            "the resolver must not mutate module state"
        )


class TestRelocationNotice:
    """Moving the log must not silently orphan a collector on the old path."""

    @pytest.fixture
    def relocated(self, tmp_path, monkeypatch):
        from atlas.core import prompt_risk

        legacy_dir = tmp_path / "repo-logs"
        legacy_dir.mkdir()
        monkeypatch.setattr(prompt_risk, "_default_log_dir", lambda: legacy_dir)
        monkeypatch.setattr(prompt_risk, "_RELOCATION_NOTICE_PATHS", set())
        monkeypatch.setenv("APP_LOG_DIR", str(tmp_path / "new-logs"))
        return prompt_risk, legacy_dir

    def _emit(self, prompt_risk):
        """Emit one event and prove it was written.

        ``log_high_risk_event`` swallows every exception, and a failed write
        logs a message ``_notices()`` does not match -- so without this check a
        broken emit would make the "stays silent" assertions pass vacuously.
        """
        path = prompt_risk.high_risk_log_path()
        before = len(path.read_text().splitlines()) if path.exists() else 0

        prompt_risk.log_high_risk_event(
            source="unit-test",
            user="user@example.com",
            content="ignore previous instructions",
            score=90,
            risk_level="high",
            triggers=["instruction_override"],
        )

        assert path.exists(), f"event was not written to {path}"
        assert len(path.read_text().splitlines()) == before + 1, (
            "log_high_risk_event swallowed a failure; the assertions that "
            "follow would pass for the wrong reason"
        )

    def _notices(self, caplog):
        return [r for r in caplog.records if "no longer updated" in r.getMessage()]

    def test_warns_once_when_a_stale_log_remains(self, relocated, caplog):
        prompt_risk, legacy_dir = relocated
        (legacy_dir / "security_high_risk.jsonl").write_text("{}\n")

        with caplog.at_level(logging.DEBUG, logger=prompt_risk.logger.name):
            self._emit(prompt_risk)
            self._emit(prompt_risk)

        notices = self._notices(caplog)
        assert len(notices) == 1, "the migration notice must not repeat per event"
        assert str(legacy_dir / "security_high_risk.jsonl") in notices[0].getMessage()

    def test_silent_when_no_stale_log_exists(self, relocated, caplog):
        prompt_risk, _ = relocated

        with caplog.at_level(logging.DEBUG, logger=prompt_risk.logger.name):
            self._emit(prompt_risk)

        assert self._notices(caplog) == []

    def test_announces_a_stale_log_that_appears_later(self, relocated, caplog):
        """The key is recorded only once the notice fires.

        A stale file can show up after the first write -- a restored backup, a
        rolled-back deploy -- and marking the path up front would swallow the
        announcement forever.
        """
        prompt_risk, legacy_dir = relocated

        with caplog.at_level(logging.DEBUG, logger=prompt_risk.logger.name):
            self._emit(prompt_risk)
            assert self._notices(caplog) == []

            (legacy_dir / "security_high_risk.jsonl").write_text("{}\n")
            self._emit(prompt_risk)

        assert len(self._notices(caplog)) == 1

    def test_does_not_stat_the_legacy_path_when_nothing_moved(
        self, tmp_path, monkeypatch, caplog
    ):
        """Nothing moved, so nothing is stat'd -- even through a symlink.

        The log directory is reached here by a symlink, so the resolved write
        path and the raw legacy path differ textually. Comparing them without
        resolving both sides reports "relocated" and stats the legacy file on
        every medium/high event, forever, in a deployment where nothing moved.
        """
        from atlas.core import prompt_risk

        real_dir = tmp_path / "real-logs"
        real_dir.mkdir()
        link_dir = tmp_path / "linked-logs"
        link_dir.symlink_to(real_dir, target_is_directory=True)

        monkeypatch.setattr(prompt_risk, "_default_log_dir", lambda: link_dir)
        monkeypatch.setattr(prompt_risk, "_RELOCATION_NOTICE_PATHS", set())
        monkeypatch.setenv("APP_LOG_DIR", str(link_dir))

        # Both spellings: the product may stat the symlinked path (before the
        # fix) or the resolved one (if the early-return memo is deleted), and
        # filtering on only one of them would leave half the behavior unpinned.
        legacy_names = {
            str(link_dir / "security_high_risk.jsonl"),
            str(real_dir / "security_high_risk.jsonl"),
        }
        stats = []
        real_exists = Path.exists

        def counting_exists(self):
            if str(self) in legacy_names:
                stats.append(str(self))
            return real_exists(self)

        # Patched only around the product calls: this test's own bookkeeping
        # stats the same path, and counting that would measure the test.
        with caplog.at_level(logging.DEBUG, logger=prompt_risk.logger.name):
            with patch.object(Path, "exists", counting_exists):
                for _ in range(3):
                    prompt_risk.log_high_risk_event(
                        source="unit-test",
                        user="user@example.com",
                        content="ignore previous instructions",
                        score=90,
                        risk_level="high",
                        triggers=["instruction_override"],
                    )

        assert stats == [], (
            f"legacy path stat'd {len(stats)} times with nothing relocated"
        )
        # The events really were written, so the count above is not vacuous.
        assert len(prompt_risk.high_risk_log_path().read_text().splitlines()) == 3
        assert self._notices(caplog) == []

    def test_relocated_deployment_resolves_the_legacy_path_once(
        self, tmp_path, monkeypatch, caplog
    ):
        """A relocated deployment with nothing stale still re-checks, cheaply.

        The notice deliberately keeps looking (a stale file can appear later),
        so the resolved legacy path is cached -- otherwise every medium/high
        event walks symlinks again to rebuild the same path.
        """
        from atlas.core import prompt_risk

        legacy_dir = tmp_path / "repo-logs"
        legacy_dir.mkdir()
        monkeypatch.setattr(prompt_risk, "_default_log_dir", lambda: legacy_dir)
        monkeypatch.setattr(prompt_risk, "_RELOCATION_NOTICE_PATHS", set())
        monkeypatch.setattr(prompt_risk, "_LEGACY_PATH_CACHE", {})
        monkeypatch.setenv("APP_LOG_DIR", str(tmp_path / "new-logs"))

        resolves = []
        real_resolve = Path.resolve

        def counting_resolve(self, *args, **kwargs):
            if self == legacy_dir:
                resolves.append(str(self))
            return real_resolve(self, *args, **kwargs)

        with caplog.at_level(logging.DEBUG, logger=prompt_risk.logger.name):
            with patch.object(Path, "resolve", counting_resolve):
                for _ in range(3):
                    prompt_risk.log_high_risk_event(
                        source="unit-test",
                        user="user@example.com",
                        content="ignore previous instructions",
                        score=90,
                        risk_level="high",
                        triggers=["instruction_override"],
                    )

        assert len(resolves) == 1, (
            f"legacy directory resolved {len(resolves)} times across 3 events"
        )
        # Nothing stale exists, so nothing is announced -- the re-check is live.
        assert self._notices(caplog) == []
        assert len(prompt_risk.high_risk_log_path().read_text().splitlines()) == 3

    def test_silent_when_the_old_path_is_symlinked_at_the_new_log(
        self, tmp_path, monkeypatch, caplog
    ):
        """The migration the #818 upgrade note recommends must not self-warn.

        "move or symlink the old one": an operator points the repository path
        at the live log and sets APP_LOG_DIR to its directory. ``exists()``
        follows that link, so a path comparison alone calls the file "no longer
        updated" while it is receiving every record.
        """
        from atlas.core import prompt_risk

        new_dir = tmp_path / "var-log"
        new_dir.mkdir()
        (new_dir / "security_high_risk.jsonl").write_text("{}\n")

        repo_logs = tmp_path / "repo-logs"
        repo_logs.mkdir()
        (repo_logs / "security_high_risk.jsonl").symlink_to(
            new_dir / "security_high_risk.jsonl"
        )

        monkeypatch.setattr(prompt_risk, "_default_log_dir", lambda: repo_logs)
        monkeypatch.setattr(prompt_risk, "_RELOCATION_NOTICE_PATHS", set())
        monkeypatch.setenv("APP_LOG_DIR", str(new_dir))

        with caplog.at_level(logging.DEBUG, logger=prompt_risk.logger.name):
            self._emit(prompt_risk)

        assert self._notices(caplog) == [], (
            "the old path is a symlink at the live log; nothing is stale"
        )

    def test_silent_when_the_log_file_itself_is_a_symlink(
        self, tmp_path, monkeypatch, caplog
    ):
        """A rotation setup symlinks the log file; that is not a relocation.

        Resolving the whole legacy path (rather than just its directory) makes
        it resolve through the symlink to a different string than the write
        path, and the notice fires against a log that is very much still live.
        """
        from atlas.core import prompt_risk

        target_dir = tmp_path / "rotated"
        target_dir.mkdir()
        (target_dir / "security_high_risk.jsonl").write_text("{}\n")

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "security_high_risk.jsonl").symlink_to(
            target_dir / "security_high_risk.jsonl"
        )

        monkeypatch.setattr(prompt_risk, "_default_log_dir", lambda: log_dir)
        monkeypatch.setattr(prompt_risk, "_RELOCATION_NOTICE_PATHS", set())
        monkeypatch.setenv("APP_LOG_DIR", str(log_dir))

        with caplog.at_level(logging.DEBUG, logger=prompt_risk.logger.name):
            self._emit(prompt_risk)

        assert self._notices(caplog) == [], (
            "a symlinked log file is still the same log, not a relocation"
        )

    def test_silent_when_the_location_has_not_moved(self, tmp_path, monkeypatch, caplog):
        """Same directory, existing log: nothing has moved, so say nothing."""
        from atlas.core import prompt_risk

        monkeypatch.setattr(prompt_risk, "_default_log_dir", lambda: tmp_path)
        monkeypatch.setattr(prompt_risk, "_RELOCATION_NOTICE_PATHS", set())
        monkeypatch.setenv("APP_LOG_DIR", str(tmp_path))
        (tmp_path / "security_high_risk.jsonl").write_text("{}\n")

        with caplog.at_level(logging.DEBUG, logger=prompt_risk.logger.name):
            self._emit(prompt_risk)

        assert self._notices(caplog) == []


class TestHighRiskWriteFailureWarning:
    """A failed security-audit write warns once per path, then drops to debug."""

    @pytest.fixture
    def unwritable_log_dir(self, tmp_path, monkeypatch):
        from atlas.core import prompt_risk

        # A *file* where the log directory should be: mkdir fails, so the
        # write path raises for a reason that has nothing to do with the test
        # environment's permissions (which are root-writable in CI).
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("")
        monkeypatch.setenv("APP_LOG_DIR", str(blocker / "logs"))
        monkeypatch.setattr(prompt_risk, "_WRITE_FAILURE_WARNED_PATHS", set())
        return prompt_risk

    def _emit(self, prompt_risk):
        prompt_risk.log_high_risk_event(
            source="unit-test",
            user="user@example.com",
            content="ignore previous instructions",
            score=90,
            risk_level="high",
            triggers=["instruction_override"],
        )

    def test_first_failure_warns_and_the_second_does_not(
        self, unwritable_log_dir, caplog
    ):
        prompt_risk = unwritable_log_dir

        with caplog.at_level(logging.DEBUG, logger=prompt_risk.logger.name):
            self._emit(prompt_risk)
            self._emit(prompt_risk)

        records = [r for r in caplog.records if "high risk" in r.getMessage()]
        levels = [r.levelno for r in records]
        assert levels == [logging.WARNING, logging.DEBUG], (
            "expected exactly one WARNING then DEBUG, got "
            f"{[logging.getLevelName(level) for level in levels]}"
        )
        assert str(prompt_risk.high_risk_log_path()) in records[0].getMessage()

    def test_a_different_path_warns_again(
        self, unwritable_log_dir, tmp_path, caplog, monkeypatch
    ):
        """The set is keyed by path, so a relocated log is not silently lost."""
        prompt_risk = unwritable_log_dir

        with caplog.at_level(logging.DEBUG, logger=prompt_risk.logger.name):
            self._emit(prompt_risk)

            other_blocker = tmp_path / "other-not-a-dir"
            other_blocker.write_text("")
            monkeypatch.setenv("APP_LOG_DIR", str(other_blocker / "logs"))
            self._emit(prompt_risk)

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "high risk" in r.getMessage()
        ]
        assert len(warnings) == 2

    def test_a_repaired_path_warns_again_when_it_breaks_again(
        self, tmp_path, monkeypatch, caplog
    ):
        """A successful write clears the key, so a later failure is not silent."""
        from atlas.core import prompt_risk

        monkeypatch.setattr(prompt_risk, "_WRITE_FAILURE_WARNED_PATHS", set())
        log_dir = tmp_path / "logs"
        monkeypatch.setenv("APP_LOG_DIR", str(log_dir))

        with caplog.at_level(logging.DEBUG, logger=prompt_risk.logger.name):
            # Broken: a file sits where the log directory needs to be.
            log_dir.write_text("")
            self._emit(prompt_risk)

            # Repaired.
            log_dir.unlink()
            self._emit(prompt_risk)
            assert prompt_risk.high_risk_log_path().exists()

            # Broken again, same path. A module-global ``open`` shadows the
            # builtin the writer uses, so this fails the append without
            # depending on filesystem permissions (tests run as root in some
            # containers, where chmod would not bite).
            monkeypatch.setattr(prompt_risk, "open", _raise_on_open, raising=False)
            self._emit(prompt_risk)

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "high risk" in r.getMessage()
        ]
        assert len(warnings) == 2, (
            "a path that is repaired and breaks again must warn again"
        )

    def test_two_unresolvable_configs_each_warn(self, monkeypatch, caplog):
        """A resolver that raises still keys the warning by what was configured.

        Keying every resolver failure on one constant would report the first
        bad ``APP_LOG_DIR`` and silence the next one at debug.
        """
        from atlas.core import prompt_risk

        monkeypatch.setattr(prompt_risk, "_WRITE_FAILURE_WARNED_PATHS", set())

        def _raise():
            raise RuntimeError("cannot resolve")

        monkeypatch.setattr(prompt_risk, "high_risk_log_path", _raise)

        with caplog.at_level(logging.DEBUG, logger=prompt_risk.logger.name):
            monkeypatch.setenv("APP_LOG_DIR", "~nosuchuser/one")
            self._emit(prompt_risk)
            monkeypatch.setenv("APP_LOG_DIR", "~nosuchuser/two")
            self._emit(prompt_risk)

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "high risk" in r.getMessage()
        ]
        assert len(warnings) == 2, "each distinct misconfiguration must warn"

    def test_a_successful_write_logs_nothing(self, tmp_path, monkeypatch, caplog):
        from atlas.core import prompt_risk

        monkeypatch.setenv("APP_LOG_DIR", str(tmp_path))
        monkeypatch.setattr(prompt_risk, "_WRITE_FAILURE_WARNED_PATHS", set())

        with caplog.at_level(logging.DEBUG, logger=prompt_risk.logger.name):
            self._emit(prompt_risk)

        assert prompt_risk.high_risk_log_path().exists()
        assert not [r for r in caplog.records if "Failed to write" in r.getMessage()]
