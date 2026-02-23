"""Tests for the chat history persistence module.

Tests cover: database init, conversation CRUD, search, tags, multi-delete,
user isolation, and upsert behavior.
Uses an in-memory DuckDB database for fast, isolated tests.
"""

import os

import pytest

# Ensure clean engine state before each test
from atlas.modules.chat_history.database import reset_engine


@pytest.fixture(autouse=True)
def _clean_engine():
    """Reset the global engine before and after each test."""
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def db_path(tmp_path):
    """Provide a temporary DuckDB file path."""
    return str(tmp_path / "test_chat_history.db")


@pytest.fixture
def repo(db_path):
    """Create a ConversationRepository backed by a temp DuckDB."""
    from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database

    db_url = f"duckdb:///{db_path}"
    init_database(db_url)
    factory = get_session_factory()
    return ConversationRepository(factory)


def _make_messages(count=3):
    """Helper to create test message dicts."""
    msgs = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({
            "role": role,
            "content": f"Test message {i}",
            "message_type": "chat",
        })
    return msgs


class TestDatabaseInit:
    def test_init_creates_tables(self, db_path):
        from atlas.modules.chat_history import init_database
        engine = init_database(f"duckdb:///{db_path}")
        assert engine is not None
        assert os.path.exists(db_path)

    def test_init_idempotent(self, db_path):
        from atlas.modules.chat_history import init_database
        init_database(f"duckdb:///{db_path}")
        # Second call should not fail
        reset_engine()
        init_database(f"duckdb:///{db_path}")


class TestSaveAndGet:
    def test_save_new_conversation(self, repo):
        conv = repo.save_conversation(
            conversation_id="conv-1",
            user_email="user@test.com",
            title="Test Conversation",
            model="gpt-4",
            messages=_make_messages(3),
        )
        assert conv.id == "conv-1"
        assert conv.user_email == "user@test.com"
        assert conv.message_count == 3

    def test_get_conversation(self, repo):
        repo.save_conversation(
            conversation_id="conv-2",
            user_email="user@test.com",
            title="Get Test",
            model="gpt-4",
            messages=_make_messages(2),
        )
        result = repo.get_conversation("conv-2", "user@test.com")
        assert result is not None
        assert result["id"] == "conv-2"
        assert result["title"] == "Get Test"
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][1]["role"] == "assistant"

    def test_get_nonexistent_returns_none(self, repo):
        assert repo.get_conversation("nope", "user@test.com") is None

    def test_upsert_replaces_messages(self, repo):
        repo.save_conversation(
            conversation_id="conv-3",
            user_email="user@test.com",
            title="Upsert Test",
            model="gpt-4",
            messages=_make_messages(2),
        )
        # Save again with different messages
        repo.save_conversation(
            conversation_id="conv-3",
            user_email="user@test.com",
            title="Updated Title",
            model="gpt-4",
            messages=_make_messages(5),
        )
        result = repo.get_conversation("conv-3", "user@test.com")
        assert result["title"] == "Updated Title"
        assert len(result["messages"]) == 5
        assert result["message_count"] == 5

    def test_save_with_metadata(self, repo):
        repo.save_conversation(
            conversation_id="conv-meta",
            user_email="user@test.com",
            title="Meta Test",
            model="gpt-4",
            messages=_make_messages(1),
            metadata={"agent_mode": True, "tools": ["search"]},
        )
        result = repo.get_conversation("conv-meta", "user@test.com")
        assert result["metadata"]["agent_mode"] is True
        assert "search" in result["metadata"]["tools"]


