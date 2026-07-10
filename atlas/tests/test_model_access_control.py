"""Tests for per-model group-based access control.

Covers the three required cases at both layers:
- omitted/empty ``groups`` -> everyone allowed (backward compatible)
- present ``groups`` with a matching group -> allowed
- present ``groups`` with a non-matching group -> denied / filtered

Layers exercised:
- helper: ``is_model_allowed`` / ``filter_authorized_models``
- execution: ``ChatOrchestrator._ensure_model_authorized`` raises for a
  crafted request naming a restricted model the user can't access.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.application.chat.orchestrator import ChatOrchestrator
from atlas.core.model_access import filter_authorized_models, is_model_allowed
from atlas.domain.errors import AuthorizationError
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository
from atlas.modules.config.models import LLMConfig, ModelConfig


async def _auth_check_admin_only(user_email: str, group: str) -> bool:
    """Mock membership: only ``admin@test.com`` is in the ``admin`` group."""
    return group == "admin" and user_email == "admin@test.com"


def _model(groups=None):
    return ModelConfig(
        model_name="m",
        model_url="http://x/v1",
        groups=groups or [],
    )


# --------------------------------------------------------------------------- #
# Config schema
# --------------------------------------------------------------------------- #

def test_model_config_groups_defaults_empty():
    """Omitting ``groups`` yields an empty list (open to everyone)."""
    m = ModelConfig(model_name="m", model_url="http://x/v1")
    assert m.groups == []


def test_model_config_groups_parsed_from_dict():
    """``groups`` is parsed onto ModelConfig via LLMConfig (yaml path)."""
    cfg = LLMConfig(models={"restricted": {
        "model_name": "m", "model_url": "http://x/v1", "groups": ["admin"],
    }})
    assert cfg.models["restricted"].groups == ["admin"]


# --------------------------------------------------------------------------- #
# Helper layer: is_model_allowed
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_omitted_groups_allows_everyone():
    """No groups configured -> allowed even for an anonymous user."""
    assert await is_model_allowed(_model(), "nobody@test.com", _auth_check_admin_only)
    assert await is_model_allowed(_model(), None, _auth_check_admin_only)


@pytest.mark.asyncio
async def test_matching_group_allowed():
    """User in a listed group is allowed."""
    assert await is_model_allowed(_model(["admin"]), "admin@test.com", _auth_check_admin_only)


@pytest.mark.asyncio
async def test_non_matching_group_denied():
    """User not in any listed group is denied."""
    assert not await is_model_allowed(_model(["admin"]), "user@test.com", _auth_check_admin_only)


@pytest.mark.asyncio
async def test_restricted_model_denies_anonymous():
    """A restriction always denies a missing user."""
    assert not await is_model_allowed(_model(["admin"]), None, _auth_check_admin_only)


@pytest.mark.asyncio
async def test_any_matching_group_is_sufficient():
    """Membership in ANY one of several listed groups grants access."""
    async def in_users(user_email, group):
        return group == "users"

    assert await is_model_allowed(_model(["admin", "users"]), "u@test.com", in_users)


@pytest.mark.asyncio
async def test_auth_check_error_fails_closed():
    """A membership-check exception is treated as not-a-member (fail closed)."""
    async def boom(user_email, group):
        raise RuntimeError("auth backend down")

    assert not await is_model_allowed(_model(["admin"]), "admin@test.com", boom)


@pytest.mark.asyncio
async def test_filter_authorized_models():
    """filter_authorized_models drops restricted models the user can't access."""
    models = {
        "open": _model(),
        "admin-only": _model(["admin"]),
        "also-open": _model([]),
    }
    for_user = await filter_authorized_models(models, "user@test.com", _auth_check_admin_only)
    assert set(for_user) == {"open", "also-open"}

    for_admin = await filter_authorized_models(models, "admin@test.com", _auth_check_admin_only)
    assert set(for_admin) == {"open", "admin-only", "also-open"}


# --------------------------------------------------------------------------- #
# Execution layer: orchestrator enforcement
# --------------------------------------------------------------------------- #

def _make_orchestrator_with_models(models: dict):
    """Build an orchestrator whose config_manager exposes the given models."""
    config_manager = MagicMock()
    config_manager.llm_config = LLMConfig(models=models)

    event_pub = MagicMock()
    event_pub.publish_warning = AsyncMock()
    repo = InMemorySessionRepository()

    plain_runner = MagicMock()
    plain_runner.run_streaming = AsyncMock(return_value={"mode": "plain"})
    rag_runner = MagicMock()
    rag_runner.run_streaming = AsyncMock(return_value={"mode": "rag"})
    tools_runner = MagicMock()
    tools_runner.run_streaming = AsyncMock(return_value={"mode": "tools"})
    agent_runner = MagicMock()
    agent_runner.run = AsyncMock(return_value={"mode": "agent"})

    orch = ChatOrchestrator(
        llm=MagicMock(),
        event_publisher=event_pub,
        session_repository=repo,
        plain_mode=plain_runner,
        rag_mode=rag_runner,
        tools_mode=tools_runner,
        agent_mode=agent_runner,
        config_manager=config_manager,
    )
    return orch, repo, plain_runner.run_streaming


