"""Owner recovery of damaged team-prompt pin state (issue #1169, review nit 1).

A stored conversation whose metadata is unreadable -- invalid JSON, JSON that
is not an object, or an object whose pin or pin revision is malformed --
refuses every turn and save, so its pin state is never guessed. Before this
change the owner's ``DELETE /api/prompts/pins/{id}`` refused it too (503),
leaving no way to continue. Now the owner's explicit clear resets the pin
state, under the row lock, without guessing a pin and without weakening
anything else:

* only the owner can do it (others get the same 404 as an unknown id);
* every other readable key is kept (data classification, workspace, ...);
* a blob that is not an object at all keeps an *invalid* classification, so
  the conversation still cannot run under any level;
* the new revision cannot match anything a client saw before the damage;
* the damaged values are quarantined, not deleted, and survive later saves;
* a turn on a damaged record says how to recover instead of "try again".
"""

import json
from uuid import uuid4

import pytest

from atlas.domain.conversation_prompt_pin import pin_fields_readable
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
    _shared_prompt,
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

# Spelled out (not imported) so these tests also run, and fail, on the code
# before the fix.
PIN_RECOVERY_METADATA_KEY = "prompt_library_recovery"
UNKNOWN_CLASSIFICATION_RECORD = {"state": "unknown", "reason": "unreadable_metadata"}
NOT_OBJECTS = ["{broken", "[]", '"CUI"', "42", "true"]
# (label, change applied to a readable pinned blob)
MALFORMED = [
    ("pin without version_id", lambda m: m.update(prompt_library={"prompt_id": m["prompt_library"]["prompt_id"]})),
    ("pin is a string", lambda m: m.update(prompt_library="p-1")),
    ("pin is a list", lambda m: m.update(prompt_library=[1, 2])),
    ("blank ids", lambda m: m.update(prompt_library={"prompt_id": " ", "version_id": ""})),
    ("revision is a string", lambda m: m.update(prompt_library_pin_revision="3")),
    ("revision is negative", lambda m: m.update(prompt_library_pin_revision=-1)),
    ("revision is a bool", lambda m: m.update(prompt_library_pin_revision=True)),
    ("legacy epoch malformed", lambda m: m.update(prompt_library_pin_epoch=1.5)),
]


async def _pinned_conversation(env):
    prompt, v1 = await _shared_prompt(env["library"])
    service, _, _ = _service(env)
    conv = str(uuid4())
    await _turn(service, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None})
    meta = json.loads(_raw_metadata(env, conv))
    assert meta["prompt_library"]["version_id"] == v1["id"]
    return prompt, conv, meta


# --- the defect: a damaged record could not be recovered ---------------------


@pytest.mark.parametrize("raw", NOT_OBJECTS)
@pytest.mark.asyncio
async def test_owner_recovers_metadata_that_is_not_an_object(env, monkeypatch, raw):
    _, conv, _ = await _pinned_conversation(env)
    _set_raw_metadata(env, conv, raw)
    client = _pin_client(env, monkeypatch, env["members"])

    response = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})
    assert response.status_code == 200
    assert response.json() == {"cleared": True, "recovered": True}

    stored = json.loads(_raw_metadata(env, conv))
    assert pin_fields_readable(stored) and "prompt_library" not in stored
    # Nothing in the blob can be trusted, so its classification is unknown --
    # invalid, as it was before, never legacy -- and the original is kept.
    assert stored["data_classification"] == UNKNOWN_CLASSIFICATION_RECORD
    assert stored[PIN_RECOVERY_METADATA_KEY]["original_metadata_json"] == raw
    record = env["conversations"].get_conversation(conv, USER)
    assert record["data_classification_state"] == "invalid"
    assert not record.get("metadata_unreadable")

    # Still no turn: the classification gate refuses it (no default-prompt run).
    service, calls, _ = _service(env)
    with pytest.raises((AuthorizationError, ValidationError)):
        await _turn(service, uuid4(), conv)
    assert calls == []


