"""Per-replica coordination metrics and logs (issue #1129)."""

import asyncio
import logging

import pytest

from atlas.application.chat.runs.registry import (
    ConcurrencyLimitError,
    ConversationBusyError,
    RunRegistry,
    RunStatus,
)
from atlas.core import coordination_metrics as cm
from atlas.core.coordination_metrics import (
    CoordinationEvent,
    CoordinationMetrics,
    EndReason,
    coordination_metrics,
    outcome_for,
    reset_coordination_metrics,
)

USER = "ops@example.com"


@pytest.fixture(autouse=True)
def _fresh_metrics(monkeypatch):
    monkeypatch.setenv("ATLAS_REPLICA_ID", "replica-test")
    reset_coordination_metrics()
    yield
    reset_coordination_metrics()


def _coord_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("[COORD]")]


class TestOutcome:
    def test_interrupted_reasons_map_to_interrupted(self):
        assert outcome_for("failed", EndReason.REPLICA_SHUTDOWN.value) == "interrupted"
        assert outcome_for("failed", EndReason.OWNER_LOST.value) == "interrupted"
        assert outcome_for("failed", EndReason.WALL_CLOCK_LIMIT.value) == "failed"
        assert outcome_for("failed", EndReason.ERROR.value) == "failed"
        assert outcome_for("cancelled", EndReason.USER_CANCELLED.value) == "cancelled"
        assert outcome_for("completed", None) == "completed"


class TestRegistryInstrumentation:
    def test_lifecycle_is_counted_and_logged(self, caplog):
        caplog.set_level(logging.INFO, logger="atlas.coordination")
        registry = RunRegistry()
        record = registry.start(conversation_id="c1", user_email=USER)
        registry.set_status(record.run_id, RunStatus.RUNNING)
        registry.set_status(record.run_id, RunStatus.COMPLETED)

        metrics = coordination_metrics()
        assert metrics.counter("atlas_runs_admitted_total", kind="top_level") == 1
        assert metrics.counter("atlas_runs_finished_total", outcome="completed", reason="completed") == 1
        assert metrics.counter("atlas_run_transitions_total", from_status="queued", to_status="running") == 1
        assert record.end_reason == "completed"
        lines = _coord_lines(caplog)
        assert any("event=run_admitted" in line and record.run_id in line for line in lines)
        finished = [line for line in lines if "event=run_finished" in line]
        assert finished and "replica=replica-test" in finished[0] and "outcome=completed" in finished[0]
        # User identity never reaches a [COORD] line.
        assert all(USER not in line for line in lines)

    def test_terminal_is_counted_once(self):
        registry = RunRegistry()
        record = registry.start(conversation_id="c1", user_email=USER)
        registry.cancel(record.run_id, USER)
        registry.set_status(record.run_id, RunStatus.COMPLETED)  # late unwinding update
        metrics = coordination_metrics()
        assert metrics.counter("atlas_runs_finished_total") == 1
        assert metrics.counter("atlas_runs_finished_total", outcome="cancelled", reason="user_cancelled") == 1

    def test_rejections_are_counted_by_reason(self):
        registry = RunRegistry(max_concurrent_runs_per_user=1)
        registry.start(conversation_id="c1", user_email=USER)
        with pytest.raises(ConversationBusyError):
            registry.start(conversation_id="c1", user_email=USER)
        with pytest.raises(ConcurrencyLimitError):
            registry.start(conversation_id="c2", user_email=USER)
        metrics = coordination_metrics()
        assert metrics.counter("atlas_runs_rejected_total", reason="conversation_busy") == 1
        assert metrics.counter("atlas_runs_rejected_total", reason="user_concurrency_limit") == 1

    def test_wall_clock_is_failed_not_interrupted(self):
        registry = RunRegistry()
        record = registry.start(conversation_id="c1", user_email=USER)
        registry.enforce_wall_clock(1, now=record.created_at + 10)
        assert record.status == RunStatus.FAILED
        assert record.end_reason == "wall_clock_limit"
        assert coordination_metrics().counter("atlas_runs_finished_total", outcome="failed") == 1
        assert registry.status_counts()["interrupted"] == 0

    def test_interrupt_active_marks_runs_interrupted(self, caplog):
        caplog.set_level(logging.INFO, logger="atlas.coordination")
        registry = RunRegistry()
        live = registry.start(conversation_id="c1", user_email=USER)
        waiting = registry.start(conversation_id="c2", user_email=USER)
        registry.set_status(waiting.run_id, RunStatus.WAITING_FOR_INPUT, waiting_on="tool_approval")
        done = registry.start(conversation_id="c3", user_email=USER)
        registry.set_status(done.run_id, RunStatus.COMPLETED)

        interrupted = registry.interrupt_active()

        assert set(interrupted) == {live.run_id, waiting.run_id}
        for record in (live, waiting):
            assert record.status == RunStatus.FAILED
            assert record.end_reason == "replica_shutdown"
            assert "interrupted" in record.error
            assert record.to_public_dict()["end_reason"] == "replica_shutdown"
        assert done.end_reason == "completed"
        counts = registry.status_counts()
        assert counts["failed"] == 2 and counts["interrupted"] == 2 and counts["completed"] == 1
        assert coordination_metrics().counter("atlas_runs_finished_total", outcome="interrupted") == 2
        assert sum("event=run_interrupted" in line for line in _coord_lines(caplog)) == 2

    def test_status_counts_cover_every_state(self):
        registry = RunRegistry()
        queued = registry.start(conversation_id="c1", user_email=USER)
        running = registry.start(conversation_id="c2", user_email=USER)
        registry.set_status(running.run_id, RunStatus.RUNNING)
        registry.mark_detached(running.run_id)
        counts = registry.status_counts()
        assert counts["queued"] == 1 and counts["running"] == 1 and counts["detached"] == 1
        assert queued.end_reason is None


