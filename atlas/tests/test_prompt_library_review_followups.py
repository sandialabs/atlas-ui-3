"""Follow-ups from the independent review of the November 21 changes (issue #1169).

Each test reproduces a reviewer's finding against the changes before the
follow-up and fails there:

A. a never-pinned conversation with damaged metadata could never use a team
   prompt, and the refusal said "try again" forever;
B. an authorizer outage on a pinned conversation read as a revocation: the
   "no longer available" refusal with a clear offer, and a false use_denied;
C. tools-mode synthesis could copy the team block into a system message;
D. mid-run compaction summarised the team block away;
E. a stale clear could remove a pin changed meanwhile, and the response gave
   the browser no revision to follow;
F. strict role-ordering models got two user messages in a row.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from atlas.domain.errors import AuthorizationError
from atlas.domain.team_prompt import TeamPrompt
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.prompts.prompt_library_service import PromptLibraryService
from atlas.tests.test_prompt_library_chat_turn import (
    USER,
    Members,
    _pin_client,
    _raw_metadata,
    _service,
    _set_raw_metadata,
    _shared_prompt,
    _turn,
)


def is_team_prompt_message(message):
    """The team block, by the shape the builder writes (no import, so this
    file also runs, and fails, on the code before the follow-ups)."""
    content = message.get("content")
    return (message.get("role") == "user" and isinstance(content, str)
            and content.startswith("Team prompt ") and "<team_prompt>" in content)


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def env(tmp_path):
    from atlas.modules.chat_history import (
        ConversationRepository,
        PromptLibraryRepository,
        get_session_factory,
        init_database,
    )

    init_database(f"duckdb:///{tmp_path / 'chat.db'}")
    factory = get_session_factory()
    members = Members()
    library = PromptLibraryService(
        PromptLibraryRepository(factory), group_check=members.check, group_candidates=lambda: ["eng"]
    )
    return {"conversations": ConversationRepository(factory), "library": library, "members": members}


@pytest.mark.asyncio
async def test_a_team_prompt_on_a_damaged_never_pinned_conversation_says_why(env):
    prompt, _ = await _shared_prompt(env["library"])
    service, calls, _ = _service(env)
    sid, conv = uuid4(), str(uuid4())
    await _turn(service, sid, conv)
    _set_raw_metadata(env, conv, "{not json")
    for _ in range(2):
        with pytest.raises(AuthorizationError) as exc:
            await _turn(service, sid, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
        assert "Start a new conversation to use a team prompt" in exc.value.message
        assert "Try again" not in exc.value.message
    assert _raw_metadata(env, conv) == "{not json" and len(calls) == 1


@pytest.mark.asyncio
async def test_an_authorizer_outage_is_not_a_revocation_on_the_chat_path(env):
    from atlas.domain.conversation_prompt_pin import PromptPinBlockedError

    prompt, _ = await _shared_prompt(env["library"])
    service, calls, _ = _service(env)
    sid, conv = uuid4(), str(uuid4())
    await _turn(service, sid, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    denials_before = [e for e in env["library"].repository.list_audit_events(prompt["id"])
                      if e["event_type"] == "use_denied"]

    async def outage(user, group):
        return None

    env["library"]._group_check = outage
    with pytest.raises(AuthorizationError) as exc:
        await _turn(service, sid, conv)
    assert not isinstance(exc.value, PromptPinBlockedError)  # no "clear it" offer
    assert "could not be confirmed right now" in exc.value.message
    denials = [e for e in env["library"].repository.list_audit_events(prompt["id"]) if e["event_type"] == "use_denied"]
    assert denials == denials_before  # not recorded as a refused use
    assert len(calls) == 1
    # A *new* selection during the outage reveals nothing about the entry.
    other, _, _ = _service(env)
    with pytest.raises(AuthorizationError) as new_selection:
        await _turn(other, uuid4(), str(uuid4()), library_prompt={"prompt_id": prompt["id"], "version_id": None})
    assert "not available" in new_selection.value.message
    # Once the authorizer answers again, the pin runs.
    env["library"]._group_check = env["members"].check
    await _turn(service, sid, conv)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_tools_synthesis_never_copies_the_team_block_into_a_system_message(monkeypatch):
    from atlas.application.chat.modes import tools as tools_mod
    from atlas.application.chat.preprocessors.message_builder import MessageBuilder
    from atlas.domain.messages.models import Message, MessageRole
    from atlas.domain.sessions.models import Session

    session = Session(user_email=USER)
    session.history.add_message(Message(role=MessageRole.USER, content="what is in this image?"))
    session.context["files"] = {"a.png": {"image_b64": "AAAA", "image_mime_type": "image/png"}}
    provider = MagicMock()
    provider.get_system_prompt.return_value = "PLATFORM"
    provider.get_tool_synthesis_prompt.side_effect = lambda q: f"SYNTH about: {q}"
    messages = await MessageBuilder(prompt_provider=provider).build_messages(
        session, model_supports_vision=True,
        custom_system_prompt=TeamPrompt(text="TEAM SECRET INSTRUCTIONS", title="T", version_number=1),
    )
    runner = tools_mod.ToolsModeRunner.__new__(tools_mod.ToolsModeRunner)
    runner.prompt_provider = provider
    runner.event_publisher = MagicMock()
    runner.raise_on_stream_error = False
    captured = {}

    async def fake_stream(token_generator=None, **kw):
        return "ok"

    def stream_plain(model, msgs, user_email=None):
        captured["messages"] = msgs

    runner.llm = SimpleNamespace(stream_plain=stream_plain, call_plain=None)
    monkeypatch.setattr(tools_mod, "stream_and_accumulate", fake_stream)
    monkeypatch.setattr(tools_mod.tool_executor, "build_files_manifest", lambda ctx: None)
    await runner._stream_synthesis(MagicMock(), messages, "m", session.context, USER, None)
    systems = [m["content"] for m in captured["messages"] if m["role"] == "system"]
    assert not any("TEAM SECRET INSTRUCTIONS" in text for text in systems)
    team = [m for m in captured["messages"] if is_team_prompt_message(m)]
    assert len(team) == 1  # still there, still user-role


@pytest.mark.asyncio
async def test_mid_run_compaction_keeps_the_team_block(monkeypatch):
    from atlas.application.chat.context import run_compaction_guard as guard_module
    from atlas.application.chat.context.compaction_engine import leading_system_count

    seen = {}

    async def fake_summarize(llm, model, segment, **kw):
        seen["segment"] = segment
        return "SUMMARY"

    monkeypatch.setattr(guard_module, "summarize_messages", fake_summarize)
    guard = guard_module.RunCompactionGuard.__new__(guard_module.RunCompactionGuard)
    guard.compactor = SimpleNamespace(llm=None)
    guard.model, guard.user_email, guard.window = "m", USER, SimpleNamespace(usable=1000)
    guard._summary, guard._ledger, guard._block, guard._request = None, [], None, "q"
    team = TeamPrompt(text="TEAM RULES").message()
    messages = [
        {"role": "system", "content": "PLATFORM + notice"},
        team,
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]
    floor = leading_system_count(messages)
    assert floor == 2
    await guard._summarize_into(messages, floor, 4)
    assert messages[1] == team  # protected, verbatim
    assert not any(is_team_prompt_message(m) for m in seen["segment"])


def test_strict_role_ordering_folds_the_team_block_into_the_users_turn():
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    team = TeamPrompt(text="TEAM RULES").message()
    out = LiteLLMCaller._enforce_strict_role_ordering(
        [{"role": "system", "content": "PLATFORM"}, team, {"role": "user", "content": "Hello"}]
    )
    assert [m["role"] for m in out] == ["system", "user"]
    assert out[1]["content"].startswith(team["content"]) and out[1]["content"].endswith("Hello")
    assert out[0]["content"] == "PLATFORM"  # never promoted
    plain = [{"role": "system", "content": "S"}, {"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
    assert LiteLLMCaller._enforce_strict_role_ordering(plain) == plain  # other messages untouched


@pytest.mark.asyncio
async def test_a_stale_clear_cannot_remove_a_pin_changed_since(env, monkeypatch):
    prompt, _ = await _shared_prompt(env["library"])
    service, _, _ = _service(env)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    seen = json.loads(_raw_metadata(env, conv))["prompt_library_pin_revision"]
    client = _pin_client(env, monkeypatch, env["members"])
    # Another tab re-pins meanwhile (clear + set: revision moves on).
    other, _, _ = _service(env)
    sid = uuid4()
    await _turn(other, sid, conv, library_prompt=None, library_prompt_revision=seen)
    await _turn(other, sid, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    stored = json.loads(_raw_metadata(env, conv))
    stale = client.delete(f"/api/prompts/pins/{conv}", params={"expected_revision": seen}, headers={"x-test-user": USER})
    assert stale.status_code == 409
    assert json.loads(_raw_metadata(env, conv)) == stored  # nothing cleared
    current = stored["prompt_library_pin_revision"]
    fresh = client.delete(f"/api/prompts/pins/{conv}", params={"expected_revision": current},
                          headers={"x-test-user": USER})
    assert fresh.status_code == 200
    assert fresh.json() == {"cleared": True, "prompt_library_pin_revision": current + 1}
    plain = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})  # CLI: unconditional
    assert plain.status_code == 200 and plain.json()["cleared"] is False


@pytest.mark.asyncio
async def test_the_response_says_when_a_recovered_conversation_stays_closed(env, monkeypatch):
    prompt, _ = await _shared_prompt(env["library"])
    service, _, _ = _service(env)
    whole, partial = str(uuid4()), str(uuid4())
    for conv in (whole, partial):
        await _turn(service, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    _set_raw_metadata(env, whole, "{not json")
    meta = json.loads(_raw_metadata(env, partial))
    meta["prompt_library"] = "damaged"
    _set_raw_metadata(env, partial, json.dumps(meta))
    client = _pin_client(env, monkeypatch, env["members"])
    unreadable = client.delete(f"/api/prompts/pins/{whole}", headers={"x-test-user": USER}).json()
    assert unreadable["recovered"] is True and unreadable["classification_unknown"] is True
    readable = client.delete(f"/api/prompts/pins/{partial}", headers={"x-test-user": USER}).json()
    assert readable["recovered"] is True and "classification_unknown" not in readable
    assert isinstance(readable["prompt_library_pin_revision"], int)


def test_a_blocking_refusal_carries_the_revision_it_saw():
    from atlas.application.chat import prompt_library_turn as library_turn

    context = {"conversation_id": "conv-1", library_turn.PIN_REVISION_CONTEXT_KEY: 4,
               library_turn.PIN_CONTEXT_KEY: {"prompt_id": "p", "version_id": "v"}}
    error = library_turn._pin_refusal(context, True, None, True)
    assert error.revision == 4 and error.reason == "unavailable"