@pytest.mark.parametrize("label,damage", MALFORMED, ids=[m[0] for m in MALFORMED])
@pytest.mark.asyncio
async def test_owner_recovers_a_malformed_pin_and_keeps_everything_else(env, monkeypatch, label, damage):
    _, conv, meta = await _pinned_conversation(env)
    meta["workspace_id"] = "ws-keep"
    meta["custom_key"] = {"nested": [1, 2]}
    history_before = meta.get("prompt_library_history")
    damage(meta)
    raw = json.dumps(meta)
    _set_raw_metadata(env, conv, raw)

    service, calls, _ = _service(env)
    with pytest.raises(AuthorizationError):
        await _turn(service, uuid4(), conv)
    assert calls == []  # never run on a guessed (default) prompt

    client = _pin_client(env, monkeypatch, env["members"])
    response = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})
    assert response.status_code == 200 and response.json() == {"cleared": True, "recovered": True}

    stored = json.loads(_raw_metadata(env, conv))
    assert pin_fields_readable(stored) and "prompt_library" not in stored
    for key in ("workspace_id", "custom_key"):
        assert stored[key] == meta[key]
    assert ("data_classification" in stored) == ("data_classification" in meta)
    if "data_classification" in meta:
        assert stored["data_classification"] == meta["data_classification"]
    assert stored.get("prompt_library_history") == history_before
    damaged = stored[PIN_RECOVERY_METADATA_KEY]["damaged_fields"]
    assert {k: meta[k] for k in damaged} == damaged and damaged


@pytest.mark.asyncio
async def test_a_turn_on_a_damaged_record_names_the_way_out(env):
    _, conv, meta = await _pinned_conversation(env)
    meta["prompt_library_pin_revision"] = "x"
    _set_raw_metadata(env, conv, json.dumps(meta))
    service, calls, _ = _service(env)
    with pytest.raises(AuthorizationError) as exc:
        await _turn(service, uuid4(), conv)
    assert f"DELETE /api/prompts/pins/{conv}" in exc.value.message
    assert "Try again shortly" not in exc.value.message
    assert calls == []


# --- authorization is unchanged ------------------------------------------------


@pytest.mark.parametrize("raw", ["{broken", "[]"])
@pytest.mark.asyncio
async def test_only_the_owner_can_recover(env, monkeypatch, raw):
    _, conv, _ = await _pinned_conversation(env)
    _set_raw_metadata(env, conv, raw)
    client = _pin_client(env, monkeypatch, env["members"])
    for who in (OUTSIDER, OWNER):  # another user, even the prompt's owner
        response = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": who})
        assert response.status_code == 404
    response = client.delete(f"/api/prompts/pins/{uuid4()}", headers={"x-test-user": USER})
    assert response.status_code == 404
    assert _raw_metadata(env, conv) == raw  # untouched by the refused requests


@pytest.mark.asyncio
async def test_recovery_audits_nothing_from_the_untrusted_pin(env, monkeypatch):
    prompt, conv, meta = await _pinned_conversation(env)
    meta["prompt_library"] = {"prompt_id": prompt["id"]}  # a prompt id, but no version: untrusted
    _set_raw_metadata(env, conv, json.dumps(meta))
    before = await env["library"].list_audit_events(OWNER, prompt["id"])
    client = _pin_client(env, monkeypatch, env["members"])
    response = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})
    assert response.status_code == 200
    after = await env["library"].list_audit_events(OWNER, prompt["id"])
    assert len(after) == len(before)  # no "unpin" written against a damaged reference


# --- after recovery: revisions, sessions, saves --------------------------------


@pytest.mark.asyncio
async def test_a_stale_revision_from_before_the_damage_cannot_apply(env, monkeypatch):
    prompt, conv, meta = await _pinned_conversation(env)
    seen = meta.get("prompt_library_pin_revision", 0)
    meta["prompt_library_pin_revision"] = "corrupted"
    _set_raw_metadata(env, conv, json.dumps(meta))
    client = _pin_client(env, monkeypatch, env["members"])
    response = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})
    assert response.status_code == 200
    stored = json.loads(_raw_metadata(env, conv))
    assert stored["prompt_library_pin_revision"] > seen + 1000

    # A tab that saw the pre-damage revision tries to re-pin: refused.
    service, calls, _ = _service(env)
    with pytest.raises(ValidationError) as exc:
        await _turn(service, uuid4(), conv, library_prompt={"prompt_id": prompt["id"], "version_id": None},
                    library_prompt_revision=seen)
    assert getattr(exc.value, "code", None) == "prompt_pin_conflict"
    assert calls == []


