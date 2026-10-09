"""Saved conversations stay inside the classification they were created under.

Issue #1042: a conversation saved while one compliance level was active could
be reopened under another and its history sent to a model or tool approved
only for the second level. These tests pin the server-side boundary: the
record is server-managed, the turn gate refuses a mismatch before anything
runs, and restore, reconnect (hydration), mid-session level switches, the
REST fetch and steering all apply the same rule. Every message here is
synthetic placeholder text.
"""

import json
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from atlas.application.chat.service import ChatService
from atlas.core.compliance import ComplianceLevelManager
from atlas.domain import conversation_classification as conv_class
from atlas.domain.errors import ValidationError
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.modules.chat_history.conversation_repository import ConversationRepository
from atlas.modules.chat_history.database import (
    get_session_factory,
    init_database,
    reset_engine,
)
from atlas.modules.config.models import LLMConfig, MCPConfig, RAGSourcesConfig

USER = "alice@test.com"
CONV = "conv-classified"


@pytest.fixture
def manager(tmp_path):
    path = tmp_path / "compliance-levels.json"
    path.write_text(json.dumps({"levels": [
        {"name": "UUR", "allowed_with": ["UUR"]},
        {"name": "CUI", "aliases": ["CUI-Basic"], "allowed_with": ["CUI"]},
    ]}))
    return ComplianceLevelManager(path)


@pytest.fixture
def use_manager(manager, monkeypatch):
    monkeypatch.setattr("atlas.core.compliance.get_compliance_manager", lambda: manager)
    return manager


@pytest.fixture
def repo(tmp_path):
    reset_engine()
    init_database(f"duckdb:///{tmp_path / 'history.db'}")
    try:
        yield ConversationRepository(get_session_factory())
    finally:
        reset_engine()


def _messages(count, prefix="synthetic"):
    return [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"{prefix} message {i}",
            "message_type": "chat",
        }
        for i in range(count)
    ]


# --- the rule ----------------------------------------------------------------


@pytest.mark.parametrize(
    "binding, active, enabled, allowed",
    [
        (conv_class.make_binding("classified", "CUI"), "CUI", True, True),
        (conv_class.make_binding("classified", "CUI-Basic"), "CUI", True, True),
        (conv_class.make_binding("classified", "CUI"), "UUR", True, False),
        (conv_class.make_binding("classified", "UUR"), "CUI", True, False),
        (conv_class.make_binding("classified", "CUI"), None, True, False),
        (conv_class.make_binding("classified", "CUI"), None, False, False),
        (conv_class.make_binding("classified", "Retired"), "Retired", True, False),
        (conv_class.make_binding("unclassified"), None, True, True),
        (conv_class.make_binding("unclassified"), None, False, True),
        (conv_class.make_binding("unclassified"), "UUR", True, False),
        (conv_class.make_binding("legacy"), "UUR", True, False),
        (conv_class.make_binding("legacy"), None, True, False),
        (conv_class.make_binding("legacy"), None, False, True),
        (conv_class.make_binding("invalid"), None, False, False),
        ({"state": "classified"}, "CUI", True, False),
        ("not-a-binding", "CUI", True, False),
    ],
)
def test_resume_rule(manager, binding, active, enabled, allowed):
    refusal = conv_class.resume_refusal(
        binding, active, compliance_enabled=enabled, compliance_mgr=manager
    )
    assert (refusal is None) is allowed


def test_metadata_record_states():
    assert conv_class.binding_from_metadata({})["state"] == "legacy"
    assert conv_class.binding_from_metadata(None)["state"] == "legacy"
    assert conv_class.binding_from_metadata({"data_classification": None})["state"] == "unclassified"
    assert conv_class.binding_from_metadata({"data_classification": "CUI"}) == {
        "state": "classified", "level": "CUI",
    }
    assert conv_class.binding_from_metadata({"data_classification": 7})["state"] == "invalid"
    assert conv_class.binding_from_metadata({"data_classification": ""})["state"] == "invalid"


