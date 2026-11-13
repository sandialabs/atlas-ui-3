"""Chat-turn execution of team-library prompts (issue #1169).

Drives ``ChatService.handle_chat_message`` with a real conversation store and a
real prompt library (temporary DuckDB), stubbing only the orchestrator so no
LLM is called. Covers server-side resolution, crafted frames, exact-version
pinning across publications, restoration from history, revocation, and that
platform admission checks still run first.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from atlas.application.chat import prompt_library_turn as library_turn
from atlas.application.chat.service import ChatService
from atlas.domain.errors import AuthorizationError, ToolAuthorizationError, ValidationError
from atlas.domain.messages.models import Message, MessageRole
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.prompts.prompt_library_service import PromptLibraryService

OWNER = "owner@example.com"
USER = "member@example.com"
OUTSIDER = "outsider@example.com"


async def _publish(lib, user, prompt_id, change=None):
    """Publish the draft the caller currently sees (its draft_revision)."""
    detail = await lib.get_prompt(user, prompt_id)
    return await lib.publish(user, prompt_id, detail.get("draft_revision", 1), change)


async def _edit(lib, user, prompt_id, **fields):
    detail = await lib.get_prompt(user, prompt_id)
    return await lib.update_prompt(user, prompt_id, detail.get("draft_revision", 1), **fields)


async def _share(lib, user, prompt_id, groups, users):
    revision = (await lib.get_sharing(user, prompt_id))["revision"]
    return await lib.set_sharing(user, prompt_id, groups, users, revision)


async def _list(lib, user, **kwargs):
    return (await lib.list_prompts(user, **kwargs))["prompts"]


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


class Members:
    def __init__(self):
        self.table = {"eng": {OWNER, USER}}

    async def check(self, user, group):
        return user in self.table.get(group, set())


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
    library = PromptLibraryService(PromptLibraryRepository(factory), group_check=members.check)
    return {
        "conversations": ConversationRepository(factory),
        "library": library,
        "members": members,
    }


def _service(env, library="default"):
    sessions = {}

    async def _get(session_id):
        return sessions.get(session_id)

    async def _put(session):
        sessions[session.id] = session

    repo = MagicMock()
    repo.get = AsyncMock(side_effect=_get)
    repo.create = AsyncMock(side_effect=_put)
    repo.update = AsyncMock(side_effect=_put)
    service = ChatService(
        llm=MagicMock(),
        tool_manager=MagicMock(),
        connection=MagicMock(),
        config_manager=MagicMock(),
        session_repository=repo,
        conversation_repository=env["conversations"],
        prompt_library_service=env["library"] if library == "default" else library,
    )
    calls = []

    async def fake_execute(**kwargs):
        calls.append(kwargs)
        session = kwargs["session"]
        session.history.add_message(Message(role=MessageRole.USER, content=kwargs["content"]))
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="reply"))
        return {"type": "done"}

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=fake_execute)
    patcher = patch.object(service, "_get_orchestrator", return_value=orchestrator)
    patcher.start()
    service._test_patcher = patcher
    return service, calls, sessions


async def _turn(service, session_id, conversation_id, user=USER, **extra):
    return await service.handle_chat_message(
        session_id=session_id,
        content="hello",
        model="test-model",
        user_email=user,
        conversation_id=conversation_id,
        **extra,
    )


async def _shared_prompt(lib, content="v1 body"):
    prompt = await lib.create_prompt(OWNER, "Team reviewer", content)
    v1 = await _publish(lib, OWNER, prompt["id"], "first")
    await _share(lib, OWNER, prompt["id"], ["eng"], [])
    return prompt, v1


def _stored_meta(env, conversation_id, user=USER):
    return env["conversations"].get_conversation(conversation_id, user)["metadata"]


@pytest.mark.asyncio
async def test_library_prompt_is_resolved_server_side_and_pinned(env):
    prompt, v1 = await _shared_prompt(env["library"])
    service, calls, _ = _service(env)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    assert calls[-1]["custom_system_prompt"] == "v1 body"
    meta = _stored_meta(env, conv)
    assert meta["prompt_library"] == {
        "prompt_id": prompt["id"], "version_id": v1["id"], "version_number": 1, "title": "Team reviewer",
    }
    assert meta["prompt_library_history"][0]["from_message_index"] == 0
    events = env["library"].repository.list_audit_events(prompt["id"])
    assert [e["event_type"] for e in events][0] == "use"
    assert events[0]["details"]["conversation_id"] == conv


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [OUTSIDER, USER])
async def test_crafted_frames_cannot_bypass_access(env, user):
    shared, _ = await _shared_prompt(env["library"])
    private = await env["library"].create_prompt(OWNER, "Private", "PRIVATE BODY")
    private_v = await _publish(env["library"], OWNER, private["id"])
    service, calls, _ = _service(env)
    frames = [
        {"prompt_id": private["id"], "version_id": None},
        {"prompt_id": private["id"], "version_id": private_v["id"]},
        # A version of a private prompt smuggled under an id the user may use.
        {"prompt_id": shared["id"], "version_id": private_v["id"]},
        {"prompt_id": "no-such-prompt", "version_id": None},
    ]
    if user == OUTSIDER:
        frames.append({"prompt_id": shared["id"], "version_id": None})
    for frame in frames:
        conv = str(uuid4())
        with pytest.raises(AuthorizationError) as exc:
            await _turn(service, uuid4(), conv, user=user, library_prompt=frame)
        assert "PRIVATE BODY" not in exc.value.message
        assert env["conversations"].get_conversation(conv, user) is None
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["prompt-id", ["x"], {"prompt_id": 5}, {"prompt_id": "a", "version_id": 7}, {}])
async def test_malformed_references_fail_the_turn(env, bad):
    service, calls, _ = _service(env)
    with pytest.raises(ValidationError):
        await _turn(service, uuid4(), str(uuid4()), library_prompt=bad)
    assert calls == []


@pytest.mark.asyncio
async def test_exact_version_survives_new_publications(env):
    lib = env["library"]
    prompt, v1 = await _shared_prompt(lib)
    service, calls, _ = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    latest = {"prompt_id": prompt["id"], "version_id": None}
    await _turn(service, session_id, conv, library_prompt=latest)

    await _edit(lib, OWNER, prompt["id"], content="v2 body")
    v2 = await _publish(lib, OWNER, prompt["id"], "second")

    # "Latest" in this conversation keeps v1; an old client omitting the field too.
    await _turn(service, session_id, conv, library_prompt=latest)
    await _turn(service, session_id, conv)
    assert [c["custom_system_prompt"] for c in calls] == ["v1 body"] * 3
    assert _stored_meta(env, conv)["prompt_library"]["version_id"] == v1["id"]

    # A brand-new conversation picks up v2.
    other = str(uuid4())
    await _turn(service, uuid4(), other, library_prompt=latest)
    assert calls[-1]["custom_system_prompt"] == "v2 body"

    # An explicit choice re-pins and is recorded in the history.
    await _turn(service, session_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": v2["id"]})
    assert calls[-1]["custom_system_prompt"] == "v2 body"
    meta = _stored_meta(env, conv)
    assert meta["prompt_library"]["version_id"] == v2["id"]
    assert [h["version_number"] for h in meta["prompt_library_history"]] == [1, 2]
    # One "use" audit per pin, not per turn.
    uses = [e for e in lib.repository.list_audit_events(prompt["id"]) if e["event_type"] == "use"]
    assert len(uses) == 3


@pytest.mark.asyncio
async def test_null_unpins_and_other_prompts_take_over(env):
    prompt, _ = await _shared_prompt(env["library"])
    service, calls, _ = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    await _turn(service, session_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    await _turn(service, session_id, conv, library_prompt=None)
    assert calls[-1].get("custom_system_prompt") is None
    assert "prompt_library" not in _stored_meta(env, conv)
    # Re-pin, then an inline custom prompt with the field omitted also unpins.
    await _turn(service, session_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    await _turn(service, session_id, conv, custom_system_prompt="my own")
    assert calls[-1]["custom_system_prompt"] == "my own"
    assert "prompt_library" not in _stored_meta(env, conv)


@pytest.mark.asyncio
async def test_restored_conversation_keeps_its_version(env):
    lib = env["library"]
    prompt, v1 = await _shared_prompt(lib)
    service, calls, _ = _service(env)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    await _edit(lib, OWNER, prompt["id"], content="v2 body")
    await _publish(lib, OWNER, prompt["id"])

    # A new replica/socket: fresh service and session, hydrated from the store.
    fresh, fresh_calls, _ = _service(env)
    await _turn(fresh, uuid4(), conv)
    assert fresh_calls[-1]["custom_system_prompt"] == "v1 body"

    # The sidebar restore path loads the pin too.
    restorer, restore_calls, sessions = _service(env)
    session_id = uuid4()
    await restorer.handle_restore_conversation(session_id, conv, [], user_email=USER)
    pin = sessions[session_id].context[library_turn.PIN_CONTEXT_KEY]
    assert pin["version_id"] == v1["id"]
    await restorer.handle_chat_message(
        session_id=session_id, content="again", model="m", user_email=USER,
        library_prompt={"prompt_id": prompt["id"], "version_id": None},
    )
    assert restore_calls[-1]["custom_system_prompt"] == "v1 body"


@pytest.mark.asyncio
async def test_revocation_blocks_new_use_but_history_remains(env):
    lib = env["library"]
    prompt, v1 = await _shared_prompt(lib)
    service, calls, _ = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    await _turn(service, session_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    env["members"].table["eng"].discard(USER)

    for frame in ({"library_prompt": {"prompt_id": prompt["id"], "version_id": None}}, {}):
        with pytest.raises(AuthorizationError):
            await _turn(service, session_id, conv, **frame)
    assert len(calls) == 1
    # The transcript and the pinned reference are untouched.
    stored = env["conversations"].get_conversation(conv, USER)
    assert stored["metadata"]["prompt_library"]["version_id"] == v1["id"]
    assert len(stored["messages"]) == 2
    # The user can still continue by explicitly choosing another prompt.
    await _turn(service, session_id, conv, library_prompt=None)
    assert calls[-1].get("custom_system_prompt") is None
    assert lib.repository.list_audit_events(prompt["id"])[0]["event_type"] in ("use_denied",)


@pytest.mark.asyncio
async def test_archived_prompt_is_not_runnable(env):
    lib = env["library"]
    prompt, _ = await _shared_prompt(lib)
    service, _, _ = _service(env)
    await lib.archive(OWNER, prompt["id"])
    with pytest.raises(AuthorizationError):
        await _turn(service, uuid4(), str(uuid4()), user=OWNER,
                    library_prompt={"prompt_id": prompt["id"], "version_id": None})


@pytest.mark.asyncio
async def test_feature_off_fails_closed(env):
    prompt, _ = await _shared_prompt(env["library"])
    service, calls, _ = _service(env, library=None)
    with pytest.raises(AuthorizationError):
        await _turn(service, uuid4(), str(uuid4()), library_prompt={"prompt_id": prompt["id"], "version_id": None})
    assert calls == []


@pytest.mark.asyncio
async def test_platform_admission_checks_still_run_first(env):
    prompt, _ = await _shared_prompt(env["library"])
    service, calls, _ = _service(env)
    resolve = AsyncMock(wraps=env["library"].resolve_for_execution)
    with patch.object(service, "check_turn_admission", AsyncMock(side_effect=ToolAuthorizationError("tool denied"))), \
         patch.object(env["library"], "resolve_for_execution", resolve):
        with pytest.raises(ToolAuthorizationError):
            await _turn(service, uuid4(), str(uuid4()), selected_tools=["secret_tool"],
                        library_prompt={"prompt_id": prompt["id"], "version_id": None})
    resolve.assert_not_called()
    assert calls == []
    # A library prompt changes only the system prompt: tool selection is passed through as sent.
    await _turn(service, uuid4(), str(uuid4()), selected_tools=["a_tool"],
                library_prompt={"prompt_id": prompt["id"], "version_id": None})
    assert calls[-1]["selected_tools"] == ["a_tool"]


@pytest.mark.asyncio
async def test_two_accounts_get_independent_pins(env):
    lib = env["library"]
    prompt, v1 = await _shared_prompt(lib)
    service, calls, _ = _service(env)
    conv_owner, conv_user = str(uuid4()), str(uuid4())
    await _turn(service, uuid4(), conv_owner, user=OWNER, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    await _edit(lib, OWNER, prompt["id"], content="v2 body")
    v2 = await _publish(lib, OWNER, prompt["id"])
    await _turn(service, uuid4(), conv_user, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    assert _stored_meta(env, conv_owner, OWNER)["prompt_library"]["version_id"] == v1["id"]
    assert _stored_meta(env, conv_user)["prompt_library"]["version_id"] == v2["id"]
    # Neither account can pin onto the other's conversation.
    with pytest.raises(Exception):
        await _turn(service, uuid4(), conv_owner, library_prompt={"prompt_id": prompt["id"], "version_id": None})


@pytest.mark.asyncio
async def test_saved_workspace_selection_is_rechecked_after_revocation(env):
    """A workspace stores only the library reference; using it re-checks access."""
    from atlas.modules.chat_history import WorkspaceRepository, get_session_factory

    lib = env["library"]
    prompt, v1 = await _shared_prompt(lib)
    workspaces = WorkspaceRepository(get_session_factory())
    key = f"libprompt:{prompt['id']}@{v1['id']}"
    ws = workspaces.create_workspace(USER, "Review", {"active_prompt_key": key})
    stored_key = workspaces.get_workspace(ws["id"], USER)["config"]["active_prompt_key"]
    assert stored_key == key and "v1 body" not in str(ws)

    prompt_id, version_id = stored_key[len("libprompt:"):].split("@")
    service, calls, _ = _service(env)
    await _turn(service, uuid4(), str(uuid4()), library_prompt={"prompt_id": prompt_id, "version_id": version_id})
    assert calls[-1]["custom_system_prompt"] == "v1 body"
    env["members"].table["eng"].discard(USER)
    with pytest.raises(AuthorizationError):
        await _turn(service, uuid4(), str(uuid4()), library_prompt={"prompt_id": prompt_id, "version_id": version_id})


def _pin_client(env, monkeypatch, members):
    """The /api/prompts router wired to this test's conversation store and library."""
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from atlas.core.log_sanitizer import get_current_user
    from atlas.infrastructure.app_factory import app_factory
    from atlas.routes import prompt_library_routes

    monkeypatch.setattr(prompt_library_routes, "_get_service", lambda: env["library"])
    monkeypatch.setattr(prompt_library_routes, "_library_enabled", lambda: True)
    monkeypatch.setattr(app_factory, "conversation_repository", env["conversations"], raising=False)
    app = FastAPI()
    app.include_router(prompt_library_routes.router)

    async def identity(request: Request) -> str:
        return request.headers["x-test-user"]

    app.dependency_overrides[get_current_user] = identity
    return TestClient(app)