class TestList:
    def test_list_conversations_empty(self, repo):
        result = repo.list_conversations("user@test.com")
        assert result == []

    def test_list_conversations_ordered_by_updated(self, repo):
        for i in range(3):
            repo.save_conversation(
                conversation_id=f"conv-{i}",
                user_email="user@test.com",
                title=f"Conv {i}",
                model="gpt-4",
                messages=[{"role": "user", "content": f"Hello {i}"}],
            )
        result = repo.list_conversations("user@test.com")
        assert len(result) == 3
        # Most recently updated first
        assert result[0]["id"] == "conv-2"

    def test_list_with_pagination(self, repo):
        for i in range(5):
            repo.save_conversation(
                conversation_id=f"conv-{i}",
                user_email="user@test.com",
                title=f"Conv {i}",
                model="gpt-4",
                messages=[{"role": "user", "content": f"Hello {i}"}],
            )
        page1 = repo.list_conversations("user@test.com", limit=2, offset=0)
        page2 = repo.list_conversations("user@test.com", limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0]["id"] != page2[0]["id"]

    def test_list_includes_preview(self, repo):
        repo.save_conversation(
            conversation_id="conv-preview",
            user_email="user@test.com",
            title=None,
            model="gpt-4",
            messages=[
                {"role": "user", "content": "This is a preview test message"},
                {"role": "assistant", "content": "Here is the preview test reply"},
            ],
        )
        result = repo.list_conversations("user@test.com")
        assert len(result) == 1
        assert "preview" in result[0]
        assert "preview test reply" in result[0]["preview"]


class TestDelete:
    def test_delete_single(self, repo):
        repo.save_conversation(
            conversation_id="conv-del",
            user_email="user@test.com",
            title="Delete Me",
            model="gpt-4",
            messages=_make_messages(1),
        )
        assert repo.delete_conversation("conv-del", "user@test.com") is True
        assert repo.get_conversation("conv-del", "user@test.com") is None

    def test_delete_nonexistent_returns_false(self, repo):
        assert repo.delete_conversation("nope", "user@test.com") is False

    def test_delete_multiple(self, repo):
        for i in range(4):
            repo.save_conversation(
                conversation_id=f"conv-{i}",
                user_email="user@test.com",
                title=f"Conv {i}",
                model="gpt-4",
                messages=_make_messages(1),
            )
        count = repo.delete_conversations(["conv-0", "conv-2"], "user@test.com")
        assert count == 2
        remaining = repo.list_conversations("user@test.com")
        assert len(remaining) == 2

    def test_delete_all(self, repo):
        for i in range(3):
            repo.save_conversation(
                conversation_id=f"conv-{i}",
                user_email="user@test.com",
                title=f"Conv {i}",
                model="gpt-4",
                messages=_make_messages(1),
            )
        count = repo.delete_all_conversations("user@test.com")
        assert count == 3
        assert repo.list_conversations("user@test.com") == []


class TestSearch:
    def test_search_by_title(self, repo):
        repo.save_conversation(
            conversation_id="conv-search1",
            user_email="user@test.com",
            title="Python programming tips",
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
        )
        repo.save_conversation(
            conversation_id="conv-search2",
            user_email="user@test.com",
            title="JavaScript frameworks",
            model="gpt-4",
            messages=[{"role": "user", "content": "Hi"}],
        )
        result = repo.search_conversations("user@test.com", "Python")
        assert len(result) == 1
        assert result[0]["id"] == "conv-search1"

    def test_search_by_message_content(self, repo):
        repo.save_conversation(
            conversation_id="conv-sc",
            user_email="user@test.com",
            title="General Chat",
            model="gpt-4",
            messages=[{"role": "user", "content": "Tell me about quantum computing"}],
        )
        result = repo.search_conversations("user@test.com", "quantum")
        assert len(result) == 1
        assert result[0]["id"] == "conv-sc"

    def test_search_no_results(self, repo):
        repo.save_conversation(
            conversation_id="conv-nr",
            user_email="user@test.com",
            title="Chat",
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
        )
        result = repo.search_conversations("user@test.com", "zzzznotfound")
        assert result == []


