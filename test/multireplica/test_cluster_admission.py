"""Cluster-wide run admission (#1128): exclusivity, caps and slot cleanup.

These guard behavior that holds today. The cap and race criteria themselves
live in ``test_run_coordination_gaps.py`` (now plain tests).
"""

import json
import time
import uuid

from harness.cluster import RUN_CAP, directive
from websockets.sync.client import connect


def _ws(replica, user):
    return connect(
        replica.url.replace("http://", "ws://") + "/ws",
        additional_headers={"X-User-Email": user},
        open_timeout=10,
    )


def _send_turn(ws, conversation_id, message):
    ws.send(json.dumps({
        "type": "chat",
        "content": message,
        "model": "scripted-mock",
        "agent_mode": True,
        "agent_max_steps": 5,
        "selected_tools": ["atlas_sleep"],
        "conversation_id": conversation_id,
        "save_mode": "server",
    }))


def _wait_frame(ws, wanted, timeout=20.0):
    """The first frame whose type is in ``wanted`` (other frames skipped)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            frame = json.loads(ws.recv(timeout=max(0.1, deadline - time.monotonic())))
        except TimeoutError:
            break
        if frame.get("type") in wanted:
            return frame
    raise AssertionError(f"no {wanted} frame within {timeout}s")


def _admission_outcome(ws):
    """``run_started`` or the admission ``error`` frame for the turn just sent."""
    frame = _wait_frame(ws, {"run_started", "error"})
    return frame


def test_busy_conversation_on_one_replica_is_refused_on_another(cluster):
    user = cluster.new_user()
    conversation_id = str(uuid.uuid4())
    with _ws(cluster.a, user) as on_a, _ws(cluster.b, user) as on_b:
        _send_turn(on_a, conversation_id, directive("busy-a", sleep=6))
        started = _admission_outcome(on_a)
        assert started["type"] == "run_started", started
        try:
            _send_turn(on_b, conversation_id, directive("busy-b", sleep=1))
            refused = _admission_outcome(on_b)
            assert refused.get("error_type") == "conversation_busy", refused
        finally:
            on_a.send(json.dumps({"type": "stop_streaming", "run_id": started["run_id"]}))

        # Once A's run ended its claim is released: B may now run the
        # conversation. Retry briefly, since the release is asynchronous.
        deadline = time.monotonic() + 10
        outcome = None
        while time.monotonic() < deadline:
            _send_turn(on_b, conversation_id, "hello again")
            outcome = _admission_outcome(on_b)
            if outcome["type"] == "run_started":
                break
            assert outcome.get("error_type") == "conversation_busy", outcome
            time.sleep(0.5)
        assert outcome and outcome["type"] == "run_started", f"conversation never freed: {outcome}"


def test_refused_admissions_leave_no_slot_behind(cluster):
    """Fill the cap on A, get refused on both replicas, stop everything: B then admits a full cap."""
    user = cluster.new_user()
    runs = [cluster.launch_ok(cluster.a, user, directive(f"fill{i}", sleep=20))["run_id"]
            for i in range(RUN_CAP)]
    try:
        for replica in (cluster.a, cluster.b, cluster.b):
            status, body = cluster.launch(replica, user, directive("over", sleep=1))
            assert status == 429, (status, body)
    finally:
        for run_id in runs:
            cluster.cancel_everywhere(run_id)

    admitted = []
    try:
        deadline = time.monotonic() + 15
        while len(admitted) < RUN_CAP and time.monotonic() < deadline:
            status, body = cluster.launch(cluster.b, user, directive(f"again{len(admitted)}", sleep=8))
            if status in (200, 202):
                admitted.append(body["run_id"])
            else:
                assert status == 429, (status, body)
                time.sleep(0.5)  # releases are asynchronous; give them a moment
        assert len(admitted) == RUN_CAP, f"only {len(admitted)} of {RUN_CAP} slots came back"
        status, body = cluster.launch(cluster.a, user, directive("over-again", sleep=1))
        if status in (200, 202):
            admitted.append(body["run_id"])
        assert status == 429, "cap not enforced after the slots came back"
    finally:
        for run_id in admitted:
            cluster.cancel_everywhere(run_id)
