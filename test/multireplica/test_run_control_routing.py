"""Stop, steer and approval routed to the run's owner (issue #1128).

Every run here executes on replica A; the command reaches replica B, as it
does when a load balancer sends a user's next request elsewhere. Retried and
duplicated commands must never steer twice, settle an approval twice, or run
an approval-gated tool twice.
"""

import time
import uuid

from harness.cluster import directive
from harness.ws_client import WsClient, chat_frame


def _is(kind):
    return lambda frame: frame.get("type") == kind


def _stored_conversation(cluster, replica, user, conversation_id, timeout=45.0):
    """The saved conversation once its turn has ended and been written."""
    deadline = time.monotonic() + timeout
    body = None
    while time.monotonic() < deadline:
        status, body = cluster.call(replica, "GET", f"/api/conversations/{conversation_id}", user=user)
        if status == 200 and any(m.get("role") == "assistant" for m in body.get("messages") or []):
            return body
        time.sleep(0.5)
    raise AssertionError(f"conversation {conversation_id} never saved: {body}")


def test_websocket_stop_via_non_owner_and_other_users_are_refused(cluster):
    owner = cluster.new_user()
    intruder = cluster.new_user()
    handle = cluster.launch_ok(cluster.a, owner, directive("wsstop", sleep=10))
    run_id, conversation_id = handle["run_id"], handle["conversation_id"]
    try:
        cluster.wait_status(cluster.a, run_id, "running")
        with WsClient(cluster.b, intruder) as other:
            # Another user's stop names the run and its conversation: nothing happens.
            other.send({"type": "agent_control", "action": "stop", "run_id": run_id})
            other.send({"type": "stop_streaming", "conversation_id": conversation_id})
            # And they cannot start a turn inside a conversation A is executing.
            other.send(chat_frame("hijack", ["atlas_sleep"], conversation_id=conversation_id))
            refused = other.recv_until(_is("error"), 15)
            assert refused["error_type"] == "authorization", refused
        time.sleep(1.5)
        status, body = cluster.run_status(cluster.a, run_id)
        assert body["status"] == "running", f"another user's command reached the run: {body}"

        with WsClient(cluster.b, owner) as tab:
            tab.send({"type": "agent_control", "action": "stop", "run_id": run_id})
            frame = tab.recv_until(_is("run_status"), 15)
            assert frame["run"]["run_id"] == run_id and frame["run"]["status"] == "cancelled", frame
        assert cluster.wait_terminal(cluster.a, run_id, timeout=10)["status"] == "cancelled"
    finally:
        cluster.cancel_everywhere(run_id)


def test_repeated_cancel_via_non_owner_is_idempotent(cluster):
    user = cluster.new_user()
    handle = cluster.launch_ok(cluster.a, user, directive("twice", sleep=10))
    run_id = handle["run_id"]
    try:
        cluster.wait_status(cluster.a, run_id, "running")
        first = cluster.cancel(cluster.b, run_id)
        second = cluster.cancel(cluster.b, run_id)
        assert first[0] == 200 and first[1]["status"] == "cancelled" and first[1]["cancelled"] is True, first
        # The run is withdrawn from the directory as it ends; a repeat either
        # reaches the owner again or finds nothing live to route to.
        assert second[0] in (200, 404), second
        if second[0] == 200:
            assert second[1]["status"] == "cancelled" and second[1]["cancelled"] is False, second
        assert cluster.wait_terminal(cluster.a, run_id, timeout=10)["status"] == "cancelled"
    finally:
        cluster.cancel_everywhere(run_id)