@pytest.mark.asyncio
async def test_clients_that_omit_the_field_get_an_actionable_refusal(env):
    """CLI, follow-up/API runs and older bundles never send library_prompt."""
    prompt, _ = await _shared_prompt(env["library"])
    browser, calls, _ = _service(env)
    conv = str(uuid4())
    await _turn(browser, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    env["members"].table["eng"].discard(USER)
    cli, cli_calls, _ = _service(env)  # a fresh process/run hydrating from the store
    with pytest.raises(AuthorizationError) as exc:
        await _turn(cli, uuid4(), conv)
    assert f"DELETE /api/prompts/pins/{conv}" in exc.value.message
    assert "v1 body" not in exc.value.message and cli_calls == []
    # A browser naming the prompt still gets the generic refusal.
    with pytest.raises(AuthorizationError) as browser_exc:
        await _turn(browser, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    assert "DELETE" not in browser_exc.value.message


@pytest.mark.asyncio
async def test_owner_can_clear_an_unavailable_pin_and_continue(env, monkeypatch):
    lib = env["library"]
    prompt, v1 = await _shared_prompt(lib)
    browser, _, _ = _service(env)
    conv = str(uuid4())
    await _turn(browser, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    env["members"].table["eng"].discard(USER)
    client = _pin_client(env, monkeypatch, env["members"])

    # Only the conversation's owner can clear it; others get the not-found answer.
    other = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": OUTSIDER})
    assert other.status_code == 404
    unknown = client.delete(f"/api/prompts/pins/{uuid4()}", headers={"x-test-user": USER})
    assert unknown.status_code == 404
    assert _stored_meta(env, conv)["prompt_library"]["version_id"] == v1["id"]

    cleared = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})
    assert cleared.status_code == 200 and cleared.json() == {"cleared": True}
    meta = _stored_meta(env, conv)
    assert "prompt_library" not in meta
    assert meta["prompt_library_history"][0]["version_id"] == v1["id"]  # what ran stays recorded
    unpin = [e for e in lib.repository.list_audit_events(prompt["id"]) if e["event_type"] == "unpin"]
    assert unpin and unpin[0]["actor_email"] == USER and unpin[0]["details"] == {"conversation_id": conv}

    cli, cli_calls, _ = _service(env)
    await _turn(cli, uuid4(), conv)
    assert cli_calls[-1].get("custom_system_prompt") is None  # default prompt, explicitly chosen
    again = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})
    assert again.json() == {"cleared": False}


