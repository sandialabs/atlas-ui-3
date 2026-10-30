"""The agent-runs REST API for a trusted external service (issue #1105).

These pin the boundaries the issue cares about, end to end through HTTP:
only the master key opens the API (and nothing else stands in for it), a run
is admitted under the *target user's* access and keeps running after the
request returns, the API can only see and act on runs it started, approval-
mandatory tools are never waved through, and idempotent retries and
concurrent requests do not duplicate work.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from atlas.application.chat.runs.context import clear_current_run
from atlas.application.chat.runs.external_runs import reset_idempotency_cache
from atlas.application.chat.runs.registry import RunStatus, get_run_registry, reset_run_registry
from atlas.domain.errors import ValidationError
from atlas.routes.agent_run_routes import get_factory, is_agent_runs_path
from atlas.routes.agent_run_routes import router as agent_run_router

KEY = "k" * 40
AUTH = {"Authorization": f"Bearer {KEY}"}
USER = "user@example.com"

WORKSPACE = {
    "id": "ws-1",
    "name": "Research",
    "config": {
        "selected_tools": ["math_add", "filesystem_read"],
        "selected_data_sources": [],
        "rag_enabled": False,
    },
}


class _Workspaces:
    """Scoped by user like the real WorkspaceRepository."""

    def __init__(self, owner=USER):
        self.owner = owner
        self.rows = [WORKSPACE]

    def get_workspace(self, workspace_id, user_email):
        if user_email != self.owner:
            return None
        return next((r for r in self.rows if r["id"] == workspace_id), None)

    def list_workspaces(self, user_email):
        return list(self.rows) if user_email == self.owner else []


class _ChatService:
    """Stands in for ChatService: records the turn, finishes on ``release``."""

    def __init__(self, connection, authorized=("math_add",), admission_error=None):
        self.connection = connection
        self.calls = []
        self.tool_authorization = SimpleNamespace(filter_authorized_tools=self._filter)
        self._authorized = list(authorized)
        self.agent_mode = SimpleNamespace(agent_loop_factory=SimpleNamespace(skip_approval=False))
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.admission_error = admission_error
        self.session_repository = SimpleNamespace(get=self._session)

    async def _filter(self, tools, user_email):
        return [t for t in tools if t in self._authorized]

    async def check_turn_admission(self, **kwargs):
        if self.admission_error:
            raise ValidationError(self.admission_error)

    async def _session(self, session_id):
        return SimpleNamespace(
            history=SimpleNamespace(
                messages=[
                    SimpleNamespace(role=SimpleNamespace(value="user"), content="go", metadata={}),
                    SimpleNamespace(
                        role=SimpleNamespace(value="assistant"), content="the answer", metadata={}
                    ),
                ]
            )
        )

    async def handle_chat_message(self, **kwargs):
        self.calls.append(kwargs)
        self.started.set()
        await self.release.wait()


def _settings(**overrides):
    base = dict(
        feature_agent_runs_api_enabled=True,
        agent_runs_api_key=KEY,
        feature_chat_history_enabled=True,
        feature_agent_mode_available=True,
        atlas_launch_max_depth=2,
        atlas_launch_max_children_per_run=2,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Factory:
    def __init__(self, settings=None, service_kwargs=None):
        self.settings = settings or _settings()
        self.workspace_repository = _Workspaces()
        self.conversation_repository = object()
        self.models = {"gpt-4o": SimpleNamespace(groups=None)}
        self.service_kwargs = service_kwargs or {}
        self.services = []

    def get_config_manager(self):
        return SimpleNamespace(
            app_settings=self.settings, llm_config=SimpleNamespace(models=self.models)
        )

    def create_chat_service(self, connection=None):
        service = _ChatService(connection, **self.service_kwargs)
        self.services.append(service)
        return service

    def release_all(self):
        for service in self.services:
            service.release.set()


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    reset_run_registry()
    reset_idempotency_cache()
    clear_current_run()
    # No tool is approval-mandatory unless a test says so.
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.requires_approval",
        lambda name, cfg: (True, True, False),
    )
    yield
    reset_run_registry()
    reset_idempotency_cache()


@pytest.fixture
def factory():
    f = _Factory()
    yield f
    f.release_all()


def _app(factory):
    app = FastAPI()
    app.include_router(agent_run_router)
    app.dependency_overrides[get_factory] = lambda: factory
    return app


def _client(factory):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(factory)), base_url="http://t")


LAUNCH = {"user": USER, "workspace": "Research", "model": "gpt-4o", "message": "Summarize the build"}


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


async def test_the_api_does_not_exist_when_disabled():
    factory = _Factory(settings=_settings(feature_agent_runs_api_enabled=False))
    async with _client(factory) as client:
        response = await client.post("/api/agent-runs", json=LAUNCH, headers=AUTH)
    assert response.status_code == 404
    assert factory.services == []


@pytest.mark.parametrize("key", [None, "", "short-key"])
async def test_enabled_without_a_strong_key_fails_closed(key):
    factory = _Factory(settings=_settings(agent_runs_api_key=key))
    async with _client(factory) as client:
        response = await client.post(
            "/api/agent-runs", json=LAUNCH, headers={"Authorization": f"Bearer {key or ''}"}
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "not_configured"
    assert factory.services == []


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-key-wrong-key-wrong-key-wrong"},
        {"Authorization": KEY},
        {"Authorization": f"Basic {KEY}"},
        {"X-User-Email": USER},
    ],
)
async def test_every_route_requires_the_key(factory, headers):
    async with _client(factory) as client:
        for method, path in [
            ("POST", "/api/agent-runs"),
            ("GET", "/api/agent-runs"),
            ("GET", f"/api/agent-runs/options?user={USER}"),
            ("GET", "/api/agent-runs/some-run"),
            ("POST", "/api/agent-runs/some-run/follow-up"),
            ("POST", "/api/agent-runs/some-run/cancel"),
        ]:
            response = await client.request(method, path, json=LAUNCH, headers=headers)
            assert response.status_code == 401, path
            assert response.headers["www-authenticate"] == "Bearer"
    assert factory.services == []


def test_agent_runs_path_matching_is_exact_on_the_prefix():
    assert is_agent_runs_path("/api/agent-runs")
    assert is_agent_runs_path("/api/agent-runs/abc/cancel")
    assert not is_agent_runs_path("/api/agent-runsx")
    assert not is_agent_runs_path("/api/conversations")


async def test_browser_auth_cannot_reach_the_api_and_the_proxy_secret_does_not_gate_it(factory):
    """Through the real AuthMiddleware: identity headers are not a credential here."""
    from atlas.core.middleware import AuthMiddleware

    app = _app(factory)
    app.add_middleware(
        AuthMiddleware,
        debug_mode=False,
        proxy_secret_enabled=True,
        proxy_secret="proxy-secret",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        browser = await client.get(
            "/api/agent-runs",
            headers={"X-User-Email": USER, "X-Proxy-Secret": "proxy-secret"},
        )
        service = await client.get("/api/agent-runs", headers=AUTH)
    assert browser.status_code == 401
    assert service.status_code == 200


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------


async def test_launch_returns_a_handle_and_the_run_outlives_the_request(factory):
    async with _client(factory) as client:
        response = await client.post("/api/agent-runs", json=LAUNCH, headers=AUTH)
        assert response.status_code == 202
        handle = response.json()
        assert response.headers["location"] == handle["status_url"]
        assert handle["status_url"] == f"/api/agent-runs/{handle['run_id']}"
        assert handle["user"] == USER
        assert handle["workspace"] == "Research"
        assert handle["tools"] == ["math_add"]  # re-filtered through the user's ACLs

        await factory.services[0].started.wait()
        record = get_run_registry().get(handle["run_id"])
        assert record.status is RunStatus.RUNNING
        # A root run: top-level in the user's history, no parent lineage.
        assert record.depth == 0
        assert record.parent_run_id is None
        assert record.parent_conversation_id is None
        call = factory.services[0].calls[0]
        assert call["user_email"] == USER
        assert call["content"] == "Summarize the build"
        assert call["incognito"] is False
        assert call["agent_mode"] is True
        assert call["conversation_id"] == handle["conversation_id"]
        assert factory.services[0].agent_mode.agent_loop_factory.skip_approval is True

        factory.release_all()
        await _settle()
        status = (await client.get(handle["status_url"], headers=AUTH)).json()
    assert status["status"] == "completed"
    assert status["result"] == "the answer"
    assert status["result_status"] == "available"


@pytest.mark.parametrize(
    "overrides, status, code",
    [
        ({"workspace": "Nope"}, 404, "workspace_not_found"),
        ({"user": "someone-else@example.com"}, 404, "workspace_not_found"),
        ({"model": "secret-model"}, 422, "model_not_available"),
        ({"message": "x" * 20001}, 400, "invalid_request"),
    ],
)
async def test_invalid_launches_are_refused_with_structured_errors(factory, overrides, status, code):
    async with _client(factory) as client:
        response = await client.post("/api/agent-runs", json={**LAUNCH, **overrides}, headers=AUTH)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert get_run_registry().all_records() == []


async def test_another_users_workspace_is_not_found_rather_than_forbidden(factory):
    """Impersonating the wrong user reveals nothing about the right one's workspaces."""
    async with _client(factory) as client:
        response = await client.post(
            "/api/agent-runs", json={**LAUNCH, "user": "other@example.com", "workspace": "ws-1"},
            headers=AUTH,
        )
    assert response.status_code == 404
    assert "Research" not in response.text