async def _seed_session(repo, user_email):
    sid = uuid.uuid4()
    await repo.create(Session(id=sid, user_email=user_email))
    return sid


@pytest.mark.asyncio
async def test_execute_denies_unauthorized_model(monkeypatch):
    """A crafted request for a restricted model is rejected before any LLM call."""
    monkeypatch.setattr(
        "atlas.core.model_access.is_user_in_group", _auth_check_admin_only
    )
    orch, repo, plain_run = _make_orchestrator_with_models(
        {"admin-only": {"model_name": "m", "model_url": "http://x/v1", "groups": ["admin"]}}
    )
    sid = await _seed_session(repo, "user@test.com")

    with pytest.raises(AuthorizationError) as exc:
        await orch.execute(
            session_id=sid, content="hi", model="admin-only", user_email="user@test.com",
        )
    assert exc.value.code == "MODEL_ACCESS_DENIED"
    plain_run.assert_not_called()


@pytest.mark.asyncio
async def test_execute_allows_authorized_model(monkeypatch):
    """An authorized user reaches the mode runner for a restricted model."""
    monkeypatch.setattr(
        "atlas.core.model_access.is_user_in_group", _auth_check_admin_only
    )
    orch, repo, plain_run = _make_orchestrator_with_models(
        {"admin-only": {"model_name": "m", "model_url": "http://x/v1", "groups": ["admin"]}}
    )
    sid = await _seed_session(repo, "admin@test.com")

    await orch.execute(
        session_id=sid, content="hi", model="admin-only", user_email="admin@test.com",
    )
    plain_run.assert_called_once()


@pytest.mark.asyncio
async def test_execute_allows_unrestricted_model(monkeypatch):
    """A model without groups is reachable by everyone (backward compatible)."""
    monkeypatch.setattr(
        "atlas.core.model_access.is_user_in_group", _auth_check_admin_only
    )
    orch, repo, plain_run = _make_orchestrator_with_models(
        {"open": {"model_name": "m", "model_url": "http://x/v1"}}
    )
    sid = await _seed_session(repo, "anyone@test.com")

    await orch.execute(
        session_id=sid, content="hi", model="open", user_email="anyone@test.com",
    )
    plain_run.assert_called_once()


# --------------------------------------------------------------------------- #
# Listing layer: /api/config and /api/config/shell filter restricted models
# --------------------------------------------------------------------------- #

@pytest.fixture
def restricted_model_config():
    """Inject an ``admin``-restricted model into the live config for one test."""
    from atlas.infrastructure.app_factory import app_factory

    config_manager = app_factory.get_config_manager()
    models = config_manager.llm_config.models
    models["admin-only-model"] = ModelConfig(
        model_name="admin/only", model_url="http://x/v1", groups=["admin"],
    )
    try:
        yield
    finally:
        models.pop("admin-only-model", None)


def _model_names(resp):
    return {m["name"] for m in resp.json()["models"]}


def test_config_hides_restricted_model_from_non_member(restricted_model_config):
    """A non-admin user must not see an admin-restricted model in /api/config."""
    from main import app
    from starlette.testclient import TestClient

    client = TestClient(app)
    # user@example.com is in {users, mcp_basic} but NOT admin (mock groups).
    resp = client.get("/api/config", headers={"X-User-Email": "user@example.com"})
    assert resp.status_code == 200
    assert "admin-only-model" not in _model_names(resp)


def test_config_shows_restricted_model_to_member(restricted_model_config):
    """An admin user sees the admin-restricted model in both config endpoints."""
    from main import app
    from starlette.testclient import TestClient

    client = TestClient(app)
    # test@test.com is in the admin mock group.
    for path in ("/api/config", "/api/config/shell"):
        resp = client.get(path, headers={"X-User-Email": "test@test.com"})
        assert resp.status_code == 200
        assert "admin-only-model" in _model_names(resp), path


def test_config_shell_hides_restricted_model_from_non_member(restricted_model_config):
    """The fast shell endpoint applies the same filter as /api/config."""
    from main import app
    from starlette.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/config/shell", headers={"X-User-Email": "user@example.com"})
    assert resp.status_code == 200
    assert "admin-only-model" not in _model_names(resp)