class TestTags:
    def test_add_and_list_tags(self, repo):
        repo.save_conversation(
            conversation_id="conv-tag1",
            user_email="user@test.com",
            title="Tagged",
            model="gpt-4",
            messages=_make_messages(1),
        )
        tag_id = repo.add_tag("conv-tag1", "work", "user@test.com")
        assert tag_id is not None

        tags = repo.list_tags("user@test.com")
        assert len(tags) == 1
        assert tags[0]["name"] == "work"
        assert tags[0]["conversation_count"] == 1

    def test_filter_by_tag(self, repo):
        repo.save_conversation(
            conversation_id="conv-t1",
            user_email="user@test.com",
            title="Work Chat",
            model="gpt-4",
            messages=_make_messages(1),
        )
        repo.save_conversation(
            conversation_id="conv-t2",
            user_email="user@test.com",
            title="Personal Chat",
            model="gpt-4",
            messages=_make_messages(1),
        )
        repo.add_tag("conv-t1", "work", "user@test.com")
        result = repo.list_conversations("user@test.com", tag_name="work")
        assert len(result) == 1
        assert result[0]["id"] == "conv-t1"

    def test_remove_tag(self, repo):
        repo.save_conversation(
            conversation_id="conv-rt",
            user_email="user@test.com",
            title="Remove Tag",
            model="gpt-4",
            messages=_make_messages(1),
        )
        tag_id = repo.add_tag("conv-rt", "temp", "user@test.com")
        assert repo.remove_tag("conv-rt", tag_id, "user@test.com") is True
        tags = repo.list_tags("user@test.com")
        # Tag record still exists but has 0 associations
        assert tags[0]["conversation_count"] == 0

    def test_tags_included_in_conversation(self, repo):
        repo.save_conversation(
            conversation_id="conv-ti",
            user_email="user@test.com",
            title="Tag Include",
            model="gpt-4",
            messages=_make_messages(1),
        )
        repo.add_tag("conv-ti", "important", "user@test.com")
        result = repo.get_conversation("conv-ti", "user@test.com")
        assert "important" in result["tags"]


class TestUpdateTitle:
    def test_update_title(self, repo):
        repo.save_conversation(
            conversation_id="conv-ut",
            user_email="user@test.com",
            title="Old Title",
            model="gpt-4",
            messages=_make_messages(1),
        )
        assert repo.update_title("conv-ut", "New Title", "user@test.com") is True
        result = repo.get_conversation("conv-ut", "user@test.com")
        assert result["title"] == "New Title"

    def test_update_title_nonexistent(self, repo):
        assert repo.update_title("nope", "New Title", "user@test.com") is False


class TestUserIsolation:
    def test_users_cannot_see_each_others_conversations(self, repo):
        repo.save_conversation(
            conversation_id="conv-u1",
            user_email="alice@test.com",
            title="Alice Chat",
            model="gpt-4",
            messages=_make_messages(1),
        )
        repo.save_conversation(
            conversation_id="conv-u2",
            user_email="bob@test.com",
            title="Bob Chat",
            model="gpt-4",
            messages=_make_messages(1),
        )
        alice_convs = repo.list_conversations("alice@test.com")
        bob_convs = repo.list_conversations("bob@test.com")
        assert len(alice_convs) == 1
        assert alice_convs[0]["title"] == "Alice Chat"
        assert len(bob_convs) == 1
        assert bob_convs[0]["title"] == "Bob Chat"

    def test_user_cannot_get_others_conversation(self, repo):
        repo.save_conversation(
            conversation_id="conv-priv",
            user_email="alice@test.com",
            title="Private",
            model="gpt-4",
            messages=_make_messages(1),
        )
        result = repo.get_conversation("conv-priv", "bob@test.com")
        assert result is None

    def test_user_cannot_delete_others_conversation(self, repo):
        repo.save_conversation(
            conversation_id="conv-nd",
            user_email="alice@test.com",
            title="Not Deletable",
            model="gpt-4",
            messages=_make_messages(1),
        )
        assert repo.delete_conversation("conv-nd", "bob@test.com") is False
        # Verify it still exists for Alice
        assert repo.get_conversation("conv-nd", "alice@test.com") is not None


