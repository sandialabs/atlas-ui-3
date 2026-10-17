"""Parent-child conversation lineage (issue #1081).

The sidebar tree is built from two sources: the live run registry (which knows
a child before it is saved) and the child conversation's saved metadata (which
survives the registry reaping a terminal run and the process restarting). These
tests pin the durable half: the link is written by the launch path, published
only to the owner of the parent, and rendered as "no parent" when the parent is
gone or inaccessible.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from atlas.application.chat.runs import RunRegistry
from atlas.domain.conversation_lineage import (
    PARENT_CONVERSATION_METADATA_KEY,
    lineage_fields,
    parent_conversation_id_from_metadata,
)
from atlas.modules.chat_history.database import reset_engine

USER = "user@test.com"
OTHER = "other@test.com"


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def repo(tmp_path):
    from atlas.modules.chat_history import (
        ConversationRepository,
        get_session_factory,
        init_database,
    )

    init_database(f"duckdb:///{tmp_path / 'lineage.db'}")
    return ConversationRepository(get_session_factory())


@pytest.fixture
def chat_service(repo):
    from atlas.application.chat.service import ChatService

    return ChatService(llm=MagicMock(), conversation_repository=repo)


def _messages(text="hello"):
    return [
        {"role": "user", "content": text, "message_type": "chat"},
        {"role": "assistant", "content": "reply", "message_type": "chat"},
    ]


def _save(repo, conversation_id, user_email=USER, parent_id=None, title=None):
    metadata = {PARENT_CONVERSATION_METADATA_KEY: parent_id} if parent_id else None
    repo.save_conversation(
        conversation_id=conversation_id,
        user_email=user_email,
        title=title or conversation_id,
        model="gpt-4",
        messages=_messages(conversation_id),
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

def test_parent_id_reads_only_a_non_empty_string():
    assert parent_conversation_id_from_metadata({"parent_conversation_id": "p"}) == "p"
    assert parent_conversation_id_from_metadata({"parent_conversation_id": "  p  "}) == "p"
    assert parent_conversation_id_from_metadata({"parent_conversation_id": ""}) is None
    assert parent_conversation_id_from_metadata({"parent_conversation_id": 3}) is None
    assert parent_conversation_id_from_metadata(None) is None
    assert parent_conversation_id_from_metadata({"workspace_id": "w"}) is None


def test_lineage_fields_requires_the_checked_parent():
    metadata = {"parent_conversation_id": "p"}
    assert lineage_fields(metadata, visible_parent_id="p") == {"parent_conversation_id": "p"}
    # No parent, a foreign parent, or a mismatch all read the same: no link.
    assert lineage_fields(metadata, visible_parent_id=None) == {"parent_conversation_id": None}
    assert lineage_fields({}, visible_parent_id=None) == {"parent_conversation_id": None}


# ---------------------------------------------------------------------------
# Run registry
# ---------------------------------------------------------------------------

def test_run_public_dict_carries_the_parent_conversation():
    registry = RunRegistry()
    parent = registry.start(conversation_id="parent-conv", user_email=USER)
    child = registry.start(
        conversation_id="child-conv",
        user_email=USER,
        parent_run_id=parent.run_id,
        depth=1,
    )

    assert child.to_public_dict()["parent_conversation_id"] == "parent-conv"
    assert parent.to_public_dict()["parent_conversation_id"] is None

    snapshot = {r["run_id"]: r for r in registry.snapshot_for_user(USER)}
    assert snapshot[child.run_id]["parent_conversation_id"] == "parent-conv"
    assert "launch_spec" not in snapshot[child.run_id]


def test_run_started_payload_carries_the_parent_conversation():
    """The live-sidebar path needs the link before the child's first save."""
    registry = RunRegistry()
    parent = registry.start(conversation_id="parent-conv", user_email=USER)
    child = registry.start(
        conversation_id="child-conv",
        user_email=USER,
        parent_run_id=parent.run_id,
        depth=1,
    )
    # The websocket frame is built from these two fields; both must be present.
    public = child.to_public_dict()
    assert public["parent_run_id"] == parent.run_id
    assert public["parent_conversation_id"] == "parent-conv"


# ---------------------------------------------------------------------------
# Persistence and listing
# ---------------------------------------------------------------------------

def test_saved_child_publishes_its_parent_in_the_listing(repo):
    _save(repo, "parent-conv")
    _save(repo, "child-conv", parent_id="parent-conv")

    listed = {c["id"]: c for c in repo.list_conversations(USER)}
    assert listed["parent-conv"]["parent_conversation_id"] is None
    assert listed["child-conv"]["parent_conversation_id"] == "parent-conv"


def test_search_publishes_the_parent_too(repo):
    _save(repo, "parent-conv")
    _save(repo, "child-conv", parent_id="parent-conv")

    found = repo.search_conversations(USER, "child-conv")
    assert [c["parent_conversation_id"] for c in found] == ["parent-conv"]


