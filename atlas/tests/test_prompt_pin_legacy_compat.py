"""Legacy conversation compatibility with damaged metadata (issue #1169, review finding 1).

Before the library existed (``main`` at the PR's merge base), a conversation
whose stored metadata could not be read was refused when *restored* -- the
data-classification gate fails closed on it -- but a session that already
had it open, or a client continuing it by id, kept working. This branch had
made every turn refuse, with the library on or off, because the pin could not
be read.

A pin is now recorded separately (``conversation_prompt_pin_markers``, written
in the same transaction as the pin). With unreadable metadata:

* no marker (never pinned, and the blob never mentions the library): the pin
  layer stands aside; everything else behaves exactly as on ``main``;
* a marker (has or had a pin): still fail closed, with owner recovery.
"""

import json
from uuid import uuid4

import pytest

from atlas.domain.errors import AuthorizationError, ValidationError
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.prompts.prompt_library_service import PromptLibraryService
from atlas.tests.test_prompt_library_chat_turn import (
    OUTSIDER,
    OWNER,
    USER,
    Members,
    _pin_client,
    _raw_metadata,
    _service,
    _set_raw_metadata,
    _share,
    _shared_prompt,
    _turn,
)


def _outcome(response):
    """The clear's outcome fields (the response also carries the stored pin revision)."""
    return {k: v for k, v in response.json().items() if k in ("cleared", "recovered")}


DAMAGED = ["{not json", "[1, 2]", '"text"', "42"]


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
    return {"conversations": ConversationRepository(factory), "library": library, "members": members,
            "factory": factory}


def _marker_count(env, conv):
    from atlas.modules.chat_history.models import ConversationPromptPinMarker

    with env["factory"]() as db:
        return db.query(ConversationPromptPinMarker).filter_by(conversation_id=conv).count()


# --- library disabled, legacy (never pinned) conversation, damaged metadata --------


@pytest.mark.parametrize("raw", DAMAGED)
@pytest.mark.asyncio
async def test_an_open_session_keeps_working_with_the_library_off(env, raw):
    service, calls, _ = _service(env, library=None)
    session_id, conv = uuid4(), str(uuid4())
    await _turn(service, session_id, conv)
    _set_raw_metadata(env, conv, raw)
    await _turn(service, session_id, conv)  # as on main: the open session continues
    assert len(calls) == 2 and calls[-1].get("custom_system_prompt") is None
    assert _raw_metadata(env, conv) == raw  # and nothing rewrote the record


@pytest.mark.parametrize("raw", DAMAGED)
@pytest.mark.parametrize("library_on", [True, False])
@pytest.mark.asyncio
async def test_other_paths_behave_exactly_as_on_main(env, raw, library_on):
    """On main (the PR's merge base), a new session continuing the conversation
    by id, a restore, and a turn after the restore are handled by the
    data-classification gate: the restore succeeds and the turns are refused
    for the unreadable classification record. Same here -- never for a pin."""
    library = "default" if library_on else None
    service, _, _ = _service(env, library=library)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv)
    _set_raw_metadata(env, conv, raw)

    fresh, calls, _ = _service(env, library=library)
    with pytest.raises(ValidationError) as by_id:
        await _turn(fresh, uuid4(), conv)
    restored, restored_calls, _ = _service(env, library=library)
    sid = uuid4()
    frame = await restored.handle_restore_conversation(sid, conv, [], user_email=USER)
    assert frame.get("type") == "conversation_restored"
    with pytest.raises(ValidationError) as after_restore:
        await _turn(restored, sid, conv)
    for refusal in (by_id, after_restore):
        assert "compliance level could not be read" in refusal.value.message
        assert "prompt" not in refusal.value.message.lower()
    assert calls == [] and restored_calls == []
    assert _raw_metadata(env, conv) == raw


@pytest.mark.asyncio
async def test_the_owner_clear_leaves_a_never_pinned_record_untouched(env, monkeypatch):
    service, _, _ = _service(env)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv)
    _set_raw_metadata(env, conv, "{not json")
    client = _pin_client(env, monkeypatch, env["members"])
    response = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})
    assert response.status_code == 200 and _outcome(response) == {"cleared": False}
    assert _raw_metadata(env, conv) == "{not json"


# --- pinned conversations stay fail closed --------------------------------------