class TestSaveConversationSecurity:
    """Tests for cross-user save_conversation protections."""

    def test_save_cannot_overwrite_other_users_conversation(self, repo):
        """A user who knows another user's conversation_id cannot overwrite it."""
        repo.save_conversation(
            conversation_id="shared-id",
            user_email="alice@test.com",
            title="Alice Original",
            model="gpt-4",
            messages=[{"role": "user", "content": "Alice secret message"}],
        )
        # Bob tries to save using the same conversation_id - should be rejected
        result = repo.save_conversation(
            conversation_id="shared-id",
            user_email="bob@test.com",
            title="Bob Hijack",
            model="gpt-4",
            messages=[{"role": "user", "content": "Bob overwrote you"}],
        )
        assert result is None

        # Alice's conversation should be unchanged
        alice_conv = repo.get_conversation("shared-id", "alice@test.com")
        assert alice_conv is not None
        assert alice_conv["title"] == "Alice Original"
        assert alice_conv["messages"][0]["content"] == "Alice secret message"

        # Bob should have no conversation
        bob_conv = repo.get_conversation("shared-id", "bob@test.com")
        assert bob_conv is None

    def test_upsert_only_updates_own_conversation(self, repo):
        """Upsert with matching id but different user creates a new record."""
        repo.save_conversation(
            conversation_id="upsert-id",
            user_email="alice@test.com",
            title="Alice V1",
            model="gpt-4",
            messages=_make_messages(2),
        )
        # Alice updates her own conversation - should work
        repo.save_conversation(
            conversation_id="upsert-id",
            user_email="alice@test.com",
            title="Alice V2",
            model="gpt-4",
            messages=_make_messages(4),
        )
        alice_conv = repo.get_conversation("upsert-id", "alice@test.com")
        assert alice_conv["title"] == "Alice V2"
        assert alice_conv["message_count"] == 4

    def test_save_preserves_title_on_update_when_none(self, repo):
        """Updating a conversation with title=None preserves existing title."""
        repo.save_conversation(
            conversation_id="title-keep",
            user_email="user@test.com",
            title="Custom Title",
            model="gpt-4",
            messages=_make_messages(1),
        )
        repo.save_conversation(
            conversation_id="title-keep",
            user_email="user@test.com",
            title=None,
            model="gpt-4",
            messages=_make_messages(2),
        )
        result = repo.get_conversation("title-keep", "user@test.com")
        assert result["title"] == "Custom Title"
        assert result["message_count"] == 2

    def test_user_cannot_search_others_conversations(self, repo):
        """Search is scoped by user_email."""
        repo.save_conversation(
            conversation_id="search-secret",
            user_email="alice@test.com",
            title="Alice Secret Project",
            model="gpt-4",
            messages=[{"role": "user", "content": "Classified information"}],
        )
        result = repo.search_conversations("bob@test.com", "Secret")
        assert result == []

    def test_user_cannot_tag_others_conversation(self, repo):
        """Tags cannot be added to another user's conversation."""
        repo.save_conversation(
            conversation_id="tag-priv",
            user_email="alice@test.com",
            title="Alice Chat",
            model="gpt-4",
            messages=_make_messages(1),
        )
        tag_id = repo.add_tag("tag-priv", "hacked", "bob@test.com")
        assert tag_id is None

    def test_user_cannot_update_others_title(self, repo):
        """Title update is scoped by user_email."""
        repo.save_conversation(
            conversation_id="title-priv",
            user_email="alice@test.com",
            title="Alice Title",
            model="gpt-4",
            messages=_make_messages(1),
        )
        assert repo.update_title("title-priv", "Hacked Title", "bob@test.com") is False
        alice_conv = repo.get_conversation("title-priv", "alice@test.com")
        assert alice_conv["title"] == "Alice Title"

    def test_delete_all_only_affects_own_conversations(self, repo):
        """delete_all_conversations only deletes the requesting user's data."""
        repo.save_conversation(
            conversation_id="da-alice",
            user_email="alice@test.com",
            title="Alice Chat",
            model="gpt-4",
            messages=_make_messages(1),
        )
        repo.save_conversation(
            conversation_id="da-bob",
            user_email="bob@test.com",
            title="Bob Chat",
            model="gpt-4",
            messages=_make_messages(1),
        )
        count = repo.delete_all_conversations("alice@test.com")
        assert count == 1
        # Bob's conversation should survive
        assert repo.get_conversation("da-bob", "bob@test.com") is not None

    def test_bulk_delete_only_affects_own_conversations(self, repo):
        """delete_conversations only deletes ids belonging to the requesting user."""
        repo.save_conversation(
            conversation_id="bd-alice",
            user_email="alice@test.com",
            title="Alice Chat",
            model="gpt-4",
            messages=_make_messages(1),
        )
        repo.save_conversation(
            conversation_id="bd-bob",
            user_email="bob@test.com",
            title="Bob Chat",
            model="gpt-4",
            messages=_make_messages(1),
        )
        # Alice tries to delete both
        count = repo.delete_conversations(["bd-alice", "bd-bob"], "alice@test.com")
        assert count == 1
        assert repo.get_conversation("bd-bob", "bob@test.com") is not None