@pytest.mark.asyncio
async def test_storage_outage_refuses_without_dropping_the_pin(env, monkeypatch):
    from atlas.modules.prompts.prompt_library_policy import PromptLibraryUnavailable

    prompt, v1 = await _shared_prompt(env["library"])
    service, calls, sessions = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    await _turn(service, session_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})

    async def outage(*args, **kwargs):
        raise PromptLibraryUnavailable()

    monkeypatch.setattr(env["library"], "resolve_for_execution", outage)
    with pytest.raises(AuthorizationError) as exc:
        await _turn(service, session_id, conv)
    assert "temporarily unavailable" in exc.value.message
    assert sessions[session_id].context[library_turn.PIN_CONTEXT_KEY]["version_id"] == v1["id"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_pin_can_be_cleared_while_the_library_is_switched_off(env, monkeypatch):
    from atlas.routes import prompt_library_routes

    prompt, _ = await _shared_prompt(env["library"])
    browser, _, _ = _service(env)
    conv = str(uuid4())
    await _turn(browser, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    off, off_calls, _ = _service(env, library=None)  # feature disabled
    with pytest.raises(AuthorizationError) as exc:
        await _turn(off, uuid4(), conv)
    assert "/api/prompts/pins/" in exc.value.message
    client = _pin_client(env, monkeypatch, env["members"])
    monkeypatch.setattr(prompt_library_routes, "_library_enabled", lambda: False)
    cleared = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})
    assert cleared.status_code == 200 and cleared.json() == {"cleared": True}
    await _turn(off, uuid4(), conv)
    assert off_calls[-1].get("custom_system_prompt") is None


# --- Third review: conflicting prompts, live pin clearing, failed loads -----


def _set_execute(service, fn):
    """Replace the stub orchestrator's turn body (keeps the recorded calls)."""
    service._get_orchestrator.return_value.execute.side_effect = fn


