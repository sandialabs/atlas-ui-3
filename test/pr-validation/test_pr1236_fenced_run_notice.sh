#!/bin/bash
# Test script for PR #1236 (issue #1218): a run cancelled by a coordination outage is told to the user
#
# Runs the backend tests for the fenced-run signal and log texts, the
# frontend tests for the notice, a standalone reproduction of the fence
# (Redis unreachable for a whole lease), and the multi-replica harness
# (required for a change under atlas/application/chat/runs/).
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

fail() {
    echo "FAILED: $*"
    exit 1
}

echo "== backend: fenced-run signal, log texts, orphan sweep logging"
(cd atlas && PYTHONPATH="$PROJECT_ROOT" python -m pytest tests/test_run_fence_notice.py tests/test_run_leases.py -q) \
    || fail "backend tests"
echo "PASSED: backend tests"

echo "== frontend: interrupted-run notice"
(cd frontend && npx vitest run src/test/interrupted-run-notice.test.jsx) || fail "frontend tests"
echo "PASSED: frontend tests"

echo "== a fenced run reports failed/fenced to its client with Redis down"
PYTHONPATH="$PROJECT_ROOT" python - <<'PY' || fail "fence repro"
import asyncio

import fakeredis
from redis.exceptions import ConnectionError as RedisConnectionError

from atlas.application.chat.runs import registry as registry_module
from atlas.application.chat.runs.coordination import set_run_coordinator
from atlas.application.chat.runs.leases import RunFencedError, RunLeaseGuard, set_lease_guard
from atlas.application.chat.runs.registry import RunRegistry
from atlas.infrastructure.redis import RedisClientManager, RedisSettings
from atlas.infrastructure.redis.run_coordinator import RedisRunCoordinator
from atlas.infrastructure.redis.run_leases import RedisRunLeases

EMAIL, LEASE = "u@example.com", 2.0


async def main():
    manager = RedisClientManager(
        RedisSettings(url="redis://fake/0", key_prefix="t"),
        client=fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer()),
    )
    coord = RedisRunCoordinator(manager, replica_id="A", lease_seconds=LEASE)
    clock = [1000.0]
    registry = RunRegistry()
    registry_module._registry = registry
    set_run_coordinator(coord)
    guard = RunLeaseGuard(coord, RedisRunLeases(coord), registry, clock=lambda: clock[0])
    set_lease_guard(guard)
    registry.add_observer(guard.observe)
    frames = []
    registry.add_listener(EMAIL, lambda record: frames.append(record.to_public_dict()))

    run = registry.start(conversation_id="c1", user_email=EMAIL, run_id="r1")
    run.claim = await coord.claim(run_id="r1", conversation_id="c1", user_email=EMAIL, max_runs_per_user=5)
    registry.attach_task("r1", asyncio.create_task(asyncio.sleep(3600)))
    await asyncio.sleep(0.05)
    await guard.ensure_may_act("r1")

    async def down(*args, **kwargs):
        raise RedisConnectionError("redis is down")

    coord.renew = down
    clock[0] += LEASE
    try:
        await guard.ensure_may_act("r1")
    except RunFencedError:
        pass
    last = frames[-1]
    print("client frame:", last["status"], last["end_reason"])
    assert (last["status"], last["end_reason"]) == ("failed", "fenced"), last


asyncio.run(main())
PY
echo "PASSED: fenced run reaches the client as failed/fenced"

echo "== multi-replica harness"
bash test/multireplica_tests.sh || fail "multi-replica harness"
echo "PASSED: all checks"
