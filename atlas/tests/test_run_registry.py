"""Registry retention of a finished parent (issue #1128)."""

from atlas.application.chat.runs.registry import RunRegistry, RunStatus


def test_finished_parent_keeps_its_retention_window_from_its_last_descendant():
    import time as _time

    from atlas.application.chat.runs.coordination import RUN_RETENTION_SECONDS

    registry = RunRegistry()
    parent = registry.start(conversation_id="conv-p", user_email="u@example.com")
    child = registry.start(conversation_id="conv-c", user_email="u@example.com",
                           parent_run_id=parent.run_id, depth=1)
    registry.set_status(parent.run_id, RunStatus.COMPLETED)
    parent.ended_at = _time.time() - 2 * RUN_RETENTION_SECONDS
    registry.set_status(child.run_id, RunStatus.COMPLETED)
    # The child just ended: the parent is retained for a full window from now.
    assert registry.reap_terminal() == 0
    assert registry.get(parent.run_id) is not None
    assert registry.reap_terminal(now=_time.time() + RUN_RETENTION_SECONDS + 60) == 2
