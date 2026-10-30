"""GET /api/conversations/{id} with the shared execution projection (issue #1122).

Route-level: the projection is attached as ``execution``, unflushed tool rows
are merged into ``messages``, an approved tool's card is gone once it starts,
a finished run merges nothing, and another user cannot read the projection.
"""

import asyncio
from unittest.mock import patch

import pytest

from atlas.application.chat.runs.execution_state import (
    MemoryExecutionStore,
    invocation_id,
    reset_execution_store,
    set_execution_store,
)
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.config.config_manager import config_manager

CONV = "route-exec-conv"
RUN = "route-run"


@pytest.fixture
def store():
    reset_engine()
    memory = MemoryExecutionStore()
    set_execution_store(memory)
    yield memory
    reset_execution_store()
    reset_engine()


@pytest.fixture
def owner():
    return config_manager.app_settings.test_user


@pytest.fixture
def client(store, owner, tmp_path):
    from main import app
    from starlette.testclient import TestClient

    from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database

    init_database(f"duckdb:///{tmp_path / 'route_exec.db'}")
    repo = ConversationRepository(get_session_factory())
    repo.save_conversation(
        conversation_id=CONV,
        user_email=owner,
        title="Reopened run",
        model="gpt-4",
        messages=[{"role": "user", "content": "run the tools", "message_type": "chat"}],
    )
    with patch("atlas.routes.conversation_routes._get_repo", return_value=repo):
        yield TestClient(app)


def _apply(store, owner, call_id, event_type, **extra):
    event = {
        "type": event_type,
        "run_id": RUN,
        "conversation_id": CONV,
        "user_email": owner,
        "step": 0,
        "tool_call_id": call_id,
        "tool_name": "search",
        "invocation_id": invocation_id(RUN, 0, call_id),
        **extra,
    }
    asyncio.run(store.apply_event(event))


def _get(client, user):
    return client.get(f"/api/conversations/{CONV}", headers={"X-User-Email": user})


def test_mid_step_fetch_shows_rows_and_the_pending_card(client, store, owner):
    _apply(store, owner, "call_0", "tool_start", arguments={"q": "a"})
    _apply(store, owner, "call_1", "tool_approval_request", arguments={"q": "b"})
    resp = _get(client, owner)
    assert resp.status_code == 200
    data = resp.json()
    execution = data["execution"]
    assert execution["run_id"] == RUN
    assert execution["activity"] == "waiting_for_approval"
    assert [r["tool_call_id"] for r in execution["pending_requests"]] == ["call_1"]
    tool_rows = [m for m in data["messages"] if m.get("message_type") == "tool_call"]
    assert [m["metadata"]["invocation_id"] for m in tool_rows] == [
        invocation_id(RUN, 0, "call_0"), invocation_id(RUN, 0, "call_1"),
    ]

    # The approval was answered (possibly on another replica) and the tool
    # started. A reload no longer offers Approve/Reject.
    _apply(store, owner, "call_1", "tool_start", arguments={"q": "b"})
    execution = _get(client, owner).json()["execution"]
    assert execution["pending_requests"] == []
    assert execution["activity"] == "executing"


def test_finished_run_merges_no_rows(client, store, owner):
    _apply(store, owner, "call_0", "tool_start")
    asyncio.run(store.apply_event({
        "type": "run_status", "status": "completed", "run_id": RUN,
        "conversation_id": CONV, "user_email": owner,
    }))
    data = _get(client, owner).json()
    assert data["execution"]["status"] == "completed"
    assert [m for m in data["messages"] if m.get("message_type") == "tool_call"] == []


def test_other_user_gets_neither_the_conversation_nor_the_projection(client, store, owner):
    _apply(store, owner, "call_0", "tool_approval_request", arguments={"secret": "x"})
    resp = _get(client, "intruder@test.com")
    assert resp.status_code == 404
    assert "secret" not in resp.text
