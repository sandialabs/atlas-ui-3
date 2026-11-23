"""New Chat lost while disconnected, resumed on another replica (issue #1217).

A browser clicks New Chat with its socket down, so the reset never arrives. It
reconnects to a different replica, which resumes the session from the shared
store. The session still names the old conversation, but the first saved chat
frame that names none must get a fresh conversation, and the old record must
stay as it was.
"""

import time
import uuid

from harness.ws_client import WsClient


def _turn(tab, content, **extra):
    tab.send({"type": "chat", "content": content, "model": "scripted-mock",
              "save_mode": "server", **extra})
    saved = tab.recv_until(lambda f: f.get("type") == "conversation_saved", 30)["conversation_id"]
    # The run holds its conversation until it ends; a next frame must wait.
    tab.recv_until(
        lambda f: f.get("type") == "run_status" and f["run"]["status"] == "completed", 30
    )
    return saved


def _messages(cluster, replica, user, conversation_id):
    status, body = cluster.call(replica, "GET", f"/api/conversations/{conversation_id}", user=user)
    assert status == 200, (status, body)
    return [(m["role"], m.get("content")) for m in body["messages"]]


def _resume_on(replica, user, session_id, timeout=20.0):
    """Reconnect naming ``session_id``; the old connection may still be tearing down."""
    deadline = time.monotonic() + timeout
    while True:
        tab = WsClient(replica, user, resume_session=session_id)
        frame = tab.recv_until(lambda f: f.get("type") == "connection_session", 15)
        if frame["resumed"]:
            return tab
        tab.close()
        assert time.monotonic() < deadline, "session was never resumable on the other replica"
        time.sleep(0.5)


def test_id_less_chat_after_resume_on_another_replica_starts_a_new_conversation(cluster):
    user = cluster.new_user()
    marker = uuid.uuid4().hex[:8]

    first = WsClient(cluster.a, user, resume_session="")
    session_id = first.recv_until(lambda f: f.get("type") == "connection_session", 15)["session_id"]
    old_id = _turn(first, f"old-one-{marker}")
    assert _turn(first, f"old-two-{marker}", conversation_id=old_id) == old_id
    first.close()
    before = _messages(cluster, cluster.a, user, old_id)
    assert len(before) == 4

    # New Chat while disconnected: no reset_session ever reaches a server. The
    # reconnect lands on the other replica.
    second = _resume_on(cluster.b, user, session_id)
    try:
        new_id = _turn(second, f"fresh-{marker}")
    finally:
        second.close()

    assert new_id != old_id
    assert _messages(cluster, cluster.b, user, old_id) == before
    assert _messages(cluster, cluster.b, user, new_id)[0] == ("user", f"fresh-{marker}")
    assert len(_messages(cluster, cluster.a, user, new_id)) == 2


def test_followup_naming_the_conversation_after_resume_still_lands_in_it(cluster):
    user = cluster.new_user()
    marker = uuid.uuid4().hex[:8]

    first = WsClient(cluster.a, user, resume_session="")
    session_id = first.recv_until(lambda f: f.get("type") == "connection_session", 15)["session_id"]
    old_id = _turn(first, f"keep-one-{marker}")
    first.close()

    second = _resume_on(cluster.b, user, session_id)
    try:
        assert _turn(second, f"keep-two-{marker}", conversation_id=old_id) == old_id
    finally:
        second.close()
    assert len(_messages(cluster, cluster.a, user, old_id)) == 4