def test_refusal_message_names_levels_not_content(manager):
    message = conv_class.resume_refusal(
        conv_class.make_binding("classified", "CUI"), "UUR",
        compliance_enabled=True, compliance_mgr=manager,
    )
    assert "CUI" in message and "UUR" in message


# --- repository: the record is immutable --------------------------------------


def _save(repo, metadata, count=2):
    return repo.save_conversation(
        conversation_id=CONV, user_email=USER, title="t", model="m",
        messages=_messages(count), metadata=metadata,
    )


def test_repository_keeps_the_record_and_exposes_it(repo):
    assert _save(repo, {"data_classification": "CUI"}) is not None
    assert _save(repo, {"data_classification": "CUI"}, count=4) is not None

    stored = repo.get_conversation(CONV, USER)
    assert stored["metadata"]["data_classification"] == "CUI"
    assert stored["data_classification"] == "CUI"
    assert stored["data_classification_state"] == "classified"
    listed = repo.list_conversations(USER)[0]
    assert listed["data_classification"] == "CUI"
    assert listed["data_classification_state"] == "classified"
    found = repo.search_conversations(USER, "synthetic")[0]
    assert found["data_classification"] == "CUI"


@pytest.mark.parametrize(
    "first, second",
    [
        ({"data_classification": "CUI"}, {"data_classification": "UUR"}),
        ({"data_classification": "CUI"}, {"data_classification": None}),
        ({"data_classification": "CUI"}, {"agent_mode": False}),
        ({"data_classification": None}, {"data_classification": "UUR"}),
        # A legacy record is never stamped by an ordinary save.
        ({"agent_mode": False}, {"data_classification": "UUR"}),
    ],
)
def test_repository_refuses_to_change_the_record(repo, first, second):
    assert _save(repo, first) is not None
    assert _save(repo, second, count=4) is None
    stored = repo.get_conversation(CONV, USER)
    assert len(stored["messages"]) == 2
    assert stored["metadata"] == first


def test_legacy_rows_are_listed_as_legacy_and_can_be_stamped_explicitly(repo):
    _save(repo, {"agent_mode": False})
    assert repo.list_conversations(USER)[0]["data_classification_state"] == "legacy"

    assert repo.stamp_legacy_classification("UUR", user_email=USER) == 1
    assert repo.get_conversation(CONV, USER)["data_classification"] == "UUR"
    # A recorded classification is never rewritten by the stamp.
    assert repo.stamp_legacy_classification("CUI", user_email=USER) == 0
    assert repo.get_conversation(CONV, USER)["data_classification"] == "UUR"


# --- service: the turn gate ---------------------------------------------------


def _make_service(repository=None, *, compliance_enabled=True):
    sessions = {}

    async def _get(session_id):
        return sessions.get(session_id)

    async def _put(session):
        sessions[session.id] = session

    cm = MagicMock()
    cm.app_settings.runtime_capture_dir = tempfile.mkdtemp()
    cm.app_settings.feature_finetune_capture_enabled = False
    cm.app_settings.capture_user_salt = "test-salt"
    cm.app_settings.feature_compliance_levels_enabled = compliance_enabled
    cm.app_settings.compliance_level_required_effective = False
    # The model is approved for both levels, so the per-component check
    # (issue #1032) passes and the conversation record is what decides.
    cm.llm_config = LLMConfig(models={"m": {
        "model_name": "m", "model_url": "http://m",
        "allowed_data_classifications": ["UUR", "CUI"],
    }})
    cm.mcp_config = MCPConfig()
    cm.rag_sources_config = RAGSourcesConfig()

    session_repo = MagicMock()
    session_repo.get = AsyncMock(side_effect=_get)
    session_repo.create = AsyncMock(side_effect=_put)
    session_repo.update = AsyncMock(side_effect=_put)
    tool_manager = MagicMock()
    tool_manager.servers_config = {}
    service = ChatService(
        llm=MagicMock(), tool_manager=tool_manager, connection=MagicMock(),
        config_manager=cm, session_repository=session_repo,
        conversation_repository=repository,
    )
    return service, sessions