class TestDependencies:
    def test_outage_logged_once_then_recovery(self, caplog, monkeypatch):
        caplog.set_level(logging.INFO, logger="atlas.coordination")
        metrics = CoordinationMetrics(replica_id="r1")
        metrics.dependency_ok("redis")
        for _ in range(5):
            metrics.dependency_failed("redis", ConnectionError("boom"), op="session_get")
        assert metrics.dependencies()["redis"]["status"] == "unavailable"
        assert metrics.dependencies()["redis"]["consecutive_failures"] == 5
        assert metrics.counter("atlas_dependency_errors_total", dependency="redis") == 5
        lines = _coord_lines(caplog)
        assert sum("event=dependency_down" in line for line in lines) == 1
        assert "error=ConnectionError" in next(line for line in lines if "dependency_down" in line)

        # A long outage is re-logged, not once per failure.
        monkeypatch.setattr(cm, "DEPENDENCY_REPEAT_LOG_SECONDS", 0)
        metrics.dependency_failed("redis", ConnectionError("boom"))
        assert any("event=dependency_still_down" in line for line in _coord_lines(caplog))

        metrics.dependency_ok("redis")
        metrics.dependency_ok("redis")
        assert metrics.dependencies()["redis"]["status"] == "ok"
        assert sum("event=dependency_recovered" in line for line in _coord_lines(caplog)) == 1

    def test_redis_stats_errors_feed_the_dependency_state(self):
        from atlas.infrastructure.redis.client import RedisStats

        RedisStats().record_error(TimeoutError(), op="session_update")
        state = coordination_metrics().dependencies()["redis"]
        assert state["status"] == "unavailable" and state["last_op"] == "session_update"

    def test_redis_health_marks_up_and_down(self):
        import fakeredis

        from atlas.infrastructure.redis.client import RedisClientManager, RedisSettings

        manager = RedisClientManager(RedisSettings(url="redis://x:6379/0"), client=fakeredis.FakeAsyncRedis())
        assert asyncio.run(manager.health())["status"] == "ok"
        assert coordination_metrics().dependencies()["redis"]["status"] == "ok"

        class _Down:
            async def ping(self):
                raise ConnectionError("down")

        manager._client = _Down()
        assert asyncio.run(manager.health())["status"] == "unavailable"
        assert coordination_metrics().dependencies()["redis"]["status"] == "unavailable"


class TestPrometheus:
    def test_render_has_replica_label_types_and_gauges(self):
        metrics = CoordinationMetrics(replica_id='pod-"a"')
        metrics.incr("atlas_runs_finished_total", outcome="interrupted", reason="replica_shutdown")
        metrics.incr("atlas_run_duration_seconds_sum", 2.5, outcome="completed")
        metrics.incr("atlas_run_duration_seconds_count", 1, outcome="completed")
        metrics.dependency_failed("postgres", "Timeout", op="probe")
        text = metrics.render_prometheus([("atlas_runs", {"status": "running"}, 3)])
        assert "# TYPE atlas_runs gauge" in text
        assert 'atlas_runs{replica="pod-\\"a\\"",status="running"} 3' in text
        assert "# TYPE atlas_runs_finished_total counter" in text
        assert 'outcome="interrupted"' in text
        assert "# TYPE atlas_run_duration_seconds summary" in text
        assert 'atlas_dependency_up{dependency="postgres",replica="pod-\\"a\\""} 0' in text
        assert text.count("# TYPE atlas_run_duration_seconds ") == 1

    def test_reserved_events_use_stable_names(self, caplog):
        caplog.set_level(logging.INFO, logger="atlas.coordination")
        metrics = CoordinationMetrics(replica_id="r1")
        metrics.record_event(CoordinationEvent.LEASE_LOST, run_id="r-1", owner="r2")
        assert metrics.counter("atlas_coordination_events_total", event="lease_lost") == 1
        assert "event=lease_lost replica=r1" in _coord_lines(caplog)[0]

    def test_values_with_spaces_are_quoted(self, caplog):
        caplog.set_level(logging.INFO, logger="atlas.coordination")
        CoordinationMetrics(replica_id="r1").record_event("x", note="two words\nnext")
        assert 'note="two words next"' in _coord_lines(caplog)[0]

    def test_summary_logged_only_on_change(self, caplog):
        caplog.set_level(logging.INFO, logger="atlas.coordination")
        metrics = CoordinationMetrics(replica_id="r1")
        assert metrics.maybe_log_summary({"running": 1}) is True
        assert metrics.maybe_log_summary({"running": 1}) is False
        assert metrics.maybe_log_summary({"running": 2}) is True