def test_deleted_parent_leaves_the_child_orphaned(repo):
    _save(repo, "parent-conv")
    _save(repo, "child-conv", parent_id="parent-conv")
    repo.delete_conversation("parent-conv", USER)

    listed = repo.list_conversations(USER)
    assert [c["id"] for c in listed] == ["child-conv"]
    assert listed[0]["parent_conversation_id"] is None


def test_another_users_parent_is_not_disclosed(repo):
    # The child's metadata can only be written server-side, but a legacy or
    # corrupt blob must still fail closed: the parent id is the other user's.
    _save(repo, "foreign-parent", user_email=OTHER)
    _save(repo, "child-conv", parent_id="foreign-parent")

    listed = repo.list_conversations(USER)
    assert [c["id"] for c in listed] == ["child-conv"]
    assert listed[0]["parent_conversation_id"] is None

    full = repo.get_conversation("child-conv", USER)
    assert full["parent_conversation_id"] is None
    assert PARENT_CONVERSATION_METADATA_KEY not in full["metadata"]


def test_unknown_parent_is_not_disclosed(repo):
    _save(repo, "child-conv", parent_id="does-not-exist")

    listed = repo.list_conversations(USER)
    assert listed[0]["parent_conversation_id"] is None
    full = repo.get_conversation("child-conv", USER)
    assert full["parent_conversation_id"] is None
    assert PARENT_CONVERSATION_METADATA_KEY not in full["metadata"]


def test_full_record_keeps_a_visible_parent(repo):
    _save(repo, "parent-conv")
    _save(repo, "child-conv", parent_id="parent-conv")

    full = repo.get_conversation("child-conv", USER)
    assert full["parent_conversation_id"] == "parent-conv"
    assert full["metadata"][PARENT_CONVERSATION_METADATA_KEY] == "parent-conv"


def test_a_parent_need_not_be_on_the_same_page(repo):
    """Pagination must not silently orphan a child whose parent is on page 2."""
    _save(repo, "parent-conv")
    # Saved second, so the child is the newest and the only row on page one.
    _save(repo, "child-conv", parent_id="parent-conv")

    first_page = repo.list_conversations(USER, limit=1)
    assert first_page[0]["id"] == "child-conv"
    assert first_page[0]["parent_conversation_id"] == "parent-conv"


def test_export_strips_an_inaccessible_parent(repo):
    _save(repo, "foreign-parent", user_email=OTHER)
    _save(repo, "child-conv", parent_id="foreign-parent")

    exported = repo.export_all_conversations(USER)
    assert exported[0]["id"] == "child-conv"
    assert exported[0]["parent_conversation_id"] is None
    assert PARENT_CONVERSATION_METADATA_KEY not in exported[0]["metadata"]


# ---------------------------------------------------------------------------
# Chat service write and hydrate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_launched_child_turn_saves_the_parent_after_hydration(chat_service, repo):
    """The real turn path hydrates *after* the launch argument is read.

    This is the regression the seeded-metadata test above cannot catch: a new
    child conversation id is unknown to the store, hydration clears the lineage
    key, and applying the launch argument before that point loses the link on
    the child's very first save.
    """
    from unittest.mock import AsyncMock, patch

    from atlas.domain.messages.models import Message, MessageRole

    _save(repo, "parent-conv")
    session_id = uuid4()

    async def _execute(**_ignored):
        session = await chat_service.session_repository.get(session_id)
        session.history.add_message(Message(role=MessageRole.USER, content="do work"))
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="done"))
        return {"type": "done"}

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=_execute)
    with patch.object(chat_service, "_get_orchestrator", return_value=orchestrator):
        await chat_service.handle_chat_message(
            session_id=session_id,
            content="do work",
            model="gpt-4",
            user_email=USER,
            conversation_id="child-conv",
            parent_conversation_id="parent-conv",
        )

    saved = repo.get_conversation("child-conv", USER)
    assert saved["metadata"][PARENT_CONVERSATION_METADATA_KEY] == "parent-conv"
    assert saved["parent_conversation_id"] == "parent-conv"


@pytest.mark.asyncio
async def test_a_follow_up_keeps_the_stored_parent_over_the_launch_argument(
    chat_service, repo
):
    from unittest.mock import AsyncMock, patch

    from atlas.domain.messages.models import Message, MessageRole

    _save(repo, "stored-parent")
    _save(repo, "other-parent")
    _save(repo, "child-conv", parent_id="stored-parent")

    async def _execute(**_ignored):
        session = await chat_service.session_repository.get(session_id)
        session.history.add_message(Message(role=MessageRole.USER, content="next step"))
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="done"))
        return {"type": "done"}

    session_id = uuid4()
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=_execute)
    with patch.object(chat_service, "_get_orchestrator", return_value=orchestrator):
        await chat_service.handle_chat_message(
            session_id=session_id,
            content="next step",
            model="gpt-4",
            user_email=USER,
            conversation_id="child-conv",
            parent_conversation_id="other-parent",
        )

    saved = repo.get_conversation("child-conv", USER)
    assert saved["metadata"][PARENT_CONVERSATION_METADATA_KEY] == "stored-parent"
    assert saved["parent_conversation_id"] == "stored-parent"