async def _turn(service, session_id, level, content="synthetic prompt", **kwargs):
    """Run a turn whose stand-in LLM records the history it would receive."""
    seen = []

    async def _execute(**_ignored):
        session = await service.session_repository.get(session_id)
        seen.append([m.content for m in session.history.messages])
        session.history.add_message(Message(role=MessageRole.USER, content=content))
        session.history.add_message(
            Message(role=MessageRole.ASSISTANT, content=f"reply to {content}")
        )
        return {"type": "done"}

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=_execute)
    kwargs.setdefault("conversation_id", CONV)
    with patch.object(service, "_get_orchestrator", return_value=orchestrator):
        await service.handle_chat_message(
            session_id=session_id, content=content, model="m",
            user_email=USER, compliance_level=level, **kwargs,
        )
    return seen


def _new_session(sessions):
    sid = uuid4()
    sessions[sid] = Session(id=sid, user_email=USER)
    return sid


@pytest.mark.asyncio
async def test_new_conversation_records_the_active_level(use_manager, repo):
    service, sessions = _make_service(repo)
    await _turn(service, _new_session(sessions), "CUI-Basic")

    stored = repo.get_conversation(CONV, USER)
    assert stored["data_classification"] == "CUI"  # canonical, server-resolved


@pytest.mark.asyncio
async def test_new_conversation_without_a_level_is_recorded_unclassified(use_manager, repo):
    service, sessions = _make_service(repo)
    await _turn(service, _new_session(sessions), None)

    stored = repo.get_conversation(CONV, USER)
    assert stored["metadata"]["data_classification"] is None
    assert stored["data_classification_state"] == "unclassified"


@pytest.mark.asyncio
async def test_saved_cui_conversation_is_not_resumed_under_uur(use_manager, repo):
    """The issue's scenario: saved under CUI, reopened the next day under UUR."""
    service, sessions = _make_service(repo)
    await _turn(service, _new_session(sessions), "CUI", content="placeholder alpha")

    later = _new_session(sessions)  # a new connection: the store is the source
    with pytest.raises(ValidationError) as exc:
        await _turn(service, later, "UUR")
    assert exc.value.code == conv_class.ERROR_CODE
    assert "placeholder alpha" not in exc.value.message

    stored = repo.get_conversation(CONV, USER)
    assert len(stored["messages"]) == 2, "a refused turn must not be persisted"
    assert stored["data_classification"] == "CUI"


@pytest.mark.asyncio
async def test_saved_cui_conversation_resumes_under_cui(use_manager, repo):
    service, sessions = _make_service(repo)
    await _turn(service, _new_session(sessions), "CUI", content="placeholder alpha")

    seen = await _turn(service, _new_session(sessions), "CUI")
    assert seen == [["placeholder alpha", "reply to placeholder alpha"]]
    assert len(repo.get_conversation(CONV, USER)["messages"]) == 4


@pytest.mark.asyncio
async def test_switching_level_mid_session_is_refused(use_manager, repo):
    """The live session's history is bound too, not only the stored record."""
    service, sessions = _make_service(repo)
    sid = _new_session(sessions)
    await _turn(service, sid, "CUI")

    with pytest.raises(ValidationError):
        await _turn(service, sid, "UUR")
    # Omitting the conversation id does not detach the session's history.
    with pytest.raises(ValidationError):
        await _turn(service, sid, "UUR", conversation_id=None)
    with pytest.raises(ValidationError):
        await _turn(service, sid, None)
    # Back at the recorded level the conversation continues.
    await _turn(service, sid, "CUI")


@pytest.mark.asyncio
async def test_unclassified_conversation_is_not_resumed_under_a_level(use_manager, repo):
    service, sessions = _make_service(repo)
    await _turn(service, _new_session(sessions), None)

    with pytest.raises(ValidationError):
        await _turn(service, _new_session(sessions), "UUR")