async def test_a_workspace_with_no_tools_the_user_may_use_is_refused():
    factory = _Factory(service_kwargs={"authorized": ()})
    async with _client(factory) as client:
        response = await client.post("/api/agent-runs", json=LAUNCH, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "no_authorized_tools"


async def test_the_domain_whitelist_applies_to_the_target_user(monkeypatch):
    from atlas.application.chat.runs import external_runs

    whitelist = SimpleNamespace(is_domain_allowed=lambda email: email.endswith("@example.com"))
    monkeypatch.setattr(external_runs, "_domain_whitelist", lambda: whitelist)
    factory = _Factory(settings=_settings(feature_domain_whitelist_enabled=True))
    async with _client(factory) as client:
        refused = await client.post(
            "/api/agent-runs", json={**LAUNCH, "user": "intruder@elsewhere.org"}, headers=AUTH
        )
        options = await client.get("/api/agent-runs/options?user=intruder@elsewhere.org", headers=AUTH)
        allowed = await client.post("/api/agent-runs", json=LAUNCH, headers=AUTH)
    factory.release_all()
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "user_not_allowed"
    assert options.status_code == 403
    assert allowed.status_code == 202


async def test_the_whitelist_middleware_lets_the_service_through_to_its_key_check(factory):
    from atlas.core.domain_whitelist_middleware import DomainWhitelistMiddleware

    app = _app(factory)
    app.add_middleware(DomainWhitelistMiddleware, auth_redirect_url="/auth")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        assert (await client.get("/api/agent-runs", headers=AUTH)).status_code == 200
        assert (await client.get("/api/agent-runs")).status_code == 401


async def test_missing_persistence_is_refused(factory):
    factory.conversation_repository = None
    async with _client(factory) as client:
        response = await client.post("/api/agent-runs", json=LAUNCH, headers=AUTH)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "persistence_unavailable"


async def test_a_classification_mismatch_is_refused_before_admission():
    factory = _Factory(service_kwargs={"admission_error": "Not approved for SECRET data: gpt-4o."})
    async with _client(factory) as client:
        response = await client.post(
            "/api/agent-runs", json={**LAUNCH, "compliance_level": "SECRET"}, headers=AUTH
        )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "classification_refused"
    assert get_run_registry().all_records() == []


async def test_the_compliance_level_travels_with_the_run(factory):
    async with _client(factory) as client:
        response = await client.post(
            "/api/agent-runs", json={**LAUNCH, "compliance_level": "Internal"}, headers=AUTH
        )
    assert response.json()["compliance_level"] == "Internal"
    await factory.services[0].started.wait()
    assert factory.services[0].calls[0]["compliance_level"] == "Internal"


async def test_approval_mandatory_tools_are_refused_unless_pausing_is_allowed(factory, monkeypatch):
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.requires_approval",
        lambda name, cfg: (True, True, True),
    )
    async with _client(factory) as client:
        refused = await client.post("/api/agent-runs", json=LAUNCH, headers=AUTH)
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "approval_required"
        assert "math_add" in refused.json()["detail"]["message"]
        assert get_run_registry().all_records() == []

        paused = await client.post(
            "/api/agent-runs", json={**LAUNCH, "allow_approval_pause": True}, headers=AUTH
        )
    assert paused.status_code == 202
    assert paused.json()["tools_needing_approval"] == ["math_add"]
    # Never auto-approved: the run will prompt the user.
    assert factory.services[-1].agent_mode.agent_loop_factory.skip_approval is False