class TestRoutesSecurity:
    """Test REST API route-level security and error handling."""

    @pytest.fixture
    def client(self, repo):
        from unittest.mock import patch

        from main import app
        from starlette.testclient import TestClient

        with patch("atlas.routes.conversation_routes._get_repo", return_value=repo):
            yield TestClient(app)

    def test_get_nonexistent_returns_404(self, client):
        resp = client.get(
            "/api/conversations/does-not-exist",
            headers={"X-User-Email": "test@test.com"},
        )
        assert resp.status_code == 404

    def test_get_other_users_conversation_returns_404(self, client, repo):
        repo.save_conversation(
            conversation_id="priv-conv",
            user_email="alice@test.com",
            title="Private",
            model="gpt-4",
            messages=_make_messages(1),
        )
        resp = client.get(
            "/api/conversations/priv-conv",
            headers={"X-User-Email": "bob@test.com"},
        )
        assert resp.status_code == 404

    def test_delete_other_users_conversation(self, client, repo):
        repo.save_conversation(
            conversation_id="del-priv",
            user_email="alice@test.com",
            title="Alice Only",
            model="gpt-4",
            messages=_make_messages(1),
        )
        resp = client.delete(
            "/api/conversations/del-priv",
            headers={"X-User-Email": "bob@test.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is False
        # Verify Alice's conversation still exists
        assert repo.get_conversation("del-priv", "alice@test.com") is not None

    def test_search_isolated_by_user(self, client, repo):
        repo.save_conversation(
            conversation_id="search-iso",
            user_email="alice@test.com",
            title="Alice Secret",
            model="gpt-4",
            messages=[{"role": "user", "content": "secret data"}],
        )
        resp = client.get(
            "/api/conversations/search?q=secret",
            headers={"X-User-Email": "bob@test.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["conversations"] == []


class TestSessionResetConversationIsolation:
    """Tests for Finding #1: session reset must create a new conversation_id."""

    @pytest.fixture
    def chat_service(self, repo):
        from unittest.mock import MagicMock

        from atlas.application.chat.service import ChatService

        llm = MagicMock()
        service = ChatService(
            llm=llm,
            conversation_repository=repo,
        )
        return service

    @pytest.mark.asyncio
    async def test_reset_generates_new_conversation_id(self, chat_service):
        """After reset, the session should have a different conversation_id."""
        from uuid import UUID as UUIDType
        from uuid import uuid4

        session_id = uuid4()
        await chat_service.create_session(session_id, "user@test.com")

        session = chat_service.sessions.get(session_id)
        conv_id_before = session.context.get("conversation_id")

        await chat_service.handle_reset_session(session_id, "user@test.com")

        session = chat_service.sessions.get(session_id)
        conv_id_after = session.context.get("conversation_id")

        assert conv_id_after is not None
        assert conv_id_after != conv_id_before
        # Verify it's a valid UUID string
        UUIDType(conv_id_after)

    @pytest.mark.asyncio
    async def test_save_after_reset_creates_new_conversation(self, chat_service, repo):
        """Saving after reset should not overwrite the previous conversation."""
        from uuid import uuid4

        from atlas.domain.messages.models import Message, MessageRole

        session_id = uuid4()
        user = "user@test.com"

        # First conversation
        await chat_service.create_session(session_id, user)
        session = chat_service.sessions.get(session_id)
        session.history.add_message(Message(role=MessageRole.USER, content="First question"))
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="First answer"))
        chat_service._save_conversation(session_id, user, "gpt-4")

        first_conv_id = session.context.get("conversation_id", str(session_id))
        first_conv = repo.get_conversation(first_conv_id, user)
        assert first_conv is not None
        assert len(first_conv["messages"]) == 2

        # Reset and start second conversation
        await chat_service.handle_reset_session(session_id, user)
        session = chat_service.sessions.get(session_id)
        second_conv_id = session.context.get("conversation_id")
        assert second_conv_id != first_conv_id

        session.history.add_message(Message(role=MessageRole.USER, content="Second question"))
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="Second answer"))
        chat_service._save_conversation(session_id, user, "gpt-4")

        # Verify both conversations exist independently
        first_after = repo.get_conversation(first_conv_id, user)
        second_after = repo.get_conversation(second_conv_id, user)

        assert first_after is not None
        assert second_after is not None
        assert first_after["messages"][0]["content"] == "First question"
        assert second_after["messages"][0]["content"] == "Second question"
        assert len(first_after["messages"]) == 2
        assert len(second_after["messages"]) == 2

    @pytest.mark.asyncio
    async def test_multiple_resets_create_separate_conversations(self, chat_service, repo):
        """Multiple reset cycles should each produce a separate conversation."""
        from uuid import uuid4

        from atlas.domain.messages.models import Message, MessageRole

        session_id = uuid4()
        user = "user@test.com"
        conv_ids = []

        for i in range(3):
            if i == 0:
                await chat_service.create_session(session_id, user)
            else:
                await chat_service.handle_reset_session(session_id, user)

            session = chat_service.sessions.get(session_id)
            session.history.add_message(Message(role=MessageRole.USER, content=f"Question {i}"))
            chat_service._save_conversation(session_id, user, "gpt-4")
            conv_ids.append(session.context.get("conversation_id", str(session_id)))

        # All conversation_ids should be unique
        assert len(set(conv_ids)) == 3

        # All should exist in the database
        all_convs = repo.list_conversations(user)
        assert len(all_convs) == 3

    @pytest.mark.asyncio
    async def test_restore_then_reset_preserves_original(self, chat_service, repo):
        """Restoring a conversation, then resetting, should not destroy the original."""
        from uuid import uuid4

        from atlas.domain.messages.models import Message, MessageRole

        session_id = uuid4()
        user = "user@test.com"

        # Save an initial conversation directly
        repo.save_conversation(
            conversation_id="original-conv",
            user_email=user,
            title="Original",
            model="gpt-4",
            messages=[{"role": "user", "content": "Original question"}],
        )

        # Restore it
        await chat_service.handle_restore_conversation(
            session_id, "original-conv",
            [{"role": "user", "content": "Original question"}],
            user,
        )

        # Reset to start a new conversation
        await chat_service.handle_reset_session(session_id, user)
        session = chat_service.sessions.get(session_id)
        new_conv_id = session.context.get("conversation_id")
        assert new_conv_id != "original-conv"

        # Save the new conversation
        session.history.add_message(Message(role=MessageRole.USER, content="New question"))
        chat_service._save_conversation(session_id, user, "gpt-4")

        # Original should still be intact
        original = repo.get_conversation("original-conv", user)
        assert original is not None
        assert original["title"] == "Original"
        assert original["messages"][0]["content"] == "Original question"


