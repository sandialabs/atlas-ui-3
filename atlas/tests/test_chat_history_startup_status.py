"""Chat-history startup outcome reporting (issue #992).

When chat history is enabled but fails to initialize, ``/api/health`` must
report ``status: "degraded"`` (still HTTP 200, matching the session-store
convention from issue #1091) with ``checks.chat_history = "failed"``, and the
conversation-family routes must say "Chat history failed to initialize"
instead of "Chat history is not enabled". The failure detail stays
server-side, in ``app_factory.startup_errors``.
"""

from unittest.mock import patch

import pytest

from atlas.infrastructure.app_factory import AppFactory, app_factory
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.config.config_manager import config_manager

FAILED_MESSAGE = "Chat history failed to initialize"
NOT_ENABLED_MESSAGE = "Chat history is not enabled"
TEST_USER = config_manager.app_settings.test_user


@pytest.fixture(autouse=True)
def _clean_engine():
    """Keep the global chat-history engine out of other tests' state."""
    reset_engine()
    yield
    reset_engine()


def _bare_factory(**setting_overrides):
    """An AppFactory shell wired for ``_init_chat_history``, without full DI.

    The real constructor pulls in MCP, RAG, LLM and storage wiring these
    tests do not exercise; a bare instance exercises only the chat-history
    startup path. Settings come from a copy of the real ones so
    ``PoolSettings.from_app_settings`` sees every field it reads.
    """
    from types import SimpleNamespace

    settings = app_factory.config_manager.app_settings.model_copy(deep=True)
    for key, value in setting_overrides.items():
        setattr(settings, key, value)
    factory = AppFactory.__new__(AppFactory)
    factory.config_manager = SimpleNamespace(app_settings=settings)
    factory.startup_outcomes = {}
    factory.startup_errors = {}
    factory.conversation_repository = None
    factory.user_prompt_repository = None
    factory.workspace_repository = None
    return factory


class TestStartupOutcomes:
    def test_disabled_feature_records_disabled(self):
        factory = _bare_factory(feature_chat_history_enabled=False)
        factory._init_chat_history()
        assert factory.startup_outcomes == {"chat_history": "disabled"}
        assert factory.conversation_repository is None
        assert factory.startup_errors == {}

    def test_successful_init_records_ok(self, tmp_path):
        factory = _bare_factory(
            feature_chat_history_enabled=True,
            chat_history_db_url=f"duckdb:///{tmp_path / 'chat_history_ok.db'}",
        )
        factory._init_chat_history()
        assert factory.startup_outcomes == {"chat_history": "ok"}
        assert factory.conversation_repository is not None
        assert factory.user_prompt_repository is not None
        assert factory.workspace_repository is not None
        assert factory.startup_errors == {}

    def test_failed_init_records_failed_and_keeps_repositories_unset(self, tmp_path):
        """A failed init is recorded, repositories stay None, detail is kept.

        Repositories staying None is what stops server-side saving on a
        broken database: ChatService skips persistence when they are unset.
        """
        factory = _bare_factory(
            feature_chat_history_enabled=True,
            chat_history_db_url=f"duckdb:///{tmp_path / 'unused.db'}",
        )
        with patch(
            "atlas.modules.chat_history.init_database",
            side_effect=RuntimeError("connection refused"),
        ):
            factory._init_chat_history()
        assert factory.startup_outcomes == {"chat_history": "failed"}
        assert factory.conversation_repository is None
        assert factory.user_prompt_repository is None
        assert factory.workspace_repository is None
        assert factory.startup_errors == {"chat_history": "connection refused"}


class TestUnavailableReason:
    @pytest.mark.parametrize(
        "outcome, message",
        [
            ("failed", FAILED_MESSAGE),
            ("disabled", NOT_ENABLED_MESSAGE),
            ("ok", NOT_ENABLED_MESSAGE),
            (None, NOT_ENABLED_MESSAGE),
        ],
    )
    def test_reason_matches_outcome(self, outcome, message):
        factory = _bare_factory()
        factory.startup_outcomes = {} if outcome is None else {"chat_history": outcome}
        assert factory.chat_history_unavailable_reason() == message


class _FakeUnreachableRedis:
    async def health(self):
        return {"backend": "redis", "status": "unavailable"}


