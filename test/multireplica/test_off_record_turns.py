"""An off-record turn claims its conversation and shares nothing else (issue #1128).

Every chat turn is a run. An incognito turn still takes the cluster-wide claim,
but the replica must not write the prompt, a title, a snapshot or any other
content into the shared Redis. The same turn saved to the server does write its
title, which proves the scan below would have found the marker.
"""

import time
import uuid

import redis
from harness.ws_client import WsClient


def _turn(replica, user, marker, **extra):
    with WsClient(replica, user) as tab:
        tab.send({"type": "chat", "content": marker, "model": "scripted-mock", **extra})
        started = tab.recv_until(lambda f: f.get("type") == "run_started", 30)
        # The turn ends with its socket, so stay until the run has finished.
        tab.recv_until(
            lambda f: f.get("type") == "run_status" and f["run"]["status"] == "completed", 30
        )
    return started


def _everything_in_redis(cluster) -> str:
    client = redis.Redis.from_url(cluster.infra.redis_url)
    try:
        dump = {}
        for raw in client.scan_iter(f"{cluster.infra.redis_key_prefix}:*"):
            kind = client.type(raw).decode()
            if kind == "string":
                value = client.get(raw)
            elif kind == "hash":
                value = client.hgetall(raw)
            elif kind == "set":
                value = sorted(client.smembers(raw))
            elif kind == "zset":
                value = client.zrange(raw, 0, -1, withscores=True)
            elif kind == "list":
                value = client.lrange(raw, 0, -1)
            else:
                value = client.xrange(raw)
            dump[raw] = value
        return repr(dump)
    finally:
        client.close()


def _wait_for_record(cluster, run_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = cluster.run_record(run_id)
        if record and record.get("status") in ("completed", "failed", "cancelled"):
            return record
        time.sleep(0.3)
    raise AssertionError(f"run {run_id} never reached a terminal state in Redis")


def test_incognito_turn_writes_only_its_claim_to_redis(cluster):
    user = cluster.new_user()
    marker = f"offrecord-{uuid.uuid4().hex}"
    started = _turn(cluster.a, user, marker, incognito=True, save_mode="server")
    record = _wait_for_record(cluster, started["run_id"])
    # The claim itself is shared: owner, generation, status.
    assert record["status"] == "completed" and record["owner"] and record["generation"] > 0
    assert marker not in _everything_in_redis(cluster), "off-record content reached Redis"
    assert marker not in str(record)


def test_the_same_turn_saved_to_the_server_does_write_its_title(cluster):
    user = cluster.new_user()
    marker = f"recorded-{uuid.uuid4().hex}"
    started = _turn(cluster.a, user, marker, save_mode="server")
    _wait_for_record(cluster, started["run_id"])
    assert marker in _everything_in_redis(cluster)
