"""A browser reconnecting to a replica that does not own its run (issue #1219).

The run executes on replica A; the user's socket and requests land on B, as
they do behind a load balancer with no session affinity. B holds none of the
run's objects, so it must answer from the shared run record: the conversation
reads as running instead of missing, and a socket on B learns when the run
ends, including when A is killed.
"""

import time

from harness.cluster import directive
from harness.ws_client import WsClient, chat_frame

TERMINAL = {"completed", "failed", "cancelled"}


def _is(kind):
    return lambda frame: frame.get("type") == kind


def _snapshot_entry(tab, conversation_id, timeout=30.0):
    """Ask the replica for the run list, watching for the end of this conversation's run."""
    tab.send({"type": "list_runs", "conversation_id": conversation_id, "watch_end": True})
    snapshot = tab.recv_until(_is("runs_snapshot"), timeout)
    return next((r for r in snapshot["runs"] if r.get("conversation_id") == conversation_id), None)


def _wait_entry(tab, conversation_id, pred, timeout):
    deadline = time.monotonic() + timeout
    entry = None
    while time.monotonic() < deadline:
        entry = _snapshot_entry(tab, conversation_id)
        if pred(entry):
            return entry
        time.sleep(0.5)
    raise AssertionError(f"run list never reached the wanted state, last entry: {entry}")


def test_conversation_get_on_non_owner_shows_running_view(cluster):
    user = cluster.new_user()
    with WsClient(cluster.a, user, auto_approve={"atlas_sleep"}) as owner_tab:
        owner_tab.send(chat_frame(directive("elsewhere", sleep=6), ["atlas_sleep"]))
        started = owner_tab.recv_until(_is("run_started"), 30)
        run_id, conversation_id = started["run_id"], started["conversation_id"]
        try:
            # The first turn is unsaved, so only the shared record knows it.
            # The shared projection follows run_started by a moment.
            deadline = time.monotonic() + 10
            while True:
                status, body = cluster.call(cluster.b, "GET", f"/api/conversations/{conversation_id}", user=user)
                if status == 200 or time.monotonic() > deadline:
                    break
                time.sleep(0.25)
            assert status == 200, (status, body)
            assert body["run_elsewhere"] is True and body["run_id"] == run_id, body

            # Another user must not learn that the run exists.
            status, _ = cluster.call(cluster.b, "GET", f"/api/conversations/{conversation_id}",
                                     user=cluster.new_user())
            assert status == 404
        finally:
            cluster.cancel_everywhere(run_id)


def test_non_owner_client_learns_the_terminal_state(cluster):
    user = cluster.new_user()
    with WsClient(cluster.a, user, auto_approve={"atlas_sleep"}) as owner_tab, \
            WsClient(cluster.b, user) as other_tab:
        owner_tab.send(chat_frame(directive("watched", sleep=4), ["atlas_sleep"]))
        started = owner_tab.recv_until(_is("run_started"), 30)
        run_id, conversation_id = started["run_id"], started["conversation_id"]
        try:
            entry = _wait_entry(other_tab, conversation_id, lambda e: e is not None, 15)
            assert entry["run_id"] == run_id and entry["status"] not in TERMINAL, entry
            assert entry["detached"] is True
            # The owner's tab answers the tool approval while it reads frames.
            owner_tab.recv_until(
                lambda f: f.get("type") == "run_status" and f["run"]["status"] in TERMINAL, 60)
            ended = _wait_entry(other_tab, conversation_id, lambda e: e and e["status"] in TERMINAL, 30)
            assert ended["status"] == "completed", ended
            # Without watch_end a finished run is not listed again.
            other_tab.send({"type": "list_runs", "conversation_id": conversation_id})
            plain = other_tab.recv_until(_is("runs_snapshot"), 15)
            assert not [r for r in plain["runs"] if r.get("conversation_id") == conversation_id]
        finally:
            cluster.cancel_everywhere(run_id)


def test_non_owner_client_learns_the_owner_died(cluster):
    user = cluster.new_user()
    with WsClient(cluster.a, user, auto_approve={"atlas_sleep"}) as owner_tab:
        owner_tab.send(chat_frame(directive("ownerdies", sleep=30), ["atlas_sleep"]))
        started = owner_tab.recv_until(_is("run_started"), 30)
        run_id, conversation_id = started["run_id"], started["conversation_id"]
        owner_tab.recv_until(_is("tool_start"), 30)
        cluster.a.kill()
    with WsClient(cluster.b, user) as other_tab:
        # Within about one lease the orphan sweep ends the run and closes its
        # projection; the watching client then sees it as ended, not running.
        ended = _wait_entry(other_tab, conversation_id, lambda e: e and e["status"] in TERMINAL, 90)
        assert ended["status"] == "failed", ended
        assert ended["run_id"] == run_id


def test_owner_killed_before_any_tool_is_a_failed_end(cluster):
    user = cluster.new_user()
    with WsClient(cluster.a, user, auto_approve={"atlas_sleep"}) as owner_tab:
        owner_tab.send(chat_frame(directive("earlykill", sleep=30), ["atlas_sleep"]))
        started = owner_tab.recv_until(_is("run_started"), 30)
        run_id, conversation_id = started["run_id"], started["conversation_id"]
        # No tool_start was read: the run may not have published anything yet.
        cluster.a.kill()
    with WsClient(cluster.b, user) as other_tab:
        ended = _wait_entry(other_tab, conversation_id, lambda e: e and e["status"] in TERMINAL, 90)
        assert ended["status"] == "failed" and ended["run_id"] == run_id, ended


def test_watch_end_without_a_conversation_is_refused(cluster):
    with WsClient(cluster.b, cluster.new_user()) as tab:
        tab.send({"type": "list_runs", "watch_end": True})
        refused = tab.recv_until(_is("error"), 15)
        assert refused["error_type"] == "invalid_request", refused


def test_connection_bound_turn_is_not_shown_on_another_replica(cluster):
    user = cluster.new_user()
    with WsClient(cluster.a, user, auto_approve={"atlas_sleep"}) as owner_tab:
        frame = chat_frame(directive("plain", sleep=6), ["atlas_sleep"])
        frame["agent_mode"] = False
        owner_tab.send(frame)
        started = owner_tab.recv_until(_is("run_started"), 30)
        assert started.get("connection_bound") is True, started
        conversation_id = started["conversation_id"]
        # The turn's tool row reaches the shared projection once it starts.
        owner_tab.recv_until(_is("tool_start"), 30)
        time.sleep(1.0)
        with WsClient(cluster.b, user) as other_tab:
            entry = _snapshot_entry(other_tab, conversation_id)
            assert entry is None, f"a connection-bound turn was listed on another replica: {entry}"
        status, body = cluster.call(cluster.b, "GET", f"/api/conversations/{conversation_id}", user=user)
        assert status == 404, (status, body)