class TestHealthEndpoint:
    def _get_health(self):
        from main import app
        from starlette.testclient import TestClient

        return TestClient(app).get("/api/health")

    def test_failed_chat_history_is_degraded_but_still_200(self):
        with patch.object(app_factory, "startup_outcomes", {"chat_history": "failed"}), \
                patch.object(app_factory, "redis", None):
            resp = self._get_health()
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["checks"]["chat_history"] == "failed"
        # Unauthenticated endpoint: the outcome only, never the detail.
        assert "startup_errors" not in body

    @pytest.mark.parametrize("outcome", ["ok", "disabled"])
    def test_non_failed_outcomes_stay_healthy(self, outcome):
        with patch.object(app_factory, "startup_outcomes", {"chat_history": outcome}), \
                patch.object(app_factory, "redis", None):
            resp = self._get_health()
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["checks"]["chat_history"] == outcome

    def test_degraded_chat_history_and_unreachable_store_stay_200(self):
        """Both degraded signals compose; the endpoint never fails the probe."""
        with patch.object(app_factory, "startup_outcomes", {"chat_history": "failed"}), \
                patch.object(app_factory, "redis", _FakeUnreachableRedis()):
            resp = self._get_health()
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["checks"]["chat_history"] == "failed"
        assert body["session_store"]["status"] == "unavailable"


class TestConversationRouteMessages:
    def _get(self, path):
        from main import app
        from starlette.testclient import TestClient

        return TestClient(app).get(path, headers={"X-User-Email": TEST_USER})

    def test_list_reports_failed_initialization(self):
        with patch("atlas.routes.conversation_routes._get_repo", return_value=None), \
                patch.object(app_factory, "startup_outcomes", {"chat_history": "failed"}):
            resp = self._get("/api/conversations")
        assert resp.status_code == 200
        body = resp.json()
        assert body["conversations"] == []
        assert body["error"] == FAILED_MESSAGE

    def test_get_reports_failed_initialization(self):
        with patch("atlas.routes.conversation_routes._get_repo", return_value=None), \
                patch.object(app_factory, "startup_outcomes", {"chat_history": "failed"}):
            resp = self._get("/api/conversations/some-id")
        assert resp.status_code == 200
        assert resp.json()["error"] == FAILED_MESSAGE

    def test_list_keeps_not_enabled_message_when_disabled(self):
        with patch("atlas.routes.conversation_routes._get_repo", return_value=None), \
                patch.object(app_factory, "startup_outcomes", {"chat_history": "disabled"}):
            resp = self._get("/api/conversations")
        assert resp.status_code == 200
        assert resp.json()["error"] == NOT_ENABLED_MESSAGE


class TestWorkspaceRouteMessages:
    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from atlas.core.log_sanitizer import get_current_user
        from atlas.routes import workspace_routes

        app = FastAPI()
        app.include_router(workspace_routes.router)
        app.dependency_overrides[get_current_user] = lambda: "alice@test.com"
        return app, TestClient(app)

    def test_list_reports_failed_initialization(self):
        from atlas.routes import workspace_routes

        app, client = self._client()
        with patch.object(workspace_routes, "_workspaces_enabled", lambda: True), \
                patch.object(workspace_routes, "_get_repo", lambda: None), \
                patch.object(app_factory, "startup_outcomes", {"chat_history": "failed"}):
            resp = client.get("/api/workspaces")
        assert resp.status_code == 200
        assert resp.json()["error"] == FAILED_MESSAGE

    def test_create_returns_503_with_failed_message(self):
        from atlas.routes import workspace_routes

        app, client = self._client()
        with patch.object(workspace_routes, "_workspaces_enabled", lambda: True), \
                patch.object(workspace_routes, "_get_repo", lambda: None), \
                patch.object(app_factory, "startup_outcomes", {"chat_history": "failed"}):
            resp = client.post("/api/workspaces", json={"name": "w"})
        assert resp.status_code == 503
        assert resp.json()["detail"] == FAILED_MESSAGE


class TestUserPromptRouteMessages:
    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from atlas.core.log_sanitizer import get_current_user
        from atlas.routes import user_prompt_routes

        app = FastAPI()
        app.include_router(user_prompt_routes.router)
        app.dependency_overrides[get_current_user] = lambda: "alice@test.com"
        return app, TestClient(app)

    def test_list_reports_failed_initialization(self):
        from atlas.routes import user_prompt_routes

        app, client = self._client()
        with patch.object(user_prompt_routes, "_custom_prompts_enabled", lambda: True), \
                patch.object(user_prompt_routes, "_get_repo", lambda: None), \
                patch.object(app_factory, "startup_outcomes", {"chat_history": "failed"}):
            resp = client.get("/api/user-prompts")
        assert resp.status_code == 200
        assert resp.json()["error"] == FAILED_MESSAGE

    def test_create_returns_503_with_failed_message(self):
        from atlas.routes import user_prompt_routes

        app, client = self._client()
        with patch.object(user_prompt_routes, "_custom_prompts_enabled", lambda: True), \
                patch.object(user_prompt_routes, "_get_repo", lambda: None), \
                patch.object(app_factory, "startup_outcomes", {"chat_history": "failed"}):
            resp = client.post("/api/user-prompts", json={"title": "T", "content": "hi"})
        assert resp.status_code == 503
        assert resp.json()["detail"] == FAILED_MESSAGE