@pytest.mark.asyncio
async def test_a_readable_revision_simply_moves_on(env, monkeypatch):
    _, conv, meta = await _pinned_conversation(env)
    seen = meta["prompt_library_pin_revision"]
    meta["prompt_library"] = "damaged"
    _set_raw_metadata(env, conv, json.dumps(meta))
    client = _pin_client(env, monkeypatch, env["members"])
    response = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})
    assert response.status_code == 200
    assert json.loads(_raw_metadata(env, conv))["prompt_library_pin_revision"] == seen + 1


@pytest.mark.asyncio
async def test_after_recovery_the_conversation_continues_unpinned_and_saves_keep_the_record(env, monkeypatch):
    _, conv, meta = await _pinned_conversation(env)
    meta["prompt_library"] = {"prompt_id": "p", "version_id": None}
    _set_raw_metadata(env, conv, json.dumps(meta))

    # A session that already had the conversation open before the recovery.
    service, calls, sessions = _service(env)
    open_session = uuid4()
    with pytest.raises(AuthorizationError):
        await _turn(service, open_session, conv)

    client = _pin_client(env, monkeypatch, env["members"])
    response = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})
    assert response.status_code == 200
    recovery = json.loads(_raw_metadata(env, conv))[PIN_RECOVERY_METADATA_KEY]

    await _turn(service, open_session, conv)  # the owner chose the default prompt
    assert calls and calls[-1].get("custom_system_prompt") is None
    stored = json.loads(_raw_metadata(env, conv))
    assert "prompt_library" not in stored
    # A save cannot drop or forge the recovery record.
    assert stored[PIN_RECOVERY_METADATA_KEY] == recovery

    again = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})
    assert again.status_code == 200 and again.json() == {"cleared": False}  # readable now: no churn


@pytest.mark.asyncio
async def test_a_client_cannot_plant_a_recovery_record_on_a_new_conversation(env):
    service, _, sessions = _service(env)
    conv = str(uuid4())
    sid = uuid4()
    await _turn(service, sid, conv)
    stored = json.loads(_raw_metadata(env, conv))
    assert PIN_RECOVERY_METADATA_KEY not in stored


@pytest.mark.asyncio
async def test_a_transient_store_failure_is_still_503(env, monkeypatch):
    _, conv, _ = await _pinned_conversation(env)

    def down(*a, **k):
        raise RuntimeError("database is down")

    monkeypatch.setattr(env["conversations"], "clear_prompt_pin", down)
    client = _pin_client(env, monkeypatch, env["members"])
    response = client.delete(f"/api/prompts/pins/{conv}", headers={"x-test-user": USER})
    assert response.status_code == 503


# --- the recovery function itself ----------------------------------------------


def test_recovery_never_writes_a_pin_and_only_one_level_of_history_of_recoveries():
    from atlas.domain.conversation_prompt_pin import recover_unreadable_metadata

    first = recover_unreadable_metadata("{x", 1_800_000_000, "t1")
    second = recover_unreadable_metadata(
        json.dumps({**first, "prompt_library": "bad"}), 1_800_000_100, "t2"
    )
    third = recover_unreadable_metadata(json.dumps({**second, "prompt_library": 7}), 1_800_000_200, "t3")
    for result in (first, second, third):
        assert "prompt_library" not in result and pin_fields_readable(result)
    assert first["prompt_library_pin_revision"] == 1_800_000_000
    assert second["prompt_library_pin_revision"] == 1_800_000_001
    assert "previous" not in third[PIN_RECOVERY_METADATA_KEY]["previous"]
    assert third["data_classification"] == UNKNOWN_CLASSIFICATION_RECORD  # never relabelled