async def test_the_per_user_concurrency_cap_applies(factory):
    get_run_registry().set_max_concurrent_runs_per_user(1)
    async with _client(factory) as client:
        first = await client.post("/api/agent-runs", json=LAUNCH, headers=AUTH)
        second = await client.post("/api/agent-runs", json=LAUNCH, headers=AUTH)
    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "concurrency_limit"


async def test_concurrent_launches_cannot_overrun_the_cap(factory):
    get_run_registry().set_max_concurrent_runs_per_user(2)
    async with _client(factory) as client:
        responses = await asyncio.gather(
            *[client.post("/api/agent-runs", json=LAUNCH, headers=AUTH) for _ in range(5)]
        )
    codes = sorted(r.status_code for r in responses)
    assert codes == [202, 202, 429, 429, 429]
    assert len(get_run_registry().active_for_user(USER)) == 2


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_an_idempotent_retry_returns_the_original_run(factory):
    body = {**LAUNCH, "idempotency_key": "build-42"}
    async with _client(factory) as client:
        first = await client.post("/api/agent-runs", json=body, headers=AUTH)
        retry = await client.post("/api/agent-runs", json=body, headers=AUTH)
        conflict = await client.post(
            "/api/agent-runs", json={**body, "message": "something else"}, headers=AUTH
        )
    assert first.status_code == 202
    assert retry.status_code == 200
    assert retry.json()["run_id"] == first.json()["run_id"]
    assert retry.json()["idempotent_replay"] is True
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert len(get_run_registry().all_records()) == 1


