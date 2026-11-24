"""Regression tests for the November 24 review of the team prompt library (issue #1169).

One section per finding. Each test reproduces the reported behaviour on the
pre-review tree (dad0e688) and pins the corrected one. Finding 4 (grant
replacement racing a revocation) turned out not to be reachable through the
service -- every grant change bumps the sharing revision the write is
compared against -- so its first test documents that, and the second pins the
in-transaction re-check added as defense in depth.
"""

import asyncio
import json
import time
from uuid import uuid4

import pytest
from sqlalchemy import text

from atlas.domain.errors import AuthorizationError
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.prompts.prompt_library_service import PromptLibraryService
from atlas.tests.test_prompt_library_chat_turn import (
    OUTSIDER,
    OWNER,
    USER,
    Members,
    _pin_client,
    _publish,
    _service,
    _shared_prompt,
    _stored_meta,
    _team_text,
    _turn,
)


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

# --- Finding 1: a session's own pin change is not a conflict ----------------


async def _two_prompts(lib):
    first, _ = await _shared_prompt(lib, "first body")
    second = await lib.create_prompt(OWNER, "Second", "second body")
    await _publish(lib, OWNER, second["id"])
    sharing = await lib.get_sharing(OWNER, second["id"])
    await lib.set_sharing(OWNER, second["id"], ["eng"], [], sharing["revision"])
    return first, second


@pytest.mark.asyncio
async def test_a_change_after_this_sessions_own_unreported_change_is_not_a_conflict(env):
    """Turn 2 changes the pin and then fails before the save that would have
    reported the new revision. Turn 3 (same session, the client still holding
    the revision it last heard) is the user's own next choice, not a change
    made elsewhere."""
    first, second = await _two_prompts(env["library"])
    service, calls, _ = _service(env)
    session, conv = uuid4(), str(uuid4())
    await _turn(service, session, conv, library_prompt={"prompt_id": first["id"], "version_id": None})
    seen = _stored_meta(env, conv)["prompt_library_pin_revision"]

    original = service._get_orchestrator.return_value.execute.side_effect

    async def fails(**kwargs):
        raise RuntimeError("model unavailable")

    service._get_orchestrator.return_value.execute.side_effect = fails
    failed = await _turn(service, session, conv, library_prompt={"prompt_id": second["id"], "version_id": None},
                         library_prompt_revision=seen)
    assert failed.get("type") == "error"
    # The pin change committed before the model ran; no save reported it.
    assert _stored_meta(env, conv)["prompt_library"]["prompt_id"] == second["id"]
    service._get_orchestrator.return_value.execute.side_effect = original

    await _turn(service, session, conv, library_prompt={"prompt_id": first["id"], "version_id": None},
                library_prompt_revision=seen)
    assert _team_text(calls[-1]) == "first body"
    assert _stored_meta(env, conv)["prompt_library"]["prompt_id"] == first["id"]


@pytest.mark.asyncio
async def test_another_sessions_change_is_still_a_conflict(env):
    from atlas.domain.conversation_prompt_pin import PromptPinConflictError

    first, second = await _two_prompts(env["library"])
    service, _, _ = _service(env)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv, library_prompt={"prompt_id": first["id"], "version_id": None})
    seen = _stored_meta(env, conv)["prompt_library_pin_revision"]
    other, _, _ = _service(env)
    await _turn(other, uuid4(), conv, library_prompt={"prompt_id": second["id"], "version_id": None},
                library_prompt_revision=seen)
    with pytest.raises(PromptPinConflictError):
        await _turn(service, uuid4(), conv, library_prompt={"prompt_id": first["id"], "version_id": None},
                    library_prompt_revision=seen)


