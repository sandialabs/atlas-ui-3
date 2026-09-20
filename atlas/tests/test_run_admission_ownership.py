"""Run admission refuses a stored conversation id the caller does not own.

Issue #958. A chat frame naming a *stored* conversation owned by another
user used to be admitted as a tracked run first: the caller received
``run_started`` and ``run_status`` frames, and only then the turn failed
with an ``authorization`` error -- leaving a ``failed`` run in their
snapshot for a conversation they never owned. Nothing leaked, but the
caller's run machinery recorded activity on a foreign id.

The fix settles ownership *before* admission, at the transport, through the
same ``ChatService`` check the turn performs later. These tests pin the
transport helper's decisions against a real conversation repository backed
by a temp DuckDB, so the stored-record branch is exercised for real.
"""

from unittest.mock import MagicMock

import pytest
from main import _conversation_access_error

from atlas.application.chat.service import ChatService
from atlas.domain.errors import AuthorizationError
from atlas.modules.chat_history import (
    ConversationRepository,
    get_session_factory,
    init_database,
)
from atlas.modules.chat_history.database import reset_engine

OWNER = "owner@example.com"
OTHER = "other@example.com"
STORED_ID = "owner-stored-conv"
MINTED_ID = "00000000-0000-4000-8000-000000000000"

# What the service raises for a refused id; the transport refusal must be
# indistinguishable from it apart from the missing run around it.
_SERVICE_MESSAGE = "Conversation not found or access denied"


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def repo(tmp_path):
    init_database(f"duckdb:///{tmp_path / 'admission_ownership.db'}")
    return ConversationRepository(get_session_factory())


@pytest.fixture
def stored(repo):
    """One conversation, stored under OWNER, as user A's saved turn leaves it."""
    record = repo.save_conversation(
        conversation_id=STORED_ID,
        user_email=OWNER,
        title="A's conversation",
        model="test-model",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert record is not None
    return STORED_ID


class _RepoWithoutOwnerLookup:
    """A configured repo that cannot answer ownership questions."""


def _service_with(conversation_repository):
    return ChatService(
        llm=MagicMock(),
        tool_manager=MagicMock(),
        connection=MagicMock(),
        config_manager=MagicMock(),
        session_repository=MagicMock(),
        conversation_repository=conversation_repository,
    )


def test_a_stored_foreign_conversation_is_refused(repo, stored):
    service = _service_with(repo)

    refusal = _conversation_access_error(service, STORED_ID, OTHER)

    assert refusal is not None
    assert refusal["type"] == "error"
    assert refusal["error_type"] == "authorization"
    assert refusal["message"] == _SERVICE_MESSAGE
    assert refusal["conversation_id"] == STORED_ID


def test_the_owner_s_own_stored_conversation_passes(repo, stored):
    service = _service_with(repo)

    assert _conversation_access_error(service, STORED_ID, OWNER) is None


def test_an_unstored_id_passes(repo):
    """A minted id, or a run still in flight: nothing is stored to check."""
    service = _service_with(repo)

    assert _conversation_access_error(service, MINTED_ID, OTHER) is None


def test_a_missing_user_is_refused(repo, stored):
    service = _service_with(repo)

    refusal = _conversation_access_error(service, STORED_ID, None)

    assert refusal is not None and refusal["error_type"] == "authorization"


def test_a_repo_without_owner_lookup_fails_closed():
    """A repo that cannot answer ownership questions cannot be trusted."""
    service = _service_with(_RepoWithoutOwnerLookup())

    refusal = _conversation_access_error(service, "any-conv", OWNER)

    assert refusal is not None and refusal["error_type"] == "authorization"


def test_no_repository_passes():
    """Without persistence there is nothing to leak and nothing to check."""
    service = _service_with(None)

    assert _conversation_access_error(service, "any-conv", OWNER) is None


def test_the_refused_frame_matches_what_the_service_raises(repo, stored):
    """Wire-equivalence: the pre-admission refusal and the service's late
    rejection must read identically to the client (issue #958)."""
    service = _service_with(repo)

    refusal = _conversation_access_error(service, STORED_ID, OTHER)
    try:
        service.validate_conversation_id_owner(STORED_ID, OTHER)
        raised = None
    except AuthorizationError as e:
        raised = e

    assert raised is not None
    assert refusal["message"] == str(raised.message)
    assert refusal["error_type"] == "authorization"