@pytest.mark.asyncio
async def test_a_second_turn_without_the_argument_keeps_the_stored_parent(
    chat_service, repo
):
    """The link is the conversation's, not each turn's argument."""
    from unittest.mock import AsyncMock, patch

    from atlas.domain.messages.models import Message, MessageRole

    _save(repo, "parent-conv")
    _save(repo, "child-conv", parent_id="parent-conv")

    async def _execute(**_ignored):
        session = await chat_service.session_repository.get(session_id)
        session.history.add_message(Message(role=MessageRole.USER, content="next step"))
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="done"))
        return {"type": "done"}

    session_id = uuid4()
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=_execute)
    with patch.object(chat_service, "_get_orchestrator", return_value=orchestrator):
        await chat_service.handle_chat_message(
            session_id=session_id,
            content="next step",
            model="gpt-4",
            user_email=USER,
            conversation_id="child-conv",
        )

    saved = repo.get_conversation("child-conv", USER)
    assert saved["metadata"][PARENT_CONVERSATION_METADATA_KEY] == "parent-conv"
    assert saved["parent_conversation_id"] == "parent-conv"


@pytest.mark.asyncio
async def test_save_conversation_persists_the_parent_link(chat_service, repo):
    from atlas.domain.messages.models import Message, MessageRole

    _save(repo, "parent-conv")
    session = await chat_service.create_session(uuid4(), USER)
    session.context["parent_conversation_id"] = "parent-conv"
    session.history.add_message(Message(role=MessageRole.USER, content="do work"))
    session.history.add_message(Message(role=MessageRole.ASSISTANT, content="done"))

    assert chat_service._save_conversation(session, USER, "gpt-4") is True
    conv_id = session.context.get("conversation_id", str(session.id))
    saved = repo.get_conversation(conv_id, USER)
    assert saved["metadata"][PARENT_CONVERSATION_METADATA_KEY] == "parent-conv"
    assert saved["parent_conversation_id"] == "parent-conv"


@pytest.mark.asyncio
async def test_hydration_restores_the_parent_link_for_the_next_turn(chat_service, repo):


    _save(repo, "parent-conv")
    _save(repo, "child-conv", parent_id="parent-conv")

    session = await chat_service.create_session(uuid4(), USER)
    loaded = await chat_service._hydrate_session_from_store(
        session, "child-conv", USER, False
    )
    assert loaded == 2
    assert session.context["parent_conversation_id"] == "parent-conv"


@pytest.mark.asyncio
async def test_failed_hydration_does_not_stamp_the_old_parent_on_a_new_conversation(
    chat_service, monkeypatch
):
    """A store error while switching conversations must not reattach X's parent to Y."""
    def _boom(*_args, **_kwargs):
        raise RuntimeError("history store unavailable")

    monkeypatch.setattr(
        chat_service.conversation_repository, "get_conversation", _boom
    )
    session = await chat_service.create_session(uuid4(), USER)
    session.context["conversation_id"] = "conv-x"
    session.context["parent_conversation_id"] = "parent-of-x"

    loaded = await chat_service._hydrate_session_from_store(
        session,
        "conv-y",
        USER,
        False,
        previous_conversation_id="conv-x",
    )

    assert loaded == 0
    assert session.context.get("parent_conversation_id") is None


@pytest.mark.asyncio
async def test_failed_rehydration_of_the_same_conversation_keeps_its_parent(
    chat_service, monkeypatch
):
    """An empty session retrying the same conversation keeps its own link."""
    def _boom(*_args, **_kwargs):
        raise RuntimeError("history store unavailable")

    monkeypatch.setattr(
        chat_service.conversation_repository, "get_conversation", _boom
    )
    session = await chat_service.create_session(uuid4(), USER)
    session.context["conversation_id"] = "conv-x"
    session.context["parent_conversation_id"] = "parent-of-x"

    loaded = await chat_service._hydrate_session_from_store(
        session,
        "conv-x",
        USER,
        False,
        previous_conversation_id="conv-x",
    )

    assert loaded == 0
    assert session.context["parent_conversation_id"] == "parent-of-x"


@pytest.mark.asyncio
async def test_hydration_clears_a_previous_conversations_parent(chat_service, repo):


    _save(repo, "child-conv", parent_id="some-parent")
    _save(repo, "plain-conv")

    session = await chat_service.create_session(uuid4(), USER)
    session.context["parent_conversation_id"] = "stale-parent"
    await chat_service._hydrate_session_from_store(session, "plain-conv", USER, False)

    # Leaving the stale value would stamp the wrong parent onto a later save.
    assert session.context["parent_conversation_id"] is None
