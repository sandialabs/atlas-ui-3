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
            # Inline text riding along must not become the fallback either.
            await _turn(service, uuid4(), conv, user=user, library_prompt=frame,
                        custom_system_prompt="client text")
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