@pytest.mark.asyncio
async def test_this_sessions_own_change_then_anothers_is_a_conflict(env):
    """The allowance covers only this session's last write: once someone else
    has written after it, the stale view is refused again."""
    from atlas.domain.conversation_prompt_pin import PromptPinConflictError

    first, second = await _two_prompts(env["library"])
    service, _, _ = _service(env)
    session, conv = uuid4(), str(uuid4())
    await _turn(service, session, conv, library_prompt={"prompt_id": first["id"], "version_id": None})
    seen = _stored_meta(env, conv)["prompt_library_pin_revision"]
    await _turn(service, session, conv, library_prompt={"prompt_id": second["id"], "version_id": None},
                library_prompt_revision=seen)
    other, _, _ = _service(env)
    now = _stored_meta(env, conv)["prompt_library_pin_revision"]
    await _turn(other, uuid4(), conv, library_prompt=None, library_prompt_revision=now)
    with pytest.raises(PromptPinConflictError):
        await _turn(service, session, conv, library_prompt={"prompt_id": first["id"], "version_id": None},
                    library_prompt_revision=seen)


@pytest.mark.asyncio
async def test_the_committed_revision_is_published_as_soon_as_the_pin_changes(env):
    """The browser learns the new revision when the change commits, not only
    from the end-of-turn save (which a long agent run delays and a failed turn
    never sends)."""
    first, second = await _two_prompts(env["library"])
    service, _, _ = _service(env)
    session, conv = uuid4(), str(uuid4())
    await _turn(service, session, conv, library_prompt={"prompt_id": first["id"], "version_id": None})
    seen = _stored_meta(env, conv)["prompt_library_pin_revision"]
    frames = []

    async def collect(frame):
        frames.append(frame)

    order = []
    original = service._get_orchestrator.return_value.execute.side_effect

    async def runs(**kwargs):
        order.append(("run", len(frames)))
        return await original(**kwargs)

    service._get_orchestrator.return_value.execute.side_effect = runs
    await _turn(service, session, conv, library_prompt={"prompt_id": second["id"], "version_id": None},
                library_prompt_revision=seen, update_callback=collect)
    pin_frames = [f for f in frames if f.get("type") == "prompt_pin_state"]
    assert len(pin_frames) == 1
    stored = _stored_meta(env, conv)
    assert pin_frames[0]["conversation_id"] == conv
    assert pin_frames[0]["prompt_library_pin_revision"] == stored["prompt_library_pin_revision"] == seen + 1
    assert pin_frames[0]["prompt_library"]["prompt_id"] == second["id"]
    # Sent before the model runs.
    assert order == [("run", frames.index(pin_frames[0]) + 1)]


# --- Finding 2: team prompt + multimodal user message ------------------------


def _strict(messages):
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    return LiteLLMCaller._enforce_strict_role_ordering(messages)


def test_strict_ordering_folds_the_team_block_into_a_multimodal_user_message():
    from atlas.domain.team_prompt import TeamPrompt

    team = TeamPrompt(text="Be brief.", title="Style").message()
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    user = {"role": "user", "content": [{"type": "text", "text": "What is in this picture?"}, image]}
    out = _strict([{"role": "system", "content": "platform"}, team, user])
    assert [m["role"] for m in out] == ["system", "user"]
    content = out[1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": team["content"]}
    assert content[1:] == user["content"]  # the image block survives, in place


def test_strict_ordering_still_folds_plain_text_and_leaves_other_messages_alone():
    from atlas.domain.team_prompt import TeamPrompt

    team = TeamPrompt(text="Be brief.").message()
    out = _strict([{"role": "system", "content": "platform"}, team, {"role": "user", "content": "hi"}])
    assert [m["role"] for m in out] == ["system", "user"]
    assert out[1]["content"].endswith("\n\nhi") and out[1]["content"].startswith(team["content"])
    plain = [{"role": "user", "content": "a"}, {"role": "user", "content": [{"type": "text", "text": "b"}]}]
    assert _strict(plain) == plain


# --- Finding 3: metadata read failure with the library disabled -------------


async def _stored_unpinned_conversation(env):
    service, _, _ = _service(env, library=None)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv)
    return conv