async def test_concurrent_requests_with_one_key_launch_once(factory):
    body = {**LAUNCH, "idempotency_key": "build-43"}
    async with _client(factory) as client:
        responses = await asyncio.gather(
            *[client.post("/api/agent-runs", json=body, headers=AUTH) for _ in range(4)]
        )
    assert len(get_run_registry().all_records()) == 1
    assert [r.status_code for r in responses].count(202) == 1
    for r in responses:
        assert r.status_code in (200, 202, 409)


async def test_a_refused_launch_does_not_burn_its_idempotency_key(factory):
    body = {**LAUNCH, "idempotency_key": "build-44"}
    async with _client(factory) as client:
        factory.conversation_repository = None
        refused = await client.post("/api/agent-runs", json=body, headers=AUTH)
        factory.conversation_repository = object()
        retried = await client.post("/api/agent-runs", json=body, headers=AUTH)
    assert refused.status_code == 503
    assert retried.status_code == 202


# ---------------------------------------------------------------------------
# Observation and scoping
# ---------------------------------------------------------------------------


async def test_runs_the_api_did_not_start_are_invisible_to_it(factory):
    ui_run = get_run_registry().start(conversation_id="ui-conv", user_email=USER)
    async with _client(factory) as client:
        for method, path in [
            ("GET", f"/api/agent-runs/{ui_run.run_id}"),
            ("POST", f"/api/agent-runs/{ui_run.run_id}/cancel"),
            ("POST", f"/api/agent-runs/{ui_run.run_id}/follow-up"),
        ]:
            response = await client.request(method, path, json={"message": "hi"}, headers=AUTH)
            assert response.status_code == 404, path
            assert response.json()["detail"]["code"] == "run_not_found"
        listing = (await client.get("/api/agent-runs", headers=AUTH)).json()
    assert listing["runs"] == []
    assert get_run_registry().get(ui_run.run_id).status is not RunStatus.CANCELLED


async def test_listing_filters_by_user(factory):
    factory.workspace_repository.owner = USER
    async with _client(factory) as client:
        launched = (await client.post("/api/agent-runs", json=LAUNCH, headers=AUTH)).json()
        mine = (await client.get(f"/api/agent-runs?user={USER}", headers=AUTH)).json()
        theirs = (await client.get("/api/agent-runs?user=other@example.com", headers=AUTH)).json()
    assert [r["run_id"] for r in mine["runs"]] == [launched["run_id"]]
    assert theirs["runs"] == []


async def test_options_lists_the_users_workspaces_and_models(factory):
    async with _client(factory) as client:
        response = await client.get(f"/api/agent-runs/options?user={USER}", headers=AUTH)
        other = await client.get("/api/agent-runs/options?user=other@example.com", headers=AUTH)
    assert response.json() == {
        "workspaces": [{"id": "ws-1", "name": "Research"}],
        "models": [{"id": None, "name": "gpt-4o"}],
    }
    assert other.json()["workspaces"] == []