@pytest.mark.asyncio
async def test_legacy_conversation_fails_closed_while_levels_are_enforced(use_manager, repo):
    repo.save_conversation(
        conversation_id=CONV, user_email=USER, title="t", model="m",
        messages=_messages(2), metadata={"agent_mode": False},
    )
    service, sessions = _make_service(repo)
    for level in ("UUR", "CUI", None):
        with pytest.raises(ValidationError):
            await _turn(service, _new_session(sessions), level)
    stored = repo.get_conversation(CONV, USER)
    assert stored["data_classification_state"] == "legacy"


@pytest.mark.asyncio
async def test_legacy_conversation_resumes_when_levels_are_disabled(use_manager, repo):
    repo.save_conversation(
        conversation_id=CONV, user_email=USER, title="t", model="m",
        messages=_messages(2), metadata={"agent_mode": False},
    )
    service, sessions = _make_service(repo, compliance_enabled=False)
    seen = await _turn(service, _new_session(sessions), None)
    assert len(seen[0]) == 2
    # Still legacy: an ordinary save never stamps a classification on it.
    assert repo.get_conversation(CONV, USER)["data_classification_state"] == "legacy"


@pytest.mark.asyncio
async def test_classified_conversation_is_refused_when_levels_are_disabled(use_manager, repo):
    service, sessions = _make_service(repo)
    await _turn(service, _new_session(sessions), "CUI")

    disabled, disabled_sessions = _make_service(repo, compliance_enabled=False)
    with pytest.raises(ValidationError):
        await _turn(disabled, _new_session(disabled_sessions), None)


# --- restore ------------------------------------------------------------------


async def _seed_cui(repo):
    repo.save_conversation(
        conversation_id=CONV, user_email=USER, title="t", model="m",
        messages=_messages(2), metadata={"data_classification": "CUI"},
    )


@pytest.mark.asyncio
async def test_restore_at_another_level_is_refused_before_the_session_is_touched(use_manager, repo):
    await _seed_cui(repo)
    service, sessions = _make_service(repo)
    sid = _new_session(sessions)
    sessions[sid].history.add_message(Message(role=MessageRole.USER, content="current"))

    frame = await service.handle_restore_conversation(
        session_id=sid, conversation_id=CONV, messages=[], user_email=USER,
        compliance_level="UUR",
    )
    assert frame["type"] == "error"
    assert frame["error_type"] == conv_class.ERROR_CODE
    assert "synthetic" not in json.dumps(frame)
    assert [m.content for m in sessions[sid].history.messages] == ["current"]


@pytest.mark.asyncio
async def test_restore_at_the_recorded_level_loads_and_binds(use_manager, repo):
    await _seed_cui(repo)
    service, sessions = _make_service(repo)
    sid = _new_session(sessions)

    frame = await service.handle_restore_conversation(
        session_id=sid, conversation_id=CONV, messages=[], user_email=USER,
        compliance_level="CUI",
    )
    assert frame["type"] == "conversation_restored"
    assert frame["data_classification"] == "CUI"
    seen = await _turn(service, sid, "CUI")
    assert len(seen[0]) == 2


@pytest.mark.asyncio
async def test_restore_without_a_level_still_binds_the_stored_record(use_manager, repo):
    """An older client that omits the level cannot carry the history across."""
    await _seed_cui(repo)
    service, sessions = _make_service(repo)
    sid = _new_session(sessions)

    frame = await service.handle_restore_conversation(
        session_id=sid, conversation_id=CONV, messages=[], user_email=USER,
    )
    assert frame["type"] == "conversation_restored"
    with pytest.raises(ValidationError):
        await _turn(service, sid, "UUR")


@pytest.mark.asyncio
async def test_client_supplied_history_and_labels_do_not_override_the_store(use_manager, repo):
    await _seed_cui(repo)
    service, sessions = _make_service(repo)
    sid = _new_session(sessions)

    await service.handle_restore_conversation(
        session_id=sid, conversation_id=CONV, user_email=USER,
        messages=[{"role": "user", "content": "forged", "metadata": {"data_classification": "UUR"}}],
    )
    assert sessions[sid].context[conv_class.SESSION_BINDING_KEY]["level"] == "CUI"
    with pytest.raises(ValidationError):
        await _turn(service, sid, "UUR", data_classification="UUR")