class TestConversationRoutes:
    """Test the REST API routes for conversation history."""

    @pytest.fixture
    def client(self, repo):
        from unittest.mock import patch

        from main import app
        from starlette.testclient import TestClient

        with patch("atlas.routes.conversation_routes._get_repo", return_value=repo):
            yield TestClient(app)

    def test_list_empty(self, client):
        resp = client.get("/api/conversations", headers={"X-User-Email": "test@test.com"})
        assert resp.status_code == 200
        assert resp.json()["conversations"] == []

    def test_list_after_save(self, client, repo):
        repo.save_conversation(
            conversation_id="api-conv1",
            user_email="test@test.com",
            title="API Test",
            model="gpt-4",
            messages=_make_messages(2),
        )
        resp = client.get("/api/conversations", headers={"X-User-Email": "test@test.com"})
        assert resp.status_code == 200
        convs = resp.json()["conversations"]
        assert len(convs) == 1
        assert convs[0]["title"] == "API Test"

    def test_get_by_id(self, client, repo):
        repo.save_conversation(
            conversation_id="api-conv2",
            user_email="test@test.com",
            title="Get By ID",
            model="gpt-4",
            messages=_make_messages(3),
        )
        resp = client.get("/api/conversations/api-conv2", headers={"X-User-Email": "test@test.com"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "api-conv2"
        assert len(data["messages"]) == 3

    def test_search(self, client, repo):
        repo.save_conversation(
            conversation_id="api-search",
            user_email="test@test.com",
            title="Python Tips",
            model="gpt-4",
            messages=[{"role": "user", "content": "Python tips"}],
        )
        resp = client.get("/api/conversations/search?q=Python", headers={"X-User-Email": "test@test.com"})
        assert resp.status_code == 200
        assert len(resp.json()["conversations"]) == 1

    def test_delete_single(self, client, repo):
        repo.save_conversation(
            conversation_id="api-del",
            user_email="test@test.com",
            title="Delete Me",
            model="gpt-4",
            messages=_make_messages(1),
        )
        resp = client.delete("/api/conversations/api-del", headers={"X-User-Email": "test@test.com"})
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_multiple(self, client, repo):
        for i in range(3):
            repo.save_conversation(
                conversation_id=f"api-mdel-{i}",
                user_email="test@test.com",
                title=f"Multi {i}",
                model="gpt-4",
                messages=_make_messages(1),
            )
        resp = client.post(
            "/api/conversations/delete",
            json={"ids": ["api-mdel-0", "api-mdel-1"]},
            headers={"X-User-Email": "test@test.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted_count"] == 2

    def test_update_title(self, client, repo):
        repo.save_conversation(
            conversation_id="api-title",
            user_email="test@test.com",
            title="Old",
            model="gpt-4",
            messages=_make_messages(1),
        )
        resp = client.patch(
            "/api/conversations/api-title/title",
            json={"title": "New Title"},
            headers={"X-User-Email": "test@test.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["updated"] is True

    def test_add_tag(self, client, repo):
        repo.save_conversation(
            conversation_id="api-tag",
            user_email="test@test.com",
            title="Tag Me",
            model="gpt-4",
            messages=_make_messages(1),
        )
        resp = client.post(
            "/api/conversations/api-tag/tags",
            json={"name": "important"},
            headers={"X-User-Email": "test@test.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "important"

    def test_feature_disabled_returns_empty(self, repo):
        """When repo returns None, endpoints return empty results gracefully."""
        from unittest.mock import patch

        from main import app
        from starlette.testclient import TestClient

        with patch("atlas.routes.conversation_routes._get_repo", return_value=None):
            client = TestClient(app)
            resp = client.get("/api/conversations", headers={"X-User-Email": "test@test.com"})
            assert resp.status_code == 200
            assert resp.json()["conversations"] == []