def _failing_reads(env, monkeypatch, marker=None):
    """The metadata read fails (a transient storage error); the marker read
    answers ``marker`` (or fails too when ``marker`` is an exception)."""
    repo = env["conversations"]

    def fail(*args, **kwargs):
        raise RuntimeError("database read failed")

    monkeypatch.setattr(repo, "get_conversation_metadata", fail)
    if marker is not None:
        if isinstance(marker, Exception):
            def raise_marker(*args, **kwargs):
                raise marker
            monkeypatch.setattr(repo, "conversation_was_pinned", raise_marker)
        else:
            monkeypatch.setattr(repo, "conversation_was_pinned", lambda *a, **k: marker)


@pytest.mark.asyncio
async def test_library_off_a_failed_read_of_a_never_pinned_conversation_still_runs(env, monkeypatch):
    conv = await _stored_unpinned_conversation(env)
    _failing_reads(env, monkeypatch)
    service, calls, _ = _service(env, library=None)
    await _turn(service, uuid4(), conv)
    assert calls and _team_text(calls[-1]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("library_on", [False, True])
async def test_a_failed_read_of_a_pinned_conversation_stays_refused(env, monkeypatch, library_on):
    prompt, _ = await _shared_prompt(env["library"])
    service, _, _ = _service(env)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    _failing_reads(env, monkeypatch)
    fresh, calls, _ = _service(env, library="default" if library_on else None)
    with pytest.raises(AuthorizationError):
        await _turn(fresh, uuid4(), conv)
    assert calls == []


@pytest.mark.asyncio
async def test_a_failed_read_with_an_unreadable_marker_stays_refused(env, monkeypatch):
    conv = await _stored_unpinned_conversation(env)
    _failing_reads(env, monkeypatch, marker=RuntimeError("marker read failed"))
    service, calls, _ = _service(env, library=None)
    with pytest.raises(AuthorizationError):
        await _turn(service, uuid4(), conv)
    assert calls == []


# --- Finding 4: grant replacement and a revocation ---------------------------


async def _managed(lib):
    prompt = await lib.create_prompt(OWNER, "Managed", "body")
    await _publish(lib, OWNER, prompt["id"])
    sharing = await lib.get_sharing(OWNER, prompt["id"])
    await lib.set_sharing(OWNER, prompt["id"], [], [{"email": USER, "permission": "manage"}], sharing["revision"])
    return prompt


@pytest.mark.asyncio
async def test_a_manager_revoked_through_sharing_cannot_replace_grants_afterwards(env):
    """Not reachable before the fix either: the owner's revocation bumps the
    sharing revision, and the manager's write is compared against the
    revision it read (documents why the reported race is a false positive
    through the service)."""
    from atlas.modules.prompts.prompt_library_policy import PromptConflict

    lib = env["library"]
    prompt = await _managed(lib)
    seen = (await lib.get_sharing(USER, prompt["id"]))["revision"]
    original = lib._repo.replace_grants

    def revoke_first(prompt_id, grants, actor, expected):
        if actor == USER:
            owner_view = lib._repo.get_sharing_state(prompt_id)
            original(prompt_id, [], OWNER, owner_view[0])  # the owner revokes the manager
        return original(prompt_id, grants, actor, expected)

    lib._repo.replace_grants = revoke_first
    with pytest.raises(PromptConflict):
        await lib.set_sharing(USER, prompt["id"], [], [{"email": USER, "permission": "manage"},
                                                       {"email": OUTSIDER, "permission": "use"}], seen)
    assert (await lib.get_sharing(OWNER, prompt["id"]))["users"] == []


@pytest.mark.asyncio
async def test_grant_replacement_rechecks_manage_permission_in_the_transaction(env):
    """Defense in depth: a manage grant removed without a revision bump (a
    write outside this service, or a future path that forgets the bump) still
    stops the replacement inside the transaction."""
    from atlas.modules.chat_history.database import get_session_factory
    from atlas.modules.prompts.prompt_library_policy import PromptNotFound

    lib = env["library"]
    prompt = await _managed(lib)
    seen = (await lib.get_sharing(USER, prompt["id"]))["revision"]
    original = lib._repo.replace_grants

    def revoke_without_bump(prompt_id, grants, actor, expected):
        with get_session_factory()() as session:
            session.execute(text("DELETE FROM prompt_library_grants WHERE prompt_id = :p AND grantee = :u"),
                            {"p": prompt_id, "u": USER})
            session.commit()
        return original(prompt_id, grants, actor, expected)

    lib._repo.replace_grants = revoke_without_bump
    with pytest.raises(PromptNotFound):
        await lib.set_sharing(USER, prompt["id"], [], [{"email": USER, "permission": "manage"},
                                                       {"email": OUTSIDER, "permission": "use"}], seen)
    lib._repo.replace_grants = original
    users = (await lib.get_sharing(OWNER, prompt["id"]))["users"]
    assert all(u["email"] != OUTSIDER for u in users)


# --- Finding 5: bounded group checks when a prompt runs ----------------------


class SlowGroups:
    """An authorizer whose ``hang`` groups answer only when released."""

    def __init__(self, members, hang=(), delay=0.0, delays=None):
        self.members, self.hang, self.delay, self.calls = members, set(hang), delay, []
        self.delays = dict(delays or {})
        self._released = None

    async def check(self, user, group):
        self.calls.append(group)
        if group in self.hang:
            if self._released is None:
                self._released = asyncio.Event()
            await self._released.wait()
        if self.delay or group in self.delays:
            await asyncio.sleep(self.delays.get(group, self.delay))
        return user in self.members.get(group, set())

    async def release(self):
        """Let hung checks finish (they outlive the callers that gave up)."""
        if self._released is not None:
            self._released.set()
        for _ in range(50):
            await asyncio.sleep(0)


def _library(tmp_path, groups, candidates):
    from atlas.modules.chat_history import PromptLibraryRepository, get_session_factory, init_database

    init_database(f"duckdb:///{tmp_path / 'exec.db'}")
    return PromptLibraryService(PromptLibraryRepository(get_session_factory()), group_check=groups.check,
                                group_candidates=lambda: list(candidates))


async def _shared_with(lib, groups):
    prompt = await lib.create_prompt(OWNER, "Wide", "body")
    await _publish(lib, OWNER, prompt["id"])
    sharing = await lib.get_sharing(OWNER, prompt["id"])
    await lib.set_sharing(OWNER, prompt["id"], groups, [], sharing["revision"])
    return prompt


@pytest.mark.asyncio
async def test_a_hung_group_check_cannot_stall_a_turn(tmp_path, monkeypatch):
    from atlas.modules.prompts import prompt_library_service as svc
    from atlas.modules.prompts.prompt_library_policy import PromptMembershipUnknown

    names = [f"g{i:02d}" for i in range(6)]
    owner_in_all = {g: {OWNER} for g in names}
    groups = SlowGroups(owner_in_all)
    lib = _library(tmp_path, groups, names)
    prompt = await _shared_with(lib, names)
    groups.hang = {"g03"}
    monkeypatch.setattr(svc, "PROMPT_GROUP_CHECK_TIMEOUT_S", 0.3, raising=False)
    started = time.monotonic()
    with pytest.raises(PromptMembershipUnknown):
        await asyncio.wait_for(lib.resolve_for_execution(USER, prompt["id"]), 5)
    assert time.monotonic() - started < 2
    await groups.release()


@pytest.mark.asyncio
async def test_one_confirmed_group_is_enough_to_run(tmp_path, monkeypatch):
    from atlas.modules.prompts import prompt_library_service as svc

    names = [f"g{i:02d}" for i in range(6)]
    members = {g: {OWNER} for g in names}
    groups = SlowGroups(members)
    lib = _library(tmp_path, groups, names)
    prompt = await _shared_with(lib, names)
    members["g00"].add(USER)
    groups.hang = {g for g in names if g != "g00"}
    monkeypatch.setattr(svc, "PROMPT_GROUP_CHECK_TIMEOUT_S", 5.0, raising=False)
    started = time.monotonic()
    version = await asyncio.wait_for(lib.resolve_for_execution(USER, prompt["id"]), 5)
    assert version["prompt_id"] == prompt["id"]
    assert time.monotonic() - started < 2
    await groups.release()


@pytest.mark.asyncio
async def test_stopping_early_on_one_prompt_does_not_fail_another_sharing_the_check(tmp_path, monkeypatch):
    """Independent-review follow-up: P stops at its first confirmed group and
    no longer needs g01; Q, running at the same time, needs exactly that check.
    Q must get the real answer, not "unknown"."""
    from atlas.modules.prompts import prompt_library_service as svc

    names = ["g00", "g01"]
    members = {g: {OWNER} for g in names}
    groups = SlowGroups(members)
    lib = _library(tmp_path, groups, names)
    p = await _shared_with(lib, names)
    q = await _shared_with(lib, ["g01"])
    members["g00"].add(USER)
    members["g01"].add(USER)
    groups.hang = {"g01"}
    groups.delays = {"g00": 0.2}
    monkeypatch.setattr(svc, "PROMPT_GROUP_CHECK_TIMEOUT_S", 5.0, raising=False)
    # P starts first, so the g01 check is P's; Q joins it while P still waits
    # for g00. P then confirms g00 and stops waiting for g01.
    p_task = asyncio.create_task(lib.resolve_for_execution(USER, p["id"]))
    await asyncio.sleep(0.05)
    q_task = asyncio.create_task(lib.resolve_for_execution(USER, q["id"]))
    p_version = await asyncio.wait_for(p_task, 2)
    assert p_version["prompt_id"] == p["id"]
    await groups.release()
    q_version = await asyncio.wait_for(q_task, 2)
    assert q_version["prompt_id"] == q["id"]


@pytest.mark.asyncio
async def test_group_checks_for_one_prompt_are_capped(tmp_path, monkeypatch):
    from atlas.modules.prompts import prompt_library_service as svc
    from atlas.modules.prompts.prompt_library_policy import PromptMembershipUnknown

    names = [f"g{i:02d}" for i in range(12)]
    groups = SlowGroups({g: {OWNER} for g in names})
    lib = _library(tmp_path, groups, names)
    prompt = await _shared_with(lib, names)
    groups.calls.clear()
    monkeypatch.setattr(svc, "MAX_PROMPT_GROUP_CHECKS", 5, raising=False)
    with pytest.raises(PromptMembershipUnknown):
        await lib.resolve_for_execution(USER, prompt["id"])
    assert len(groups.calls) == 5


@pytest.mark.asyncio
async def test_a_confirmed_non_member_is_still_a_plain_refusal(tmp_path):
    from atlas.modules.prompts.prompt_library_policy import PromptMembershipUnknown, PromptUnavailable

    names = ["g00", "g01"]
    groups = SlowGroups({g: {OWNER} for g in names})
    lib = _library(tmp_path, groups, names)
    prompt = await _shared_with(lib, names)
    with pytest.raises(PromptUnavailable) as exc:
        await lib.resolve_for_execution(USER, prompt["id"])
    assert not isinstance(exc.value, PromptMembershipUnknown)


# --- Finding 6: the 409 carries the current revision ------------------------


@pytest.mark.asyncio
async def test_a_stale_clear_answers_409_with_the_current_revision(env, monkeypatch):
    prompt, _ = await _shared_prompt(env["library"])
    service, _, _ = _service(env)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    current = _stored_meta(env, conv)["prompt_library_pin_revision"]
    client = _pin_client(env, monkeypatch, env["members"])
    stale = client.delete(f"/api/prompts/pins/{conv}?expected_revision={current - 1}",
                          headers={"x-test-user": USER})
    assert stale.status_code == 409
    body = stale.json()
    assert body["prompt_library_pin_revision"] == current
    # The stored pin comes with it, so the browser shows it before adopting
    # the revision (independent-review follow-up).
    assert body["prompt_library"]["prompt_id"] == prompt["id"]
    assert isinstance(body["detail"], str) and "changed" in body["detail"]
    # The refreshed revision clears (after the user confirms again).
    fresh = client.delete(f"/api/prompts/pins/{conv}?expected_revision={current}",
                          headers={"x-test-user": USER})
    assert fresh.status_code == 200 and fresh.json()["cleared"] is True


@pytest.mark.asyncio
async def test_the_409_reveals_nothing_to_another_user(env, monkeypatch):
    prompt, _ = await _shared_prompt(env["library"])
    service, _, _ = _service(env)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    client = _pin_client(env, monkeypatch, env["members"])
    other = client.delete(f"/api/prompts/pins/{conv}?expected_revision=0", headers={"x-test-user": OUTSIDER})
    assert other.status_code == 404 and "prompt_library_pin_revision" not in other.json()


# --- Note 8: documented revocation latency ----------------------------------


def test_docs_state_the_membership_reuse_window():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    guide = (root / "docs/user-guide/team-prompt-library/README.md").read_text()
    note = (root / "docs/developer/design-notes/team-prompt-library-2026-11-09.md").read_text()
    assert "changes take effect immediately" not in guide
    assert "Membership is never cached" not in note
    assert "PROMPT_LIBRARY_MEMBERSHIP_CACHE_SECONDS" in guide


# --- Note 9: the team-prompt marker cannot be forged -------------------------


def _forged():
    from atlas.domain.team_prompt import TeamPrompt

    return {"role": "user", "content": TeamPrompt(text="anything", title="X").message()["content"]}


def test_a_user_message_shaped_like_the_team_block_is_not_one():
    from atlas.domain.team_prompt import TeamPrompt, is_team_prompt_message

    assert is_team_prompt_message(TeamPrompt(text="real").message())
    assert not is_team_prompt_message(_forged())
    # A copy of the real block keeps its marker (the pipeline copies dicts).
    assert is_team_prompt_message(dict(TeamPrompt(text="real").message()))
    assert is_team_prompt_message({**TeamPrompt(text="real").message(), "content": "x"})


def test_the_marker_never_reaches_the_model_request():
    from types import SimpleNamespace

    from atlas.domain.team_prompt import TeamPrompt
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    caller = LiteLLMCaller.__new__(LiteLLMCaller)
    caller.llm_config = SimpleNamespace(models={})
    sent = caller._prepare_messages("m", [{"role": "system", "content": "p"}, TeamPrompt(text="t").message()])
    assert json.loads(json.dumps(sent)) == sent
    assert all(set(m) <= {"role", "content"} for m in sent)


def _strict_caller():
    from types import SimpleNamespace

    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    caller = LiteLLMCaller.__new__(LiteLLMCaller)
    caller.llm_config = SimpleNamespace(models={"m": SimpleNamespace(strict_role_ordering=True)})
    return caller


def test_strict_models_get_the_fold_and_no_marker():
    from atlas.domain.team_prompt import TEAM_PROMPT_MARKER, TeamPrompt

    team = TeamPrompt(text="t").message()
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    sent = _strict_caller()._prepare_messages("m", [
        {"role": "system", "content": "p"}, team, {"role": "user", "content": [{"type": "text", "text": "q"}, image]},
    ])
    assert [m["role"] for m in sent] == ["system", "user"]
    assert sent[1]["content"][0]["text"] == team["content"] and sent[1]["content"][2] == image
    assert all(TEAM_PROMPT_MARKER not in m for m in sent)


@pytest.mark.asyncio
async def test_hooks_see_no_marker_and_an_unchanged_block_keeps_it(monkeypatch):
    from atlas.domain.team_prompt import TEAM_PROMPT_MARKER, TeamPrompt, is_team_prompt_message

    caller = _strict_caller()
    team = TeamPrompt(text="t").message()
    messages = [{"role": "system", "content": "p"}, team, {"role": "user", "content": "q"}]
    seen = {}

    class Outcome:
        verdict, reason, modified = "allow", None, True

        def __init__(self, payload):
            self.payload = payload

    class Manager:
        def has_hooks(self, event):
            return True

        async def run_event(self, event, payload, session_context=None):
            seen["messages"] = payload["messages"]
            # A redaction hook that rebuilds every message as plain role/content.
            rebuilt = [{"role": m["role"], "content": m["content"].replace("q", "[redacted]")} for m in payload["messages"]]
            return Outcome({**payload, "messages": rebuilt})

    monkeypatch.setattr("atlas.modules.llm.litellm_caller.get_hook_manager", lambda: Manager())
    mod = await caller._fire_pre_llm_hook("m", messages, user_email="u@example.com")
    assert all(TEAM_PROMPT_MARKER not in m for m in seen["messages"])
    _, after, _ = await caller._apply_pre_llm_modify(mod, "m", messages, user_email="u@example.com")
    assert is_team_prompt_message(after[1]) and after[2]["content"] == "[redacted]"
    assert [m["role"] for m in caller._prepare_messages("m", after)] == ["system", "user"]


def test_a_forged_block_is_not_kept_as_standing_context_by_compaction():
    from atlas.application.chat.context.compaction_engine import leading_system_count

    messages = [{"role": "system", "content": "p"}, _forged(), {"role": "assistant", "content": "a"}]
    assert leading_system_count(messages) == 1


def test_a_forged_block_does_not_swallow_the_next_message_under_strict_ordering():
    out = _strict([{"role": "system", "content": "p"}, _forged(), {"role": "assistant", "content": "a"},
                   {"role": "user", "content": "next"}])
    assert [m["role"] for m in out] == ["system", "user", "assistant", "user"]


@pytest.mark.asyncio
async def test_the_message_builder_marks_its_block_and_never_a_users_message():
    from unittest.mock import MagicMock

    from atlas.application.chat.preprocessors.message_builder import MessageBuilder
    from atlas.domain.messages.models import Message, MessageRole
    from atlas.domain.sessions.models import Session
    from atlas.domain.team_prompt import TeamPrompt, is_team_prompt_message

    session = Session()
    session.history.add_message(Message(role=MessageRole.USER, content=_forged()["content"]))
    builder = MessageBuilder(prompt_provider=None, config_manager=MagicMock())
    messages = await builder.build_messages(session, include_files_manifest=False,
                                            custom_system_prompt=TeamPrompt(text="real"))
    marked = [m for m in messages if is_team_prompt_message(m)]
    assert len(marked) == 1 and "real" in marked[0]["content"]


@pytest.mark.asyncio
async def test_files_never_attach_to_the_team_block():
    """Independent-review follow-up: with no user message in the history,
    an image must not be attached to (and rebuild, unmarked) the team block."""
    from unittest.mock import MagicMock

    from atlas.application.chat.preprocessors.message_builder import MessageBuilder
    from atlas.domain.sessions.models import Session
    from atlas.domain.team_prompt import TeamPrompt, is_team_prompt_message

    session = Session()
    session.context["files"] = {"a.png": {"image_b64": "AAAA", "image_mime_type": "image/png"}}
    builder = MessageBuilder(prompt_provider=None, config_manager=MagicMock())
    messages = await builder.build_messages(session, include_files_manifest=False, model_supports_vision=True,
                                            custom_system_prompt=TeamPrompt(text="real"))
    block = [m for m in messages if m.get("role") == "user"]
    assert len(block) == 1 and is_team_prompt_message(block[0]) and isinstance(block[0]["content"], str)


def test_a_hook_cannot_get_a_user_copy_of_the_block_marked():
    """Second independent-review follow-up: re-marking after a hook goes by
    position, so a user message that copies the block's exact text is never
    marked, even when a hook drops the real block."""
    from atlas.domain.team_prompt import TeamPrompt, is_team_prompt_message, restore_team_prompt_marker

    team = TeamPrompt(text="t").message()
    copy = {"role": "user", "content": team["content"]}
    original = [{"role": "system", "content": "p"}, team, {"role": "assistant", "content": "a"}, copy]
    plain = [{"role": m["role"], "content": m["content"]} for m in original]
    kept = restore_team_prompt_marker(original, plain)
    assert is_team_prompt_message(kept[1]) and not is_team_prompt_message(kept[3])
    dropped = restore_team_prompt_marker(original, [plain[0], plain[2], plain[3]])  # hook removed the block
    assert not any(is_team_prompt_message(m) for m in dropped)