@pytest.mark.asyncio
async def test_client_held_history_without_a_store_binds_to_the_restore_level(use_manager):
    """With no server store the client is the only holder of the history."""
    service, sessions = _make_service(None)
    sid = _new_session(sessions)
    await service.handle_restore_conversation(
        session_id=sid, conversation_id="local-1", user_email=USER,
        messages=_messages(2), compliance_level="CUI",
    )
    with pytest.raises(ValidationError):
        await _turn(service, sid, "UUR", conversation_id="local-1")

    sid2 = _new_session(sessions)
    await service.handle_restore_conversation(
        session_id=sid2, conversation_id="local-2", user_email=USER,
        messages=_messages(2),
    )
    # No level on the restore: provenance unknown, fails closed.
    with pytest.raises(ValidationError):
        await _turn(service, sid2, "CUI", conversation_id="local-2")


# --- steering and the in-flight view --------------------------------------------


@pytest.mark.asyncio
async def test_steering_into_a_turn_at_another_level_is_refused(use_manager, caplog):
    service, sessions = _make_service(None)
    sid = _new_session(sessions)
    sessions[sid].context[conv_class.SESSION_BINDING_KEY] = conv_class.make_binding("classified", "CUI")

    assert await service.steering_classification_refusal(sid, "CUI-Basic") is None
    assert await service.steering_classification_refusal(sid, "UUR")
    assert await service.steering_classification_refusal(sid, None)
    assert any("Refused steer of conversation" in r.getMessage() for r in caplog.records)
    # An undefined level is refused, not read as no level.
    assert "not defined" in await service.steering_classification_refusal(sid, "Bogus")


@pytest.mark.asyncio
async def test_steering_fails_closed_without_a_session_or_binding(use_manager):
    service, sessions = _make_service(None)
    assert await service.steering_classification_refusal(uuid4(), "CUI")
    sid = _new_session(sessions)  # a run whose turn has not reached its gate
    assert await service.steering_classification_refusal(sid, "CUI")


@pytest.mark.asyncio
async def test_in_flight_view_carries_the_run_binding():
    from atlas.application.chat.runs.in_flight import in_flight_conversation

    session = Session(id=uuid4(), user_email=USER)
    session.context[conv_class.SESSION_BINDING_KEY] = conv_class.make_binding("classified", "CUI")
    record = MagicMock(session_id=session.id, run_id="r1", created_at=0.0, updated_at=0.0)
    record.stream.text.return_value = ""
    registry = MagicMock()
    registry.active_for_conversation.return_value = record
    session_repo = MagicMock()
    session_repo.get = AsyncMock(return_value=session)

    live = await in_flight_conversation(session_repo, registry, CONV, USER)
    assert live["metadata"]["data_classification"] == "CUI"
    assert live["data_classification_state"] == "classified"


# --- REST fetch ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_fetch_at_another_level_returns_no_content(use_manager, repo, monkeypatch):
    from atlas.infrastructure.app_factory import app_factory
    from atlas.routes import conversation_routes

    await _seed_cui(repo)
    cm = MagicMock()
    cm.app_settings.feature_compliance_levels_enabled = True
    monkeypatch.setattr(app_factory, "conversation_repository", repo, raising=False)
    monkeypatch.setattr(app_factory, "get_config_manager", lambda: cm)
    monkeypatch.setattr(
        conversation_routes, "_in_flight_conversation", AsyncMock(return_value=None)
    )

    refused = await conversation_routes.get_conversation(
        CONV, compliance_level="UUR", current_user=USER
    )
    assert refused.status_code == 409
    body = json.loads(refused.body)
    assert body["error_type"] == conv_class.ERROR_CODE
    assert body["data_classification"] == "CUI"
    assert "messages" not in body and "synthetic" not in refused.body.decode()

    allowed = await conversation_routes.get_conversation(
        CONV, compliance_level="CUI", current_user=USER
    )
    assert len(allowed["messages"]) == 2


