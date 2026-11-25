"""Subagent results are readable from any replica (issue #1222).

Turn 1 of a parent conversation runs on replica A and launches a child with
``atlas_launch_subagent``. Turn 2 runs on replica B and has the model call
``atlas_get_subagent_runs`` and ``atlas_get_subagent_result``, as a load
balancer without session affinity would send it. B's local run registry has
never seen the child, so both answers must come from the shared run record
and, once that is gone, from the stored child conversation.
"""

import json
import time

import redis
from harness.cluster import directive
from harness.ws_client import WsClient, chat_frame

LAUNCH_TOOLS = [
    "atlas_discover_subagent_options",
    "atlas_launch_subagent",
    "atlas_get_subagent_runs",
    "atlas_get_subagent_result",
    "atlas_sleep",
]
REFUSAL = "That run is not a sub-conversation of this conversation."


def _is(kind):
    return lambda frame: frame.get("type") == kind


def _tool_result(tab, tool_name, after=0):
    """The decoded result of the first ``tool_name`` completion after frame ``after``."""
    for frame in tab.frames[after:]:
        if frame.get("type") == "tool_complete" and str(frame.get("tool_name", "")).endswith(tool_name):
            result = frame.get("result")
            content = result.get("content") if isinstance(result, dict) and "content" in result else result
            if not isinstance(content, str):
                return content  # structured tool results arrive already decoded
            # Some tools put a sentence before the JSON payload.
            start = next((i for i, ch in enumerate(content) if ch in "{["), None)
            try:
                return json.loads(content[start:]) if start is not None else content
            except ValueError:
                return content
    raise AssertionError(f"no {tool_name} result in {[f.get('type') for f in tab.frames]}")


def _launch_on_a(cluster, user, child_prompt):
    """Turn 1: the parent launches one child through replica A."""
    cluster.ensure_workspace(user)
    with WsClient(cluster.a, user, auto_approve=LAUNCH_TOOLS) as tab:
        tab.send(chat_frame(
            f"label=parent subagent=1 child={child_prompt} words=3 stream_ms=0", LAUNCH_TOOLS
        ))
        started = tab.recv_until(_is("run_started"), 30)
        tab.recv_until(
            lambda f: f.get("type") == "run_status" and f["run"]["run_id"] == started["run_id"]
            and f["run"]["status"] in ("completed", "failed", "cancelled"),
            60,
        )
        launched = _tool_result(tab, "atlas_launch_subagent")
    assert isinstance(launched, dict) and launched.get("run_id"), launched
    return started["conversation_id"], launched


def _observe_on_b(cluster, user, parent_conversation_id, run_id):
    """Turn 2 of the same conversation on replica B; the two read tools' results."""
    with WsClient(cluster.b, user, auto_approve=LAUNCH_TOOLS) as tab:
        tab.send(chat_frame(
            f"label=turn2 observe=1 rid={run_id} words=3 stream_ms=0", LAUNCH_TOOLS,
            conversation_id=parent_conversation_id,
        ))
        started = tab.recv_until(_is("run_started"), 30)
        tab.recv_until(
            lambda f: f.get("type") == "run_status" and f["run"]["run_id"] == started["run_id"]
            and f["run"]["status"] in ("completed", "failed", "cancelled"),
            60,
        )
        return (
            _tool_result(tab, "atlas_get_subagent_runs"),
            _tool_result(tab, "atlas_get_subagent_result"),
        )