@pytest.mark.asyncio
@pytest.mark.parametrize("pinned_version", [False, True])
async def test_conflicting_prompts_are_rejected_before_any_lookup(env, monkeypatch, pinned_version):
    lib = env["library"]
    prompt, v1 = await _shared_prompt(lib)
    events_before = len(lib.repository.list_audit_events(prompt["id"]))
    lookups = []
    real_resolve = lib.resolve_for_execution

    async def spy(*args, **kwargs):
        lookups.append(args)
        return await real_resolve(*args, **kwargs)

    monkeypatch.setattr(lib, "resolve_for_execution", spy)
    service, calls, _ = _service(env)
    conv = str(uuid4())
    ref = {"prompt_id": prompt["id"], "version_id": v1["id"] if pinned_version else None}
    with pytest.raises(ValidationError) as exc:
        await _turn(service, uuid4(), conv, library_prompt=ref, custom_system_prompt="client text")
    assert exc.value.message == library_turn.CONFLICTING_PROMPTS_MESSAGE
    assert calls == [] and lookups == []
    # Nothing was attempted, so nothing is audited (no use, no denial).
    assert len(lib.repository.list_audit_events(prompt["id"])) == events_before
    assert env["conversations"].get_conversation(conv, USER) is None


@pytest.mark.asyncio
async def test_conflicting_prompts_leave_an_existing_pin_alone(env):
    prompt, v1 = await _shared_prompt(env["library"])
    service, calls, sessions = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    ref = {"prompt_id": prompt["id"], "version_id": None}
    await _turn(service, session_id, conv, library_prompt=ref)
    with pytest.raises(ValidationError):
        await _turn(service, session_id, conv, library_prompt=ref, custom_system_prompt="client text")
    assert len(calls) == 1
    assert sessions[session_id].context[library_turn.PIN_CONTEXT_KEY]["version_id"] == v1["id"]
    assert _stored_meta(env, conv)["prompt_library"]["version_id"] == v1["id"]


@pytest.mark.parametrize(
    "library_prompt, custom, persona, conflict",
    [
        ({"prompt_id": "p"}, "text", None, True),
        ({"prompt_id": "p"}, None, "persona-1", True),
        ({"prompt_id": "p"}, "   ", None, False),  # blank inline text is no prompt
        ("malformed", "text", None, True),  # any non-null reference is a selection
        (None, "text", None, False),  # explicit null: the client chose the other prompt
        ("<omitted>", "text", "persona-1", False),
        ({"prompt_id": "p"}, None, None, False),
    ],
)
def test_raw_frame_conflict_check(library_prompt, custom, persona, conflict):
    omitted = object()
    value = omitted if library_prompt == "<omitted>" else library_prompt
    assert library_turn.frame_has_conflicting_prompts(value, custom, persona, omitted) is conflict


@pytest.mark.asyncio
async def test_clearing_a_pin_binds_a_session_that_is_already_open(env, monkeypatch):
    lib = env["library"]
    prompt, v1 = await _shared_prompt(lib)
    service, calls, sessions = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    await _turn(service, session_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    uses_before = [e for e in lib.repository.list_audit_events(prompt["id"]) if e["event_type"] == "use"]

    client = _pin_client(env, monkeypatch, env["members"])
    cleared = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})
    assert cleared.json() == {"cleared": True}

    # The same session -- still holding the pin in memory -- runs unpinned.
    await _turn(service, session_id, conv)
    assert calls[-1].get("custom_system_prompt") is None
    assert sessions[session_id].context[library_turn.PIN_CONTEXT_KEY] is None
    meta = _stored_meta(env, conv)
    assert "prompt_library" not in meta
    assert meta["prompt_library_pin_revision"] == 2  # set (1), owner clear (2)
    assert meta["prompt_library_history"][0]["version_id"] == v1["id"]
    uses_after = [e for e in lib.repository.list_audit_events(prompt["id"]) if e["event_type"] == "use"]
    assert len(uses_after) == len(uses_before)


@pytest.mark.asyncio
async def test_a_clear_during_a_running_turn_is_not_undone_by_its_save(env):
    prompt, v1 = await _shared_prompt(env["library"])
    service, calls, _ = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    await _turn(service, session_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})

    async def clear_mid_turn(**kwargs):
        calls.append(kwargs)
        # The turn already resolved the pin; the owner clears it before the save.
        removed = env["conversations"].clear_prompt_pin(conv, USER)
        assert removed is not None
        session = kwargs["session"]
        session.history.add_message(Message(role=MessageRole.USER, content=kwargs["content"]))
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="reply"))
        return {"type": "done"}

    _set_execute(service, clear_mid_turn)
    await _turn(service, session_id, conv)
    assert calls[-1]["custom_system_prompt"] == "v1 body"  # resolved before the clear
    assert "prompt_library" not in _stored_meta(env, conv)  # ... but not written back
    assert len(env["conversations"].get_conversation(conv, USER)["messages"]) == 4

    async def plain(**kwargs):
        calls.append(kwargs)
        session = kwargs["session"]
        session.history.add_message(Message(role=MessageRole.USER, content=kwargs["content"]))
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="reply"))
        return {"type": "done"}

    _set_execute(service, plain)
    await _turn(service, session_id, conv)
    assert calls[-1].get("custom_system_prompt") is None
    assert "prompt_library" not in _stored_meta(env, conv)


@pytest.mark.asyncio
async def test_choosing_the_prompt_again_after_a_clear_pins_the_current_version(env):
    lib = env["library"]
    prompt, v1 = await _shared_prompt(lib)
    service, calls, _ = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    ref = {"prompt_id": prompt["id"], "version_id": None}
    await _turn(service, session_id, conv, library_prompt=ref)
    await _edit(lib, OWNER, prompt["id"], content="v2 body")
    v2 = await _publish(lib, OWNER, prompt["id"], "second")
    env["conversations"].clear_prompt_pin(conv, USER)
    # This session decided on the pin it last saw (revision 1); the owner's
    # clear moved it to 2. The first re-selection is refused as stale and
    # the session adopts the cleared state; the next one is informed.
    from atlas.domain.conversation_prompt_pin import PromptPinConflictError

    with pytest.raises(PromptPinConflictError) as exc:
        await _turn(service, session_id, conv, library_prompt=ref)
    assert exc.value.pin_state["pin"] is None and exc.value.pin_state["revision"] == 2
    assert len(calls) == 1
    # "Latest" no longer means "the version I had pinned".
    await _turn(service, session_id, conv, library_prompt=ref)
    assert calls[-1]["custom_system_prompt"] == "v2 body"
    meta = _stored_meta(env, conv)
    assert meta["prompt_library"]["version_id"] == v2["id"]
    assert meta["prompt_library_pin_revision"] == 3


@pytest.fixture
def no_compliance(monkeypatch):
    monkeypatch.setattr(ChatService, "_compliance_enabled", lambda self: False)