# --- unreadable records -------------------------------------------------------


def test_non_object_metadata_is_invalid_not_legacy():
    assert conv_class.binding_from_metadata([])["state"] == "invalid"
    assert conv_class.binding_from_metadata("CUI")["state"] == "invalid"


def _corrupt_metadata(repo, value):
    from atlas.modules.chat_history.models import ConversationRecord

    with repo._get_session() as session:
        session.get(ConversationRecord, CONV).metadata_json = value
        session.commit()


@pytest.mark.parametrize("raw", ["{not json", "[]", '"CUI"'])
@pytest.mark.asyncio
async def test_unreadable_stored_metadata_fails_closed_even_when_levels_are_disabled(
    use_manager, repo, raw
):
    await _seed_cui(repo)
    _corrupt_metadata(repo, raw)
    assert repo.get_conversation(CONV, USER)["data_classification_state"] == "invalid"
    assert repo.list_conversations(USER)[0]["data_classification_state"] == "invalid"

    service, sessions = _make_service(repo, compliance_enabled=False)
    with pytest.raises(ValidationError):
        await _turn(service, _new_session(sessions), None)
    frame = await service.handle_restore_conversation(
        session_id=_new_session(sessions), conversation_id=CONV, messages=[],
        user_email=USER, compliance_level=None,
    )
    assert frame["type"] == "error"


# --- review follow-ups ----------------------------------------------------------


@pytest.mark.asyncio
async def test_conversations_created_while_levels_are_disabled_stay_migratable(use_manager, repo):
    """No record is written while disabled, so the stamp script can migrate them."""
    disabled, disabled_sessions = _make_service(repo, compliance_enabled=False)
    await _turn(disabled, _new_session(disabled_sessions), None)
    stored = repo.get_conversation(CONV, USER)
    assert "data_classification" not in stored["metadata"]
    assert stored["data_classification_state"] == "legacy"
    # Later turns while disabled keep it unrecorded.
    await _turn(disabled, _new_session(disabled_sessions), None)
    assert repo.get_conversation(CONV, USER)["data_classification_state"] == "legacy"

    # Levels are enabled: the conversation fails closed until it is stamped.
    enabled, enabled_sessions = _make_service(repo)
    with pytest.raises(ValidationError):
        await _turn(enabled, _new_session(enabled_sessions), "UUR")
    assert repo.stamp_legacy_classification("UUR", user_email=USER) == 1
    seen = await _turn(enabled, _new_session(enabled_sessions), "UUR")
    assert len(seen[0]) == 4


@pytest.mark.asyncio
async def test_a_failed_store_read_refuses_the_turn_and_the_next_turn_retries(use_manager, repo):
    await _seed_cui(repo)
    service, sessions = _make_service(repo)
    sid = _new_session(sessions)

    with patch.object(repo, "get_conversation", side_effect=RuntimeError("store down")):
        with pytest.raises(ValidationError) as exc:
            await _turn(service, sid, "UUR")
    assert "could not be loaded" in exc.value.message
    assert repo.get_conversation(CONV, USER)["data_classification"] == "CUI"

    # The store answers again: the record is loaded and the rule applies.
    with pytest.raises(ValidationError) as exc:
        await _turn(service, sid, "UUR")
    assert "saved under CUI" in exc.value.message
    seen = await _turn(service, sid, "CUI")
    assert len(seen[0]) == 2


@pytest.mark.asyncio
async def test_switching_to_an_empty_record_drops_the_previous_history(use_manager, repo):
    """An empty UUR record must not rebind a session still holding CUI history."""
    service, sessions = _make_service(repo)
    sid = _new_session(sessions)
    await _turn(service, sid, "CUI", content="placeholder alpha")

    repo.save_conversation(
        conversation_id="conv-empty-uur", user_email=USER, title="t", model="m",
        messages=[], metadata={"data_classification": "UUR"},
    )
    seen = await _turn(service, sid, "UUR", conversation_id="conv-empty-uur")
    assert seen == [[]], "no CUI message may reach the UUR turn"
