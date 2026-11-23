"""``run_started`` reaches the client before any ``run_status`` for the run (#1216).

The registry publishes ``run_status(queued)`` the moment a run is admitted, and
the WebSocket handler delivers it from a task. Seeding the run's session
awaits the session store; with a store that really yields (Redis) that task
used to run during the await and put ``queued`` on the wire ahead of
``run_started``. The run's task must still start only after seeding, so its
first frames and tools see the attached files.

The store here yields on every read, like Redis does.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import main
import pytest

from atlas.application.chat.runs import reset_run_registry
from atlas.application.chat.runs.coordination import set_run_coordinator
from atlas.infrastructure.redis import RedisClientManager, RedisSettings
from atlas.infrastructure.redis.run_coordinator import RedisRunCoordinator
from atlas.tests.asgi_websocket_session import AsgiWebSocketSession

USER = "order@example.com"
CONV = "conv-order"


@pytest.fixture
def coordinator():
    coord = RedisRunCoordinator(
        RedisClientManager(
            RedisSettings(url="redis://fake/0", key_prefix="t"),
            client=fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer()),
        ),
        replica_id="replica-a",
    )
    set_run_coordinator(coord)
    reset_run_registry()
    yield coord
    reset_run_registry()
    set_run_coordinator(None)


@pytest.fixture
def chat_service():
    with patch("main.app_factory") as mock_factory:
        mock_config = MagicMock()
        mock_config.app_settings.test_user = None
        mock_config.app_settings.debug_mode = False
        mock_config.app_settings.auth_user_header = "X-User-Email"
        mock_config.app_settings.auth_user_header_type = "header"
        mock_config.app_settings.auth_aws_expected_alb_arn = ""
        mock_config.app_settings.auth_aws_region = "us-east-1"
        mock_config.app_settings.feature_proxy_secret_enabled = False
        mock_config.app_settings.feature_websocket_origin_check_enabled = False
        mock_config.app_settings.max_concurrent_runs_per_user = 3
        mock_factory.get_config_manager.return_value = mock_config

        service = MagicMock()
        service.end_session = AsyncMock()
        service.session_repository.delete = AsyncMock()
        mock_factory.create_chat_service.return_value = service
        yield service


def _chat_frame():
    return {
        "type": "chat",
        "content": "work",
        "agent_mode": True,
        "save_mode": "server",
        "selected_tools": ["atlas_sleep"],
        "conversation_id": CONV,
    }


async def _one_turn(chat_service, store_delay):
    """Run one tracked turn; return (frames in arrival order, event log)."""
    events = []

    async def slow_get(*_args, **_kwargs):
        events.append("store_read")
        await asyncio.sleep(store_delay)
        return None

    async def slow_create(session_id, user_email):
        await asyncio.sleep(store_delay)
        events.append(f"store_created:{session_id}")
        return MagicMock(context={}, id=session_id)

    async def handle_chat_message(**_kwargs):
        events.append("task_started")
        return {}

    chat_service.session_repository.get = AsyncMock(side_effect=slow_get)
    chat_service.create_session = AsyncMock(side_effect=slow_create)
    chat_service.handle_chat_message = AsyncMock(side_effect=handle_chat_message)

    async with AsgiWebSocketSession(
        main.app, "/ws", headers={"X-User-Email": USER}
    ) as ws:
        await ws.send_json(_chat_frame())
        await ws.receive_until(
            lambda f: f.get("type") == "run_status"
            and f["run"]["status"] in ("completed", "failed"),
            describe="the run's terminal status",
        )
    return ws.frames, events


def _kinds(frames):
    return [
        f["type"] if f["type"] != "run_status" else f"run_status:{f['run']['status']}"
        for f in frames
        if f["type"] in ("run_started", "run_status")
    ]


@pytest.mark.parametrize("store_delay", [0, 0.05])
async def test_run_started_is_the_first_run_frame(chat_service, coordinator, store_delay):
    frames, _events = await _one_turn(chat_service, store_delay)

    kinds = _kinds(frames)
    assert kinds[0] == "run_started", kinds
    assert kinds.count("run_started") == 1
    assert "run_status:queued" in kinds, kinds
    started = next(f for f in frames if f["type"] == "run_started")
    statuses = [f for f in frames if f["type"] == "run_status"]
    assert {s["run"]["run_id"] for s in statuses} == {started["run_id"]}


async def test_run_task_still_starts_after_seeding(chat_service, coordinator):
    _frames, events = await _one_turn(chat_service, 0.05)

    # Seeding creates two sessions: the connection's, then the run's own. The
    # task must begin after both, so it is the run session that gates it.
    started_at = events.index("task_started")
    created_before = {e for e in events[:started_at] if e.startswith("store_created:")}
    assert len(created_before) == 2, events
    last_created = max(i for i, e in enumerate(events) if e.startswith("store_created:"))
    assert started_at > last_created, events