async def _session_on_other_conversation(env, prompt):
    """A pinned conversation B (saved by one session) and a second session on A."""
    pinner, _, _ = _service(env)
    conv_b = str(uuid4())
    await _turn(pinner, uuid4(), conv_b, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    service, calls, sessions = _service(env)
    session_id, conv_a = uuid4(), str(uuid4())
    await _turn(service, session_id, conv_a)
    return service, calls, sessions, session_id, conv_b


def _fail(*args, **kwargs):
    raise RuntimeError("store unavailable")


@pytest.mark.asyncio
async def test_failed_load_never_runs_a_pinned_conversation_on_the_default_prompt(env, monkeypatch, no_compliance):
    prompt, v1 = await _shared_prompt(env["library"])
    service, calls, sessions, session_id, conv_b = await _session_on_other_conversation(env, prompt)
    store = env["conversations"]
    monkeypatch.setattr(store, "get_conversation", _fail)
    monkeypatch.setattr(store, "get_conversation_metadata", _fail)
    with pytest.raises(AuthorizationError) as exc:
        await _turn(service, session_id, conv_b)
    assert exc.value.message == library_turn.PIN_STATE_UNAVAILABLE_MESSAGE
    assert len(calls) == 1  # only the earlier turn on A ran
    # An explicit choice of another prompt is refused too while the pin is unknown.
    with pytest.raises(AuthorizationError):
        await _turn(service, session_id, conv_b, custom_system_prompt="other prompt")
    assert len(calls) == 1
    monkeypatch.undo()
    assert _stored_meta(env, conv_b)["prompt_library"]["version_id"] == v1["id"]


@pytest.mark.asyncio
async def test_failed_load_adopts_the_stored_pin_when_it_can_be_read(env, monkeypatch, no_compliance):
    prompt, v1 = await _shared_prompt(env["library"])
    service, calls, sessions, session_id, conv_b = await _session_on_other_conversation(env, prompt)
    monkeypatch.setattr(env["conversations"], "get_conversation", _fail)
    await _turn(service, session_id, conv_b)
    assert calls[-1]["custom_system_prompt"] == "v1 body"  # the pinned version, not the default
    assert sessions[session_id].context[library_turn.PIN_CONTEXT_KEY]["version_id"] == v1["id"]


@pytest.mark.asyncio
async def test_failed_load_still_enforces_revoked_access(env, monkeypatch, no_compliance):
    prompt, _ = await _shared_prompt(env["library"])
    service, calls, _, session_id, conv_b = await _session_on_other_conversation(env, prompt)
    env["members"].table["eng"].discard(USER)
    monkeypatch.setattr(env["conversations"], "get_conversation", _fail)
    with pytest.raises(AuthorizationError) as exc:
        await _turn(service, session_id, conv_b)
    assert f"DELETE /api/prompts/pins/{conv_b}" in exc.value.message
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_turns_recover_once_the_store_answers_again(env, monkeypatch, no_compliance):
    prompt, v1 = await _shared_prompt(env["library"])
    service, calls, _, session_id, conv_b = await _session_on_other_conversation(env, prompt)
    store = env["conversations"]
    with monkeypatch.context() as patched:
        patched.setattr(store, "get_conversation", _fail)
        patched.setattr(store, "get_conversation_metadata", _fail)
        with pytest.raises(AuthorizationError):
            await _turn(service, session_id, conv_b)
    await _turn(service, session_id, conv_b)
    assert calls[-1]["custom_system_prompt"] == "v1 body"
    assert _stored_meta(env, conv_b)["prompt_library"]["version_id"] == v1["id"]


@pytest.mark.asyncio
async def test_a_failed_reload_of_the_same_conversation_rechecks_a_cleared_pin(env, monkeypatch, no_compliance):
    """Same conversation, empty history (reload retried), pin cleared meanwhile."""
    prompt, v1 = await _shared_prompt(env["library"])
    service, calls, sessions, = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    await _turn(service, session_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    env["conversations"].clear_prompt_pin(conv, USER)
    sessions[session_id].history.messages.clear()  # forces the reload attempt
    monkeypatch.setattr(env["conversations"], "get_conversation", _fail)
    await _turn(service, session_id, conv)
    assert calls[-1].get("custom_system_prompt") is None  # the clear is honoured


# --- Review of 69036b8a: direct persona conflicts, unknown pin + no record --


@pytest.mark.asyncio
async def test_direct_service_calls_reject_a_library_prompt_with_a_persona(env, monkeypatch):
    """ChatService callers other than the WebSocket get the same rejection."""
    lib = env["library"]
    prompt, v1 = await _shared_prompt(lib)
    service, calls, sessions = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    ref = {"prompt_id": prompt["id"], "version_id": None}
    await _turn(service, session_id, conv, library_prompt=ref)
    events_before = lib.repository.list_audit_events(prompt["id"])
    meta_before = _stored_meta(env, conv)
    lookups = []

    async def spy(*args, **kwargs):
        lookups.append(args)
        raise AssertionError("no library lookup expected")

    monkeypatch.setattr(lib, "resolve_for_execution", spy)
    for frame in (ref, {"prompt_id": prompt["id"], "version_id": v1["id"]}):
        with pytest.raises(ValidationError) as exc:
            await _turn(service, session_id, conv, library_prompt=frame, persona_id="any-persona")
        assert exc.value.message == library_turn.CONFLICTING_PROMPTS_MESSAGE
    assert len(calls) == 1 and lookups == []  # only the first, pinning turn ran
    assert lib.repository.list_audit_events(prompt["id"]) == events_before
    assert sessions[session_id].context[library_turn.PIN_CONTEXT_KEY]["version_id"] == v1["id"]
    assert _stored_meta(env, conv) == meta_before


def _record_missing(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_unknown_pin_with_no_metadata_record_is_refused_not_defaulted(env, monkeypatch, no_compliance):
    """A failed full load followed by "no record" is not proof of a new conversation."""
    prompt, v1 = await _shared_prompt(env["library"])
    service, calls, sessions, session_id, conv_b = await _session_on_other_conversation(env, prompt)
    store = env["conversations"]
    with monkeypatch.context() as patched:
        patched.setattr(store, "get_conversation", _fail)
        patched.setattr(store, "get_conversation_metadata", _record_missing)
        with pytest.raises(AuthorizationError) as exc:
            await _turn(service, session_id, conv_b)
        assert exc.value.message == library_turn.PIN_STATE_UNAVAILABLE_MESSAGE
        with pytest.raises(AuthorizationError):  # stays refused while unsettled
            await _turn(service, session_id, conv_b)
    assert len(calls) == 1  # only the earlier turn on A reached the model
    assert sessions[session_id].context.get(library_turn.PIN_UNKNOWN_CONTEXT_KEY) is True
    assert _stored_meta(env, conv_b)["prompt_library"]["version_id"] == v1["id"]
    # Once the full load works, the stored pin -- not the default -- runs.
    await _turn(service, session_id, conv_b)
    assert calls[-1]["custom_system_prompt"] == "v1 body"
    assert sessions[session_id].context.get(library_turn.PIN_UNKNOWN_CONTEXT_KEY) is None


@pytest.mark.asyncio
async def test_new_conversation_after_a_failed_load_proceeds_once_the_store_answers(env, monkeypatch, no_compliance):
    service, calls, sessions = _service(env)
    session_id = uuid4()
    await _turn(service, session_id, str(uuid4()))
    new_conv = str(uuid4())
    store = env["conversations"]
    with monkeypatch.context() as patched:
        patched.setattr(store, "get_conversation", _fail)
        patched.setattr(store, "get_conversation_metadata", _record_missing)
        with pytest.raises(AuthorizationError):
            await _turn(service, session_id, new_conv)
    # The retried full load finds no record: a genuinely new conversation.
    await _turn(service, session_id, new_conv)
    assert calls[-1].get("custom_system_prompt") is None
    assert sessions[session_id].context.get(library_turn.PIN_UNKNOWN_CONTEXT_KEY) is None


@pytest.mark.asyncio
async def test_genuinely_new_conversations_are_unaffected(env):
    service, calls, sessions = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    await _turn(service, session_id, conv)
    await _turn(service, session_id, conv)
    assert len(calls) == 2 and all(c.get("custom_system_prompt") is None for c in calls)
    assert library_turn.PIN_UNKNOWN_CONTEXT_KEY not in sessions[session_id].context


# --- Pin revisions: sets and clears are compare-and-set (review of ab2fe60b) --


async def _two_sessions_on_pinned(env, prompt, version_id=None):
    a, a_calls, a_sessions = _service(env)
    a_id, conv = uuid4(), str(uuid4())
    await _turn(a, a_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": version_id})
    b, b_calls, b_sessions = _service(env)
    b_id = uuid4()
    await _turn(b, b_id, conv)  # hydrates the pinned conversation (revision 1)
    return (a, a_id, a_calls, a_sessions), (b, b_id, b_calls, b_sessions), conv


@pytest.mark.asyncio
async def test_two_sessions_selecting_different_versions_do_not_overwrite_each_other(env):
    from atlas.domain.conversation_prompt_pin import PromptPinConflictError

    lib = env["library"]
    prompt, v1 = await _shared_prompt(lib)
    await _edit(lib, OWNER, prompt["id"], content="v2 body")
    v2 = await _publish(lib, OWNER, prompt["id"], "second")
    (a, a_id, a_calls, _), (b, b_id, b_calls, b_sessions), conv = await _two_sessions_on_pinned(env, prompt, v1["id"])
    assert _stored_meta(env, conv)["prompt_library_pin_revision"] == 1
    await _turn(a, a_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": v2["id"]})
    assert a_calls[-1]["custom_system_prompt"] == "v2 body"
    # B decided on revision 1 (v1) and now picks v1 explicitly: refused, not applied.
    with pytest.raises(PromptPinConflictError) as exc:
        await _turn(b, b_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": v1["id"]})
    assert exc.value.pin_state["pin"]["version_id"] == v2["id"] and exc.value.pin_state["revision"] == 2
    assert b_sessions[b_id].context[library_turn.PIN_CONTEXT_KEY]["version_id"] == v2["id"]
    meta = _stored_meta(env, conv)
    assert meta["prompt_library"]["version_id"] == v2["id"] and meta["prompt_library_pin_revision"] == 2
    # Informed now, B's choice applies.
    await _turn(b, b_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": v1["id"]})
    assert b_calls[-1]["custom_system_prompt"] == "v1 body"
    meta = _stored_meta(env, conv)
    assert meta["prompt_library"]["version_id"] == v1["id"] and meta["prompt_library_pin_revision"] == 3
    assert [h["version_id"] for h in meta["prompt_library_history"]] == [v1["id"], v2["id"], v1["id"]]


@pytest.mark.asyncio
async def test_explicit_null_is_a_coordinated_clear(env):
    from atlas.domain.conversation_prompt_pin import PromptPinConflictError

    prompt, v1 = await _shared_prompt(env["library"])
    (a, a_id, a_calls, a_sessions), (b, b_id, b_calls, _), conv = await _two_sessions_on_pinned(env, prompt)
    await _turn(b, b_id, conv, library_prompt=None)  # B deliberately clears (revision 2)
    assert b_calls[-1].get("custom_system_prompt") is None
    meta = _stored_meta(env, conv)
    assert "prompt_library" not in meta and meta["prompt_library_pin_revision"] == 2
    assert meta["prompt_library_history"][-1]["prompt_id"] is None
    # A still holds v1 in memory: an omitted-field turn follows the store...
    await _turn(a, a_id, conv)
    assert a_calls[-1].get("custom_system_prompt") is None
    # ... and re-selecting v1 on its old view is refused once.
    a_sessions[a_id].context[library_turn.PIN_REVISION_CONTEXT_KEY] = 1  # A's view before the clear
    with pytest.raises(PromptPinConflictError):
        await _turn(a, a_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": v1["id"]})
    assert "prompt_library" not in _stored_meta(env, conv)


@pytest.mark.asyncio
async def test_a_session_that_never_saw_the_pin_runs_and_saves_under_it(env):
    prompt, v1 = await _shared_prompt(env["library"])
    b, b_calls, b_sessions = _service(env)
    b_id, conv = uuid4(), str(uuid4())
    await _turn(b, b_id, conv)  # unpinned conversation, revision 0
    a, _, _ = _service(env)
    await _turn(a, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    # B (never saw the pin) continues with a client that sends nothing.
    await _turn(b, b_id, conv)
    assert b_calls[-1]["custom_system_prompt"] == "v1 body"  # not the default
    meta = _stored_meta(env, conv)
    assert meta["prompt_library"]["version_id"] == v1["id"] and meta["prompt_library_pin_revision"] == 1
    # Its save of a session that holds no pin cannot drop the stored one.
    b_sessions[b_id].context[library_turn.PIN_CONTEXT_KEY] = None
    assert b._save_conversation(b_sessions[b_id], USER, "test-model") is True
    assert _stored_meta(env, conv)["prompt_library"]["version_id"] == v1["id"]


@pytest.mark.asyncio
async def test_a_browser_that_never_saw_the_pin_cannot_clear_it_blindly(env):
    from atlas.domain.conversation_prompt_pin import PromptPinConflictError

    prompt, v1 = await _shared_prompt(env["library"])
    b, b_calls, _ = _service(env)
    b_id, conv = uuid4(), str(uuid4())
    await _turn(b, b_id, conv, library_prompt=None)  # browser, nothing selected (revision 0)
    a, _, _ = _service(env)
    await _turn(a, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    with pytest.raises(PromptPinConflictError) as exc:
        await _turn(b, b_id, conv, library_prompt=None)
    assert exc.value.pin_state["pin"]["version_id"] == v1["id"]
    assert len(b_calls) == 1 and _stored_meta(env, conv)["prompt_library"]["version_id"] == v1["id"]


@pytest.mark.asyncio
async def test_a_reconnected_client_with_an_old_view_is_refused(env):
    """A new socket hydrates fresh state; the frame's revision is what the
    client actually saw."""
    from atlas.domain.conversation_prompt_pin import PromptPinConflictError

    lib = env["library"]
    prompt, v1 = await _shared_prompt(lib)
    await _edit(lib, OWNER, prompt["id"], content="v2 body")
    v2 = await _publish(lib, OWNER, prompt["id"], "second")
    a, _, _ = _service(env)
    conv = str(uuid4())
    await _turn(a, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": v1["id"]})
    await _turn(a, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": v2["id"]},
                library_prompt_revision=1)
    fresh, calls, _ = _service(env)
    with pytest.raises(PromptPinConflictError):
        await _turn(fresh, uuid4(), conv, library_prompt=None, library_prompt_revision=1)
    assert calls == [] and _stored_meta(env, conv)["prompt_library"]["version_id"] == v2["id"]
    await _turn(fresh, uuid4(), conv, library_prompt=None, library_prompt_revision=2)
    assert "prompt_library" not in _stored_meta(env, conv)
    with pytest.raises(ValidationError):
        await _turn(fresh, uuid4(), conv, library_prompt=None, library_prompt_revision="2")


@pytest.mark.asyncio
async def test_revoked_access_cannot_set_a_pin(env):
    lib = env["library"]
    prompt, v1 = await _shared_prompt(lib)
    service, calls, _ = _service(env)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv)
    env["members"].table["eng"].discard(USER)
    with pytest.raises(AuthorizationError):
        await _turn(service, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": v1["id"]})
    meta = _stored_meta(env, conv)
    assert "prompt_library" not in meta and "prompt_library_pin_revision" not in meta and len(calls) == 1


@pytest.mark.asyncio
async def test_conversation_saved_frames_carry_the_pin_revision(env):
    prompt, v1 = await _shared_prompt(env["library"])
    service, _, _ = _service(env)
    frames = []

    async def collect(frame):
        frames.append(frame)

    conv = str(uuid4())
    await _turn(service, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None},
                update_callback=collect)
    saved = [f for f in frames if f.get("type") == "conversation_saved"][-1]
    assert saved["prompt_library_pin_revision"] == 1 and saved["prompt_library"]["version_id"] == v1["id"]


# --- Fourth review ----------------------------------------------------------


@pytest.mark.asyncio
async def test_incognito_turns_in_a_stored_conversation_never_conflict_or_write_the_pin(env):
    """An off-record turn cannot persist a pin change, so it must not record one.

    Recording it only in the session made the next turn (which re-reads the
    stored record) see a revision it never observed and refuse a conflict.
    """
    prompt, v1 = await _shared_prompt(env["library"])
    service, calls, sessions = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    await _turn(service, session_id, conv)  # stored, unpinned
    stored_before = _stored_meta(env, conv)
    ref = {"prompt_id": prompt["id"], "version_id": None}
    for _ in range(3):
        await _turn(service, session_id, conv, library_prompt=ref, incognito=True)
        assert calls[-1]["custom_system_prompt"] == "v1 body"
    assert _stored_meta(env, conv) == stored_before
    assert sessions[session_id].context[library_turn.PIN_CONTEXT_KEY] is None
    # Back on the record, the selection is made and stored as usual.
    await _turn(service, session_id, conv, library_prompt=ref, incognito=False)
    assert _stored_meta(env, conv)["prompt_library"]["version_id"] == v1["id"]


def _set_raw_metadata(env, conversation_id, raw):
    from atlas.modules.chat_history.models import ConversationRecord

    with env["conversations"]._session_factory() as db:
        db.get(ConversationRecord, conversation_id).metadata_json = raw
        db.commit()


def _raw_metadata(env, conversation_id):
    from atlas.modules.chat_history.models import ConversationRecord

    with env["conversations"]._session_factory() as db:
        return db.get(ConversationRecord, conversation_id).metadata_json


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["{not json", "[1, 2]", '"text"', "42"])
async def test_unreadable_metadata_refuses_the_turn_and_is_left_alone(env, monkeypatch, raw):
    """Finding 5: corrupt metadata never reads as "no team prompt"."""
    prompt, v1 = await _shared_prompt(env["library"])
    service, calls, sessions = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    await _turn(service, session_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    good = _raw_metadata(env, conv)
    _set_raw_metadata(env, conv, raw)
    events_before = len(env["library"].repository.list_audit_events(prompt["id"]))

    for frame in ({}, {"library_prompt": None}, {"library_prompt": {"prompt_id": prompt["id"], "version_id": None}}):
        with pytest.raises(AuthorizationError) as exc:
            await _turn(service, session_id, conv, **frame)
        assert exc.value.message == library_turn.PIN_STATE_UNAVAILABLE_MESSAGE
    # A fresh connection (full load) is refused too. The conversation
    # classification gate reads the same blob and refuses first (it fails
    # closed on unreadable metadata even with compliance levels off); either
    # way nothing runs on the default prompt.
    fresh, fresh_calls, _ = _service(env)
    with pytest.raises((AuthorizationError, ValidationError)):
        await _turn(fresh, uuid4(), conv)
    assert len(calls) == 1 and fresh_calls == []  # nothing ran on the default prompt
    assert _raw_metadata(env, conv) == raw  # never rewritten
    assert len(env["library"].repository.list_audit_events(prompt["id"])) == events_before

    # Once the record is readable again, the stored pin runs -- not the default.
    _set_raw_metadata(env, conv, good)
    await _turn(service, session_id, conv)
    assert calls[-1]["custom_system_prompt"] == "v1 body"
    await _turn(fresh, uuid4(), conv)
    assert fresh_calls[-1]["custom_system_prompt"] == "v1 body"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, "", "null", "{}"])
async def test_genuinely_empty_metadata_is_not_an_error(env, raw):
    service, calls, _ = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    await _turn(service, session_id, conv)
    _set_raw_metadata(env, conv, raw)
    fresh, fresh_calls, _ = _service(env)
    await _turn(fresh, uuid4(), conv)
    assert fresh_calls[-1].get("custom_system_prompt") is None


@pytest.mark.asyncio
async def test_a_metadata_read_failure_refuses_the_turn(env, monkeypatch):
    prompt, _ = await _shared_prompt(env["library"])
    service, calls, _ = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    await _turn(service, session_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})

    def db_down(*args, **kwargs):
        from sqlalchemy.exc import OperationalError

        raise OperationalError("SELECT", {}, Exception("connection refused"))

    with monkeypatch.context() as patched:
        patched.setattr(env["conversations"], "get_conversation_metadata", db_down)
        with pytest.raises(AuthorizationError) as exc:
            await _turn(service, session_id, conv)
        assert exc.value.message == library_turn.PIN_STATE_UNAVAILABLE_MESSAGE
    assert len(calls) == 1
    await _turn(service, session_id, conv)
    assert calls[-1]["custom_system_prompt"] == "v1 body"


@pytest.mark.asyncio
async def test_restoring_a_conversation_with_unreadable_metadata_leaves_its_pin_unknown(env):
    prompt, _ = await _shared_prompt(env["library"])
    service, calls, sessions = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    await _turn(service, session_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    _set_raw_metadata(env, conv, "{broken")
    other_id = uuid4()
    other, other_calls, other_sessions = _service(env)
    await _turn(other, other_id, str(uuid4()))  # a session on some other chat
    await other.handle_restore_conversation(other_id, conv, [], user_email=USER)
    assert other_sessions[other_id].context.get(library_turn.PIN_UNKNOWN_CONTEXT_KEY) is True
    with pytest.raises((AuthorizationError, ValidationError)):  # pin layer and classification gate
        await _turn(other, other_id, conv)
    assert len(other_calls) == 1


@pytest.mark.asyncio
async def test_owner_clear_on_unreadable_metadata_is_refused_not_guessed(env, monkeypatch):
    prompt, _ = await _shared_prompt(env["library"])
    service, _, _ = _service(env)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    _set_raw_metadata(env, conv, "{broken")
    client = _pin_client(env, monkeypatch, env["members"])
    response = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})
    assert response.status_code == 503
    assert _raw_metadata(env, conv) == "{broken"


# --- Finding 3: live (in-flight) metadata carries the pin --------------------


@pytest.mark.asyncio
async def test_live_view_of_a_running_turn_carries_the_pin_and_matches_the_saved_record(env, monkeypatch):
    from atlas.application.chat import runs as runs_module
    from atlas.application.chat.runs import RunRegistry
    from atlas.application.chat.runs.in_flight import in_flight_conversation

    prompt, v1 = await _shared_prompt(env["library"])
    registry = RunRegistry()
    monkeypatch.setattr(runs_module, "get_run_registry", lambda: registry)
    service, calls, sessions = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    observer_id = uuid4()
    observer, observer_calls, _ = _service(env)
    # Every connection shares one session store in production.
    observer.session_repository = service.session_repository
    observer_sessions = sessions
    await _turn(observer, observer_id, str(uuid4()))  # another tab, on another chat
    seen = {}

    async def run_turn(**kwargs):
        calls.append(kwargs)
        session = kwargs["session"]
        session.history.add_message(Message(role=MessageRole.USER, content=kwargs["content"]))
        # The turn is running and nothing is stored yet: the live view is all
        # another tab can open.
        record = registry.start(conversation_id=conv, user_email=USER, session_id=session.id)
        live = await in_flight_conversation(service.session_repository, registry, conv, USER)
        seen["live"] = live["metadata"]
        await observer.handle_restore_conversation(observer_id, conv, [], user_email=USER)
        seen["observer"] = dict(observer_sessions[observer_id].context)
        registry.cancel(record.run_id, USER)
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="reply"))
        return {"type": "done"}

    _set_execute(service, run_turn)
    await _turn(service, session_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})

    live = seen["live"]
    assert live["prompt_library"]["version_id"] == v1["id"]
    assert live["prompt_library_pin_revision"] == 1
    assert live["prompt_library_history"][-1]["version_id"] == v1["id"]
    # The tab that opened the live view adopted the same pin and revision.
    assert seen["observer"][library_turn.PIN_CONTEXT_KEY]["version_id"] == v1["id"]
    assert seen["observer"][library_turn.PIN_REVISION_CONTEXT_KEY] == 1
    # Active -> persisted: the saved record says exactly what the live view did.
    stored = _stored_meta(env, conv)
    for key in ("prompt_library", "prompt_library_pin_revision", "prompt_library_history"):
        assert stored[key] == live[key]
    # Resumed: the observing tab continues under the pin it saw, no conflict.
    await _turn(observer, observer_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None},
                library_prompt_revision=live["prompt_library_pin_revision"])
    assert observer_calls[-1]["custom_system_prompt"] == "v1 body"


@pytest.mark.asyncio
async def test_live_view_of_an_unpinned_turn_has_no_pin_keys(env):
    from atlas.application.chat.runs.in_flight import _live_metadata

    service, _, sessions = _service(env)
    session_id = uuid4()
    await _turn(service, session_id, str(uuid4()))
    metadata = _live_metadata(sessions[session_id])
    assert "prompt_library" not in metadata and "prompt_library_pin_revision" not in metadata


@pytest.mark.asyncio
async def test_unsaved_conversations_change_their_pin_without_spurious_conflicts(env):
    """A chat the server never stores (browser-only or not saved) has no stored
    revision to protect: the revision a client sends for it is not checked."""
    lib = env["library"]
    prompt, v1 = await _shared_prompt(lib)
    await _edit(lib, OWNER, prompt["id"], content="v2 body")
    v2 = await _publish(lib, OWNER, prompt["id"], "second")
    service, calls, sessions = _service(env)
    session_id, conv = uuid4(), f"local_{uuid4()}"
    # A conversation reopened from browser storage reports revision 0.
    for version, body in ((v1, "v1 body"), (v2, "v2 body"), (v1, "v1 body")):
        await _turn(service, session_id, conv, incognito=True, library_prompt_revision=0,
                    library_prompt={"prompt_id": prompt["id"], "version_id": version["id"]})
        assert calls[-1]["custom_system_prompt"] == body
    await _turn(service, session_id, conv, incognito=True, library_prompt=None, library_prompt_revision=0)
    assert calls[-1].get("custom_system_prompt") is None
    assert env["conversations"].get_conversation(conv, USER) is None