@pytest.mark.parametrize("raw", DAMAGED)
@pytest.mark.parametrize("library_on", [True, False])
@pytest.mark.asyncio
async def test_a_damaged_previously_pinned_conversation_never_falls_back(env, raw, library_on):
    prompt, _ = await _shared_prompt(env["library"])
    service, _, _ = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    await _turn(service, session_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    assert _marker_count(env, conv) == 1
    _set_raw_metadata(env, conv, raw)
    # The open session (as on main it would have continued) is refused for the pin.
    open_service, open_calls, _ = service, None, None
    with pytest.raises(AuthorizationError) as exc:
        await _turn(open_service, session_id, conv)
    assert f"/api/prompts/pins/{conv}" in exc.value.message
    del open_calls
    other, other_calls, _ = _service(env, library="default" if library_on else None)
    with pytest.raises((AuthorizationError, ValidationError)):
        await _turn(other, uuid4(), conv)
    assert other_calls == []


@pytest.mark.asyncio
async def test_a_pin_cleared_long_ago_still_counts_as_possibly_pinned(env):
    prompt, _ = await _shared_prompt(env["library"])
    service, calls, _ = _service(env)
    session_id, conv = uuid4(), str(uuid4())
    await _turn(service, session_id, conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    await _turn(service, session_id, conv, library_prompt=None)  # cleared; history remains
    _set_raw_metadata(env, conv, "{not json")
    ran = len(calls)
    with pytest.raises(AuthorizationError):  # the open session: refused for the pin
        await _turn(service, session_id, conv)
    assert len(calls) == ran


@pytest.mark.asyncio
async def test_a_damaged_blob_naming_the_library_without_a_marker_stays_fail_closed(env):
    service, calls, _ = _service(env)
    sid, conv = uuid4(), str(uuid4())
    await _turn(service, sid, conv)
    _set_raw_metadata(env, conv, '{"prompt_library": {"prompt_id": "p"')  # truncated
    assert _marker_count(env, conv) == 0
    with pytest.raises(AuthorizationError):  # the open session: refused for the pin
        await _turn(service, sid, conv)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_library_on_valid_unpinned_and_pinned_conversations(env):
    prompt, v1 = await _shared_prompt(env["library"])
    service, calls, _ = _service(env)
    plain, pinned = str(uuid4()), str(uuid4())
    await _turn(service, uuid4(), plain)
    await _turn(service, uuid4(), pinned, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    assert _marker_count(env, plain) == 0 and _marker_count(env, pinned) == 1
    fresh, fresh_calls, _ = _service(env)
    await _turn(fresh, uuid4(), plain)
    await _turn(fresh, uuid4(), pinned)
    assert fresh_calls[0].get("custom_system_prompt") is None
    assert fresh_calls[1].get("library_system_prompt") is not None or fresh_calls[1].get("custom_system_prompt")


@pytest.mark.asyncio
async def test_library_off_valid_pinned_conversation_is_refused_not_run_on_the_default(env):
    prompt, _ = await _shared_prompt(env["library"])
    service, _, _ = _service(env)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    off, calls, _ = _service(env, library=None)
    with pytest.raises(AuthorizationError) as exc:
        await _turn(off, uuid4(), conv)
    assert f"/api/prompts/pins/{conv}" in exc.value.message and calls == []


@pytest.mark.asyncio
async def test_revoked_access_on_a_pinned_conversation_is_refused(env):
    prompt, _ = await _shared_prompt(env["library"])
    service, _, _ = _service(env)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    await _share(env["library"], OWNER, prompt["id"], [], [])
    fresh, calls, _ = _service(env)
    with pytest.raises(AuthorizationError):
        await _turn(fresh, uuid4(), conv)
    assert calls == []


@pytest.mark.asyncio
async def test_owner_recovery_of_a_pinned_damaged_conversation_and_others_get_404(env, monkeypatch):
    prompt, _ = await _shared_prompt(env["library"])
    service, _, _ = _service(env)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    meta = json.loads(_raw_metadata(env, conv))
    meta["prompt_library_pin_revision"] = "x"
    _set_raw_metadata(env, conv, json.dumps(meta))
    client = _pin_client(env, monkeypatch, env["members"])
    outsider = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": OUTSIDER})
    assert outsider.status_code == 404
    response = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})
    assert _outcome(response) == {"cleared": True, "recovered": True}
    fresh, calls, _ = _service(env)
    await _turn(fresh, uuid4(), conv)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_markers_are_written_with_the_pin_and_removed_with_the_conversation(env):
    prompt, _ = await _shared_prompt(env["library"])
    service, _, _ = _service(env)
    first_turn, later = str(uuid4()), str(uuid4())
    # Pinned on the turn that creates the record, and on a later turn.
    await _turn(service, uuid4(), first_turn, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    sid = uuid4()
    await _turn(service, sid, later)
    assert _marker_count(env, later) == 0
    await _turn(service, sid, later, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    assert _marker_count(env, first_turn) == 1 and _marker_count(env, later) == 1
    assert env["conversations"].delete_conversation(later, USER)
    assert _marker_count(env, later) == 0
