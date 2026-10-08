"""Required compliance level mode (FEATURE_COMPLIANCE_LEVEL_REQUIRED).

With the mode on, the UI has no "All Levels" state and the server refuses a
chat turn that carries no valid compliance level. These tests pin the server
half: the derived settings gate, the chat-service enforcement, the default
level resolution and what the config endpoints expose to the UI.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from main import app
from starlette.testclient import TestClient

from atlas.application.chat.service import ChatService
from atlas.core.compliance import ComplianceLevelManager
from atlas.domain.errors import ValidationError
from atlas.infrastructure.app_factory import app_factory
from atlas.modules.config.settings import AppSettings


def _make_service(*, enabled=True, required=True):
    sessions = {}

    async def _get(session_id):
        return sessions.get(session_id)

    async def _store(session):
        sessions[session.id] = session

    repo = MagicMock()
    repo.get = AsyncMock(side_effect=_get)
    repo.create = AsyncMock(side_effect=_store)
    repo.update = AsyncMock(side_effect=_store)

    service = ChatService(
        llm=MagicMock(),
        tool_manager=MagicMock(),
        connection=MagicMock(),
        config_manager=MagicMock(),
        session_repository=repo,
    )
    settings = service.config_manager.app_settings
    settings.feature_compliance_levels_enabled = enabled
    settings.compliance_level_required_effective = enabled and required
    service.config_manager.llm_config.models = {
        "test-model": MagicMock(compliance_level=None)
    }
    return service


async def _send(service, **kwargs):
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(return_value={"type": "done"})
    with patch.object(service, "_get_orchestrator", return_value=orchestrator):
        await service.handle_chat_message(
            session_id=uuid4(),
            content="hello",
            model="test-model",
            user_email="user@test.com",
            **kwargs,
        )
    return orchestrator


# --- settings gate ----------------------------------------------------------

@pytest.mark.parametrize(
    "enabled,required,expected",
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_required_effective_needs_compliance_feature(monkeypatch, enabled, required, expected):
    monkeypatch.setenv("FEATURE_COMPLIANCE_LEVELS_ENABLED", str(enabled).lower())
    monkeypatch.setenv("FEATURE_COMPLIANCE_LEVEL_REQUIRED", str(required).lower())
    settings = AppSettings()
    assert settings.compliance_level_required_effective is expected


def test_required_mode_reads_from_environment(monkeypatch):
    monkeypatch.setenv("FEATURE_COMPLIANCE_LEVELS_ENABLED", "true")
    monkeypatch.setenv("FEATURE_COMPLIANCE_LEVEL_REQUIRED", "true")
    monkeypatch.setenv("COMPLIANCE_DEFAULT_LEVEL", "Internal")
    settings = AppSettings()
    assert settings.compliance_level_required_effective is True
    assert settings.compliance_default_level == "Internal"


# --- chat service enforcement -------------------------------------------------

@pytest.mark.asyncio
async def test_turn_without_level_is_refused_when_required():
    service = _make_service()
    with pytest.raises(ValidationError, match="compliance level is required"):
        await _send(service)


@pytest.mark.asyncio
async def test_refused_turn_never_reaches_the_orchestrator():
    service = _make_service()
    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(return_value={"type": "done"})
    with patch.object(service, "_get_orchestrator", return_value=orchestrator):
        with pytest.raises(ValidationError):
            await service.handle_chat_message(
                session_id=uuid4(),
                content="hello",
                model="test-model",
                user_email="user@test.com",
                compliance_level="",
            )
    orchestrator.execute.assert_not_called()


@pytest.mark.asyncio
async def test_refused_turn_leaves_the_session_untouched():
    """The check runs before session lookup, hydration or rebinding."""
    service = _make_service()
    with pytest.raises(ValidationError):
        await _send(service, conversation_id="conv-1")
    service.session_repository.get.assert_not_called()
    service.session_repository.create.assert_not_called()


@pytest.mark.asyncio
async def test_undefined_level_is_refused_when_required():
    """A level the deployment does not define validates to None: refused."""
    service = _make_service()
    with pytest.raises(ValidationError):
        await _send(service, compliance_level="NotARealLevel")


@pytest.mark.asyncio
async def test_any_level_is_refused_when_no_definitions_load(tmp_path):
    """Permissive validation must not let an invented level through."""
    service = _make_service()
    empty = ComplianceLevelManager(config_path=tmp_path / "missing.json")
    with patch("atlas.core.compliance.get_compliance_manager", return_value=empty):
        with pytest.raises(ValidationError, match="no compliance levels configured"):
            await _send(service, compliance_level="anything")


@pytest.mark.asyncio
async def test_defined_level_proceeds_when_required():
    service = _make_service()
    # The model must also be approved for the level (issue #1032).
    service.config_manager.llm_config.models = {
        "test-model": MagicMock(compliance_level=None, allowed_data_classifications=["Public"])
    }
    orchestrator = await _send(service, compliance_level="Public")
    orchestrator.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_level_allowed_when_not_required():
    service = _make_service(required=False)
    orchestrator = await _send(service)
    orchestrator.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_required_flag_ignored_when_compliance_disabled():
    service = _make_service(enabled=False, required=True)
    orchestrator = await _send(service)
    orchestrator.execute.assert_awaited_once()


# --- default level resolution -----------------------------------------------

def _manager(tmp_path, names):
    path = tmp_path / "compliance-levels.json"
    path.write_text(json.dumps({
        "levels": [
            {"name": n, "aliases": [n.lower()], "allowed_with": [n]} for n in names
        ]
    }))
    return ComplianceLevelManager(config_path=path)


def test_default_level_uses_configured_level(tmp_path):
    assert _manager(tmp_path, ["Public", "Internal"]).resolve_default_level("Internal") == "Internal"


def test_default_level_resolves_alias(tmp_path):
    assert _manager(tmp_path, ["Public", "Internal"]).resolve_default_level("internal") == "Internal"


def test_default_level_falls_back_to_first_defined(tmp_path):
    mgr = _manager(tmp_path, ["Public", "Internal"])
    assert mgr.resolve_default_level(None) == "Public"
    assert mgr.resolve_default_level("Bogus") == "Public"


def test_default_level_empty_string_matches_none(tmp_path):
    """The test-session pin uses "" for the default level; it must behave as None.

    ``conftest`` pins ``COMPLIANCE_DEFAULT_LEVEL`` to an empty string (rather
    than clearing it) so ``load_dotenv(override=False)`` cannot restore a
    developer value. Production defaults the setting to ``None``; both must
    resolve the same way so the test pin does not create a distinct path.
    """
    mgr = _manager(tmp_path, ["Public", "Internal"])
    assert mgr.resolve_default_level("") == mgr.resolve_default_level(None)


def test_default_level_none_without_definitions(tmp_path):
    mgr = ComplianceLevelManager(config_path=tmp_path / "missing.json")
    assert mgr.resolve_default_level("Public") is None


# --- config endpoints ---------------------------------------------------------

@pytest.fixture
def required_mode(monkeypatch):
    settings = app_factory.get_config_manager().app_settings
    monkeypatch.setattr(settings, "feature_compliance_levels_enabled", True)
    monkeypatch.setattr(settings, "feature_compliance_level_required", True)
    monkeypatch.setattr(settings, "compliance_default_level", "Internal")
    return settings


def _get(path):
    settings = app_factory.get_config_manager().app_settings
    return TestClient(app).get(path, headers={"X-User-Email": settings.test_user})


def test_config_shell_exposes_required_flag(required_mode):
    resp = _get("/api/config/shell")
    assert resp.status_code == 200
    assert resp.json()["features"]["compliance_level_required"] is True


def test_compliance_levels_endpoint_returns_default_level(required_mode):
    resp = _get("/api/compliance-levels")
    assert resp.status_code == 200
    data = resp.json()
    assert data["default_level"] == "Internal"
    assert data["default_level"] in data["all_level_names"]


def test_no_default_level_when_not_required(monkeypatch):
    settings = app_factory.get_config_manager().app_settings
    monkeypatch.setattr(settings, "feature_compliance_level_required", False)
    resp = _get("/api/compliance-levels")
    assert resp.status_code == 200
    assert resp.json()["default_level"] is None
    shell = _get("/api/config/shell").json()
    assert shell["features"]["compliance_level_required"] is False


# --- non-browser clients pass the level through ------------------------------

@pytest.mark.asyncio
async def test_atlas_client_forwards_compliance_level():
    from atlas.atlas_client import AtlasClient

    chat_service = MagicMock()
    chat_service.handle_chat_message = AsyncMock(return_value={})
    client = AtlasClient()
    client._initialized = True
    client._factory = MagicMock()
    client._factory.create_chat_service.return_value = chat_service

    await client.chat("hi", model="m", user_email="u@test.com", compliance_level="Internal")

    assert chat_service.handle_chat_message.await_args.kwargs["compliance_level"] == "Internal"


@pytest.mark.asyncio
@pytest.mark.parametrize("extra,expected", [(["--compliance-level", "Internal"], "Internal"), ([], None)])
async def test_cli_forwards_compliance_level(monkeypatch, extra, expected):
    import atlas_chat_cli

    seen = {}

    class RecordingClient:
        async def chat(self, *args, **kwargs):
            seen.update(kwargs)
            return MagicMock(message="ok", **{"to_dict.return_value": {}})

        async def cleanup(self):
            pass

    monkeypatch.setattr(atlas_chat_cli, "AtlasClient", lambda: RecordingClient())
    args = atlas_chat_cli.build_parser().parse_args(["hi", "--json", *extra])

    assert await atlas_chat_cli.run(args) == 0
    assert seen["compliance_level"] == expected