class TestReviewFollowUps:
    def test_prometheus_values_keep_full_precision(self):
        metrics = CoordinationMetrics(replica_id="r1")
        metrics.started_at = 1762279473.125
        metrics.incr("atlas_runs_admitted_total", 1234567, kind="top_level")
        text = metrics.render_prometheus()
        samples = {line.split("{")[0]: line.rsplit(" ", 1)[1] for line in text.splitlines() if not line.startswith("#")}
        assert float(samples["atlas_replica_start_time_seconds"]) == 1762279473.125
        assert samples["atlas_runs_admitted_total"] == "1234567"

    def test_only_connectivity_errors_mark_the_database_down(self, monkeypatch):
        from sqlalchemy.exc import IntegrityError, OperationalError

        import atlas.infrastructure.dependency_probes as probes
        import atlas.modules.chat_history as chat_history
        from atlas.application.chat.service import _record_persist_failure

        monkeypatch.setattr(chat_history, "get_engine", object)
        monkeypatch.setattr(probes, "database_dependency_name", lambda engine: "postgres")
        _record_persist_failure(IntegrityError("INSERT", {}, Exception("duplicate key")))
        metrics = coordination_metrics()
        assert "postgres" not in metrics.dependencies()
        assert metrics.counter("atlas_conversation_persist_failures_total", error="IntegrityError") == 1
        _record_persist_failure(OperationalError("SELECT", {}, Exception("server closed the connection")))
        assert metrics.dependencies()["postgres"]["status"] == "unavailable"

    def test_redis_command_errors_are_not_an_outage(self):
        from redis.exceptions import ConnectionError as RedisConnectionError
        from redis.exceptions import ResponseError

        from atlas.infrastructure.redis.client import RedisStats

        stats = RedisStats()
        stats.record_error(ResponseError("OOM command not allowed"), op="session_update")
        metrics = coordination_metrics()
        assert "redis" not in metrics.dependencies()
        assert metrics.counter("atlas_dependency_command_errors_total", error="ResponseError") == 1
        assert stats.command_errors == 1
        stats.record_error(RedisConnectionError("refused"), op="session_get")
        assert metrics.dependencies()["redis"]["status"] == "unavailable"

    def test_statement_errors_from_a_reachable_database_are_not_an_outage(self):
        from sqlalchemy.exc import OperationalError

        from atlas.infrastructure.dependency_probes import is_db_connectivity_error

        class _PgError(Exception):
            def __init__(self, sqlstate):
                super().__init__(sqlstate)
                self.sqlstate = sqlstate

        for sqlstate in ("57014", "40P01", "40001"):  # statement timeout, deadlock, serialization
            assert not is_db_connectivity_error(OperationalError("SELECT", {}, _PgError(sqlstate)))
        for sqlstate in ("08006", "57P01"):  # connection failure, admin shutdown
            assert is_db_connectivity_error(OperationalError("SELECT", {}, _PgError(sqlstate)))
        assert is_db_connectivity_error(OperationalError("SELECT", {}, Exception("connection refused")))

    def test_redis_pool_exhaustion_is_not_an_outage(self):
        from redis.exceptions import ConnectionError as RedisConnectionError

        from atlas.infrastructure.redis.client import RedisStats

        RedisStats().record_error(RedisConnectionError("No connection available."), op="session_get")
        metrics = coordination_metrics()
        assert "redis" not in metrics.dependencies()
        assert metrics.counter("atlas_dependency_command_errors_total", error="PoolExhausted") == 1

    def test_replica_info_uses_instance_id_label(self):
        text = CoordinationMetrics(replica_id="r1").render_prometheus()
        info = next(line for line in text.splitlines() if line.startswith("atlas_replica_info{"))
        assert 'instance_id="' in info and 'instance="' not in info
