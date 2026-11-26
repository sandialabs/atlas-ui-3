"""Unit tests for atlas.mcp_shared.user_input.UserInput.

The helper is exercised end to end against real servers in
test_mcp_elicitation_round_trip.py; these cover the branches that are awkward
to reach over the wire.
"""

import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp_types import ElicitResult, InputRequiredResult

from atlas.mcp_shared.user_input import UserInput

MODERN = "2026-07-28"
LEGACY = "2025-11-25"


def _ctx(*, protocol=MODERN, responses=None, state=None, background=False):
    return SimpleNamespace(
        is_background_task=background,
        request_context=None if background else SimpleNamespace(protocol_version=protocol),
        input_responses=responses,
        request_state=state,
        elicit=AsyncMock(return_value="legacy-answer"),
    )


@pytest.mark.asyncio
async def test_first_round_asks_and_returns_none():
    form = UserInput(_ctx())

    assert await form.ask("name", "What's your name?", str, title="Name") is None

    result = form.input_required()
    assert isinstance(result, InputRequiredResult)
    request = result.input_requests["name"]
    assert request.params.message == "What's your name?"
    assert request.params.requested_schema["properties"]["value"]["title"] == "Name"
    assert json.loads(result.request_state) == {"v": 1, "answers": {}}


@pytest.mark.asyncio
async def test_answer_from_this_round_is_returned_typed():
    ctx = _ctx(responses={"age": ElicitResult(action="accept", content={"value": 36})})

    answer = await UserInput(ctx).ask("age", "Age?", int)

    assert answer.action == "accept"
    assert answer.data == 36


@pytest.mark.asyncio
async def test_earlier_answers_come_back_from_request_state():
    state = json.dumps({"v": 1, "answers": {"name": {"action": "accept", "content": {"value": "Ada"}}}})
    form = UserInput(_ctx(state=state))

    name = await form.ask("name", "Name?", str)
    age = await form.ask("age", "Age?", int)

    assert name.data == "Ada"
    assert age is None
    result = form.input_required()
    assert list(result.input_requests) == ["age"]
    # The answer already given is carried forward to the next round.
    assert json.loads(result.request_state)["answers"]["name"]["content"] == {"value": "Ada"}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_refusals_are_reported(action):
    ctx = _ctx(responses={"name": ElicitResult(action=action)})

    answer = await UserInput(ctx).ask("name", "Name?", str)

    assert answer.action == action


@pytest.mark.asyncio
async def test_structured_answer_is_built_into_the_requested_type():
    @dataclass
    class Details:
        title: str
        count: int

    ctx = _ctx(responses={"d": ElicitResult(action="accept", content={"title": "T", "count": 2})})

    answer = await UserInput(ctx).ask("d", "Details?", Details)

    assert answer.data == Details(title="T", count=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["not json", json.dumps({"v": 99, "answers": {"name": {}}}), json.dumps([1, 2])])
async def test_unusable_state_is_ignored(state):
    form = UserInput(_ctx(state=state))

    assert await form.ask("name", "Name?", str) is None


@pytest.mark.asyncio
async def test_non_elicitation_responses_are_skipped():
    ctx = _ctx(responses={"name": SimpleNamespace(roots=[])})

    assert await UserInput(ctx).ask("name", "Name?", str) is None


@pytest.mark.asyncio
async def test_handshake_era_connection_uses_ctx_elicit():
    ctx = _ctx(protocol=LEGACY)
    form = UserInput(ctx)

    answer = await form.ask("name", "Name?", str, title="Name")

    assert answer == "legacy-answer"
    ctx.elicit.assert_awaited_once_with(
        "Name?", str, response_title="Name", response_description=None
    )
    assert form.round_trips is False


@pytest.mark.asyncio
async def test_background_task_round_trips():
    ctx = _ctx(background=True)

    assert UserInput(ctx).round_trips is True
    assert await UserInput(ctx).ask("name", "Name?", str) is None
    ctx.elicit.assert_not_called()


def test_input_required_without_a_question_is_a_bug():
    with pytest.raises(RuntimeError):
        UserInput(_ctx()).input_required()