def _wait_record(cluster, run_id, terminal, timeout=45.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = cluster.run_record(run_id)
        if record and (record["status"] in ("completed", "failed", "cancelled")) == terminal:
            return record
        time.sleep(0.3)
    raise AssertionError(f"run {run_id} never reached terminal={terminal}: {cluster.run_record(run_id)}")


def _forget_run_record(cluster, run_id):
    """What the retention window does to a finished run's shared record."""
    client = redis.Redis.from_url(cluster.infra.redis_url)
    try:
        prefix = cluster.infra.redis_key_prefix
        client.delete(f"{prefix}:run:{run_id}", f"{prefix}:run-kids:{run_id}")
    finally:
        client.close()


def test_running_child_is_visible_from_the_other_replica(cluster):
    user = cluster.new_user()
    parent_conversation, launched = _launch_on_a(
        cluster, user, directive("kid", sleep=8, steps=1, words=3).replace(" ", ",")
    )
    child_run = launched["run_id"]
    try:
        record = _wait_record(cluster, child_run, terminal=False)
        assert record["parent_conversation_id"] == parent_conversation, record
        assert record["depth"] == "1" and record["title"], record

        runs, result = _observe_on_b(cluster, user, parent_conversation, child_run)

        listed = {run["run_id"]: run for run in runs["runs"]}
        assert child_run in listed, f"replica B does not list the running child: {runs}"
        assert listed[child_run]["parent_conversation_id"] == parent_conversation
        assert listed[child_run]["title"]
        assert result["run_id"] == child_run
        assert result["result_status"] == "pending", result
    finally:
        cluster.cancel_everywhere(child_run)


def test_finished_child_result_is_read_from_the_other_replica(cluster):
    user = cluster.new_user()
    parent_conversation, launched = _launch_on_a(
        cluster, user, directive("kidfin", sleep=1, steps=1, words=3).replace(" ", ",")
    )
    child_run = launched["run_id"]
    _wait_record(cluster, child_run, terminal=True)

    runs, result = _observe_on_b(cluster, user, parent_conversation, child_run)

    assert [r["run_id"] for r in runs["runs"]] == [child_run], runs
    assert result["status"] == "completed", result
    assert result["result_status"] == "available", result
    assert "FINAL[kidfin]" in result["result"], result


def test_child_is_found_after_its_run_record_expired(cluster):
    user = cluster.new_user()
    parent_conversation, launched = _launch_on_a(
        cluster, user, directive("kidgone", sleep=1, steps=1, words=3).replace(" ", ",")
    )
    child_run = launched["run_id"]
    _wait_record(cluster, child_run, terminal=True)
    # The child's conversation is saved at the end of its turn, before release.
    assert launched["conversation_id"] in cluster.conversation_ids(cluster.b, user)
    _forget_run_record(cluster, child_run)
    assert cluster.run_record(child_run) is None

    runs, result = _observe_on_b(cluster, user, parent_conversation, child_run)

    assert [r["conversation_id"] for r in runs["runs"]] == [launched["conversation_id"]], runs
    assert runs["runs"][0]["run_id"] == child_run, runs
    # The outcome and depth were recorded on the stored conversation by the owner.
    assert runs["runs"][0]["status"] == "completed" and runs["runs"][0]["depth"] == 1, runs
    assert result["status"] == "completed", result
    assert result["result_status"] == "available", result
    assert "FINAL[kidgone]" in result["result"], result


def test_another_user_cannot_read_the_child(cluster):
    owner, intruder = cluster.new_user(), cluster.new_user()
    parent_conversation, launched = _launch_on_a(
        cluster, owner, directive("kidpriv", sleep=1, steps=1, words=3).replace(" ", ",")
    )
    child_run = launched["run_id"]
    _wait_record(cluster, child_run, terminal=True)

    # The intruder opens their own conversation and asks for the owner's run.
    runs, result = _observe_on_b(cluster, intruder, None, child_run)

    assert runs["runs"] == [], runs
    assert REFUSAL in json.dumps(result), result
    unknown = _observe_on_b(cluster, intruder, None, "no-such-run")[1]
    # A foreign run and an unknown one are refused in the same words.
    assert REFUSAL in json.dumps(unknown), unknown


def _forget_recorded_outcome(cluster, conversation_id):
    """What a child saved before outcomes were recorded (or whose owner died) looks like."""
    import psycopg

    with psycopg.connect(cluster.infra.postgres_url, autocommit=True) as conn:
        conn.execute(
            "UPDATE conversations SET metadata_json = ((metadata_json::jsonb) - 'launch_status' "
            "- 'latest_status' - 'launch_depth')::text WHERE id = %s",
            (conversation_id,),
        )


def test_unrecorded_outcome_is_never_reported_as_success(cluster):
    user = cluster.new_user()
    parent_conversation, launched = _launch_on_a(
        cluster, user, directive("kidlost", sleep=1, steps=1, words=3).replace(" ", ",")
    )
    child_run = launched["run_id"]
    _wait_record(cluster, child_run, terminal=True)
    assert launched["conversation_id"] in cluster.conversation_ids(cluster.b, user)
    _forget_run_record(cluster, child_run)
    _forget_recorded_outcome(cluster, launched["conversation_id"])

    runs, result = _observe_on_b(cluster, user, parent_conversation, child_run)

    listed = runs["runs"][0]
    assert listed["conversation_id"] == launched["conversation_id"], runs
    assert listed["status"] == "failed" and "not recorded" in listed["error"], runs
    assert result["status"] == "failed" and "not recorded" in result["error"], result
    # The reply is still readable.
    assert "FINAL[kidlost]" in result["result"], result