async def test_cancel_stops_the_run(factory):
    async with _client(factory) as client:
        handle = (await client.post("/api/agent-runs", json=LAUNCH, headers=AUTH)).json()
        await factory.services[0].started.wait()
        response = await client.post(f"/api/agent-runs/{handle['run_id']}/cancel", headers=AUTH)
        again = await client.post(f"/api/agent-runs/{handle['run_id']}/cancel", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancelled"] is True
    assert again.json()["cancelled"] is False


# ---------------------------------------------------------------------------
# Follow-up
# ---------------------------------------------------------------------------


async def _completed_launch(client, factory, body=LAUNCH):
    handle = (await client.post("/api/agent-runs", json=body, headers=AUTH)).json()
    await factory.services[-1].started.wait()
    factory.services[-1].release.set()
    await _settle()
    return handle


async def test_follow_up_continues_the_same_conversation_on_its_launch_spec(factory):
    async with _client(factory) as client:
        first = await _completed_launch(
            client, factory, {**LAUNCH, "compliance_level": "Internal"}
        )
        response = await client.post(
            f"/api/agent-runs/{first['run_id']}/follow-up",
            json={"message": "Now list the flaky tests"},
            headers=AUTH,
        )
    assert response.status_code == 202
    handle = response.json()
    assert handle["conversation_id"] == first["conversation_id"]
    assert handle["previous_run_id"] == first["run_id"]
    assert handle["run_id"] != first["run_id"]
    await factory.services[-1].started.wait()
    call = factory.services[-1].calls[0]
    assert call["conversation_id"] == first["conversation_id"]
    assert call["content"] == "Now list the flaky tests"
    assert call["model"] == "gpt-4o"
    # The classification is the conversation's, not the caller's to change.
    assert call["compliance_level"] == "Internal"
    assert call["user_email"] == USER


async def test_follow_up_is_refused_while_the_conversation_is_busy(factory):
    async with _client(factory) as client:
        handle = (await client.post("/api/agent-runs", json=LAUNCH, headers=AUTH)).json()
        response = await client.post(
            f"/api/agent-runs/{handle['run_id']}/follow-up", json={"message": "more"}, headers=AUTH
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "conversation_busy"


async def test_concurrent_follow_ups_admit_only_one(factory):
    async with _client(factory) as client:
        first = await _completed_launch(client, factory)
        responses = await asyncio.gather(
            *[
                client.post(
                    f"/api/agent-runs/{first['run_id']}/follow-up",
                    json={"message": f"turn {i}"},
                    headers=AUTH,
                )
                for i in range(3)
            ]
        )
    assert sorted(r.status_code for r in responses) == [202, 409, 409]


async def test_follow_up_on_a_run_that_never_completed_is_refused(factory):
    async with _client(factory) as client:
        handle = (await client.post("/api/agent-runs", json=LAUNCH, headers=AUTH)).json()
        await client.post(f"/api/agent-runs/{handle['run_id']}/cancel", headers=AUTH)
        response = await client.post(
            f"/api/agent-runs/{handle['run_id']}/follow-up", json={"message": "more"}, headers=AUTH
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "not_continuable"


async def test_follow_up_after_the_workspace_is_revoked_is_refused(factory):
    async with _client(factory) as client:
        first = await _completed_launch(client, factory)
        factory.workspace_repository.rows = []
        response = await client.post(
            f"/api/agent-runs/{first['run_id']}/follow-up", json={"message": "more"}, headers=AUTH
        )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "workspace_not_found"


async def test_follow_up_is_refused_while_waiting_for_the_user(factory):
    async with _client(factory) as client:
        first = await _completed_launch(client, factory)
        followed = (
            await client.post(
                f"/api/agent-runs/{first['run_id']}/follow-up", json={"message": "a"}, headers=AUTH
            )
        ).json()
        get_run_registry().set_status(
            followed["run_id"], RunStatus.WAITING_FOR_INPUT, waiting_on="tool_approval"
        )
        status = (await client.get(f"/api/agent-runs/{followed['run_id']}", headers=AUTH)).json()
        response = await client.post(
            f"/api/agent-runs/{first['run_id']}/follow-up", json={"message": "b"}, headers=AUTH
        )
    assert status["status"] == "waiting_for_input"
    assert status["waiting_on"] == "tool_approval"
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "waiting_for_input"


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------


def test_the_openapi_document_covers_every_endpoint(factory):
    schema = _app(factory).openapi()
    paths = schema["paths"]
    assert set(paths["/api/agent-runs"]) == {"get", "post"}
    assert "202" in paths["/api/agent-runs"]["post"]["responses"]
    assert "200" in paths["/api/agent-runs"]["post"]["responses"]
    assert "get" in paths["/api/agent-runs/options"]
    assert "get" in paths["/api/agent-runs/{run_id}"]
    assert "202" in paths["/api/agent-runs/{run_id}/follow-up"]["post"]["responses"]
    assert "post" in paths["/api/agent-runs/{run_id}/cancel"]
    for status in ("401", "409", "429"):
        assert status in paths["/api/agent-runs"]["post"]["responses"]
    assert {"LaunchRequest", "FollowUpRequest", "RunHandle", "RunStatusModel", "ErrorResponse"} <= set(
        schema["components"]["schemas"]
    )