def test_command_to_a_frozen_owner_times_out_and_is_not_applied_late(cluster):
    user = cluster.new_user()
    handle = cluster.launch_ok(cluster.a, user, directive("frozen", sleep=12))
    run_id = handle["run_id"]
    try:
        cluster.wait_status(cluster.a, run_id, "running")
        cluster.a.pause()
        try:
            started = time.monotonic()
            status, body = cluster.cancel(cluster.b, run_id)
            elapsed = time.monotonic() - started
        finally:
            cluster.a.resume()
        assert status == 504 and body["detail"]["code"] == "command_timeout", (status, body)
        assert elapsed < 10, f"timeout took {elapsed:.1f}s"
        # A reads the stale command after resuming; past its deadline it must
        # not act on it, or the caller's 504 would be a lie.
        time.sleep(2.5)
        status, body = cluster.run_status(cluster.a, run_id)
        assert body["status"] == "running", f"expired command was applied late: {body}"
        status, body = cluster.cancel(cluster.b, run_id)
        assert status == 200 and body["status"] == "cancelled", (status, body)
    finally:
        cluster.cancel_everywhere(run_id)


def test_steer_via_non_owner_reaches_the_loop_once(cluster):
    user = cluster.new_user()
    steer_text = f"steer-{uuid.uuid4().hex[:8]}"
    with WsClient(cluster.a, user, auto_approve={"atlas_sleep"}) as owner_tab, \
            WsClient(cluster.b, user) as other_tab:
        owner_tab.send(chat_frame(directive("steer", sleep=3, steps=4), ["atlas_sleep"]))
        started = owner_tab.recv_until(_is("run_started"), 30)
        run_id, conversation_id = started["run_id"], started["conversation_id"]
        try:
            # The loop is draining its steering queue once a tool has started.
            owner_tab.recv_until(_is("tool_start"), 30)
            request_id = uuid.uuid4().hex
            for _attempt in range(2):  # a client retry with the same request id
                other_tab.send(chat_frame(steer_text, ["atlas_sleep"], conversation_id=conversation_id,
                                          request_id=request_id))
                ack = other_tab.recv_until(lambda f: f.get("type") in ("agent_update", "error"), 15)
                assert ack.get("update_type") == "steering_queued", ack
                assert ack["run_id"] == run_id, ack
            stored = _stored_conversation(cluster, cluster.b, user, conversation_id)
        finally:
            cluster.cancel_everywhere(run_id)
    steers = [m for m in stored["messages"] if m.get("role") == "user" and steer_text in str(m.get("content"))]
    assert len(steers) == 1, f"steer applied {len(steers)} times: {stored['messages']}"


def test_approval_via_non_owner_runs_the_tool_once(cluster):
    user = cluster.new_user()
    key = f"appr-{user.split('@')[0]}"
    with WsClient(cluster.a, user) as owner_tab, WsClient(cluster.b, user) as other_tab:
        owner_tab.send(chat_frame(directive(key, tool="approved_write", sleep=0), ["harness_approved_write"]))
        started = owner_tab.recv_until(_is("run_started"), 30)
        run_id = started["run_id"]
        try:
            request = owner_tab.recv_until(_is("tool_approval_request"), 30)
            assert request.get("invocation_id") and request.get("run_id") == run_id, request
            assert cluster.side_effects(key) == [], "gated tool ran before approval"
            answer = {
                "type": "tool_approval_response",
                "tool_call_id": request["tool_call_id"],
                "invocation_id": request["invocation_id"],
                "run_id": run_id,
                "conversation_id": request.get("conversation_id"),
                "approved": True,
                "arguments": request.get("arguments"),
            }
            other_tab.send(answer)
            # A retry and a contradicting answer, both via B, then the same
            # answer on the owner: each is told the first answer won.
            other_tab.send(answer)
            other_tab.send({**answer, "approved": False, "reason": "changed my mind"})
            for _ in range(2):
                settled = other_tab.recv_until(_is("tool_approval_settled"), 15)
                assert settled["approved"] is True and settled["already_resolved"] is True, settled
            cluster.wait_side_effects(key, 1, timeout=15)
            owner_tab.send(answer)
            stored = _stored_conversation(cluster, cluster.a, user, started["conversation_id"])
        finally:
            cluster.cancel_everywhere(run_id)
    assert stored is not None
    time.sleep(1)
    records = cluster.side_effects(key)
    assert len(records) == 1 and records[0]["replica"] == "A", records
