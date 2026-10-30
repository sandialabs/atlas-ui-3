"""REST API for agent runs launched by a trusted external service (#1105).

One master key (``AGENT_RUNS_API_KEY``) authenticates one trusted service,
which may act on behalf of any user. The endpoints mirror the built-in
sub-conversation tools a conversation already has:

* ``GET  /api/agent-runs/options`` -- ``atlas_discover_launch_options``
* ``POST /api/agent-runs`` -- ``atlas_launch``
* ``GET  /api/agent-runs`` -- ``atlas_get_runs``
* ``GET  /api/agent-runs/{run_id}`` -- ``atlas_result``
* ``POST /api/agent-runs/{run_id}/follow-up`` -- ``atlas_follow_up``
* ``POST /api/agent-runs/{run_id}/cancel`` -- stop, as in the UI

These paths are exempt from the browser auth middleware (identity header,
proxy secret, OIDC session): the bearer key checked here is their only
credential, so a signed-in user's browser cannot reach them.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from atlas.application.chat.runs.external_runs import (
    agent_runs_api_enabled,
    cancel_api_run,
    follow_up_api_run,
    get_api_run,
    launch_api_run,
    list_api_options,
    list_api_runs,
)
from atlas.application.chat.runs.launcher import LaunchRefused
from atlas.core.agent_runs_paths import AGENT_RUNS_PREFIX

logger = logging.getLogger(__name__)

MIN_API_KEY_CHARS = 32

# Refusal code -> HTTP status. Anything unlisted is a 400.
_STATUS_FOR_CODE = {
    "invalid_request": 400,
    "user_not_allowed": 403,
    "workspace_not_found": 404,
    "run_not_found": 404,
    "model_not_available": 422,
    "no_authorized_tools": 422,
    "classification_refused": 422,
    "approval_required": 409,
    "conversation_busy": 409,
    "waiting_for_input": 409,
    "not_continuable": 409,
    "idempotency_conflict": 409,
    "idempotency_in_progress": 409,
    "concurrency_limit": 429,
    "persistence_unavailable": 503,
    "unavailable": 503,
}


def get_factory() -> Any:
    """The application factory; a dependency so tests can substitute one."""
    from atlas.infrastructure.app_factory import app_factory

    return app_factory


def _error(status_code: int, code: str, message: str, headers=None) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
        headers=headers,
    )


def _refused(exc: LaunchRefused) -> HTTPException:
    code = getattr(exc, "code", "launch_refused")
    return _error(_STATUS_FOR_CODE.get(code, 400), code, str(exc))


async def require_service_key(request: Request, factory: Any = Depends(get_factory)) -> None:
    """Authenticate the trusted service by its bearer key.

    Fails closed: with the feature off the API does not exist (404); with it
    on but no strong key configured, nothing is served (503) rather than
    anything being served unauthenticated.
    """
    settings = getattr(factory.get_config_manager(), "app_settings", None)
    if not agent_runs_api_enabled(settings):
        raise _error(404, "not_found", "Not Found")
    expected = getattr(settings, "agent_runs_api_key", None) or ""
    if len(expected) < MIN_API_KEY_CHARS:
        logger.error(
            "Agent-runs API is enabled but AGENT_RUNS_API_KEY is unset or shorter "
            "than the 32-character minimum; refusing request"
        )
        raise _error(503, "not_configured", "The agent-runs API is not configured.")
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if (
        scheme.lower() != "bearer"
        or not token
        or not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
    ):
        logger.warning("Agent-runs API request with a missing or invalid key")
        raise _error(
            401,
            "unauthorized",
            "A valid bearer key is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


router = APIRouter(
    prefix=AGENT_RUNS_PREFIX,
    tags=["agent-runs"],
    dependencies=[Depends(require_service_key)],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_USER_FIELD = Field(
    ...,
    min_length=1,
    max_length=320,
    pattern=r"^\S+$",
    description="The user the run acts as. Their ACLs decide everything the run may use.",
    examples=["user@example.com"],
)
_IDEMPOTENCY_FIELD = Field(
    None,
    min_length=1,
    max_length=200,
    description=(
        "Caller-generated key. A retry with the same key and body returns the "
        "original run (HTTP 200) instead of launching again; the same key with "
        "a different body is a 409. Keys are remembered for 24 hours."
    ),
)
_PAUSE_FIELD = Field(
    False,
    description=(
        "When the run's tools include ones an administrator requires approval "
        "for, pause the run until the user approves them in ATLAS instead of "
        "refusing the launch (the default). Approval-mandatory tools are never "
        "auto-approved."
    ),
)


class LaunchRequest(BaseModel):
    user: str = _USER_FIELD
    workspace: str = Field(
        ..., min_length=1, max_length=200,
        description="The user's workspace, by id or name: the run's tools and data sources.",
    )
    model: str = Field(..., min_length=1, max_length=200, description="An LLM the user may use.")
    message: str = Field(..., min_length=1, description="The task: the conversation's first user message.")
    compliance_level: Optional[str] = Field(
        None,
        max_length=200,
        description=(
            "The conversation's classification. Checked against the model, "
            "tools and data sources at admission, and fixed for the "
            "conversation's lifetime."
        ),
    )
    allow_approval_pause: bool = _PAUSE_FIELD
    idempotency_key: Optional[str] = _IDEMPOTENCY_FIELD


class FollowUpRequest(BaseModel):
    message: str = Field(..., min_length=1, description="The conversation's next user message.")
    allow_approval_pause: bool = _PAUSE_FIELD
    idempotency_key: Optional[str] = _IDEMPOTENCY_FIELD


class RunHandle(BaseModel):
    run_id: str
    conversation_id: str
    user: str
    status: str = Field(
        ...,
        description=(
            "The run's status (see RunStatusModel), or 'expired' on an idempotent "
            "replay whose run is no longer tracked."
        ),
    )
    status_url: str
    workspace: Optional[str] = None
    model: Optional[str] = None
    tools: List[str] = []
    data_sources: List[str] = []
    data_sources_unavailable: List[str] = []
    compliance_level: Optional[str] = None
    tools_needing_approval: List[str] = Field(
        [], description="Non-empty only with allow_approval_pause: the run will pause on these."
    )
    previous_run_id: Optional[str] = None
    idempotent_replay: bool = False


class RunStatusModel(BaseModel):
    run_id: str
    conversation_id: str
    user: str
    status: str = Field(
        ...,
        description="queued, running, waiting_for_input, completed, failed or cancelled.",
    )
    waiting_on: Optional[str] = None
    created_at: float
    updated_at: float
    ended_at: Optional[float] = None
    error: Optional[str] = None
    title: Optional[str] = None
    detached: bool = False
    depth: int = 0
    parent_run_id: Optional[str] = None
    parent_conversation_id: Optional[str] = None
    workspace: Optional[str] = None
    model: Optional[str] = None
    tools: List[str] = []
    data_sources: List[str] = []
    compliance_level: Optional[str] = None
    previous_run_id: Optional[str] = None
    result: Optional[str] = Field(None, description="The run's final reply, once completed.")
    result_status: str = Field(
        ..., description="pending, available, available_truncated, empty or unavailable."
    )
    result_error: Optional[str] = None
    cancelled: Optional[bool] = None


class RunList(BaseModel):
    runs: List[RunStatusModel]


class LaunchOption(BaseModel):
    id: Optional[str] = None
    name: str


class LaunchOptions(BaseModel):
    workspaces: List[LaunchOption]
    models: List[LaunchOption]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


_ERRORS: Dict[int | str, Dict[str, Any]] = {
    status: {"model": ErrorResponse}
    for status in (400, 401, 403, 404, 409, 422, 429, 503)
}


def _status_url(run_id: str) -> str:
    return f"{AGENT_RUNS_PREFIX}/{run_id}"


def _respond_handle(handle: Dict[str, Any], response: Response) -> Dict[str, Any]:
    handle = dict(handle)
    handle["status_url"] = _status_url(handle["run_id"])
    response.status_code = 200 if handle.get("idempotent_replay") else 202
    response.headers["Location"] = handle["status_url"]
    return handle


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/options", response_model=LaunchOptions, responses=_ERRORS)
async def get_launch_options(
    user: str = Query(..., min_length=1, max_length=320, pattern=r"^\S+$"),
    factory: Any = Depends(get_factory),
):
    """The workspaces and models ``user`` may launch with right now."""
    try:
        return await list_api_options(factory, user)
    except LaunchRefused as e:
        raise _refused(e)


@router.post(
    "",
    response_model=RunHandle,
    status_code=202,
    responses={**_ERRORS, 200: {"model": RunHandle, "description": "Idempotent replay"}},
)
async def launch_run(
    body: LaunchRequest, response: Response, factory: Any = Depends(get_factory)
):
    """Start an agent run as ``user`` in a new top-level conversation.

    Returns as soon as the run is admitted; the run continues after this
    request completes. Poll ``status_url`` for its outcome. The conversation
    appears in the user's history like any other.
    """
    try:
        handle = await launch_api_run(
            factory,
            user_email=body.user,
            workspace=body.workspace,
            model=body.model,
            message=body.message,
            compliance_level=body.compliance_level,
            allow_approval_pause=body.allow_approval_pause,
            idempotency_key=body.idempotency_key,
        )
    except LaunchRefused as e:
        raise _refused(e)
    return _respond_handle(handle, response)


@router.get("", response_model=RunList, responses=_ERRORS)
async def list_runs(
    user: Optional[str] = Query(None, min_length=1, max_length=320, pattern=r"^\S+$"),
):
    """API-started runs still retained, newest first, optionally for one user."""
    return {"runs": list_api_runs(user)}


@router.get("/{run_id}", response_model=RunStatusModel, responses=_ERRORS)
async def get_run(run_id: str):
    """Status, timestamps, error and final reply of an API-started run."""
    try:
        return get_api_run(run_id)
    except LaunchRefused as e:
        raise _refused(e)


@router.post(
    "/{run_id}/follow-up",
    response_model=RunHandle,
    status_code=202,
    responses={**_ERRORS, 200: {"model": RunHandle, "description": "Idempotent replay"}},
)
async def follow_up_run(
    run_id: str,
    body: FollowUpRequest,
    response: Response,
    factory: Any = Depends(get_factory),
):
    """Send the next message to the conversation of an API-started run.

    Runs as that conversation's owner, under the workspace, model and
    compliance level it was launched with, re-checked against the owner's
    access now. Refused while the conversation has a run in progress.
    """
    try:
        handle = await follow_up_api_run(
            factory,
            run_id,
            message=body.message,
            allow_approval_pause=body.allow_approval_pause,
            idempotency_key=body.idempotency_key,
        )
    except LaunchRefused as e:
        raise _refused(e)
    return _respond_handle(handle, response)


@router.post("/{run_id}/cancel", response_model=RunStatusModel, responses=_ERRORS)
async def cancel_run(run_id: str):
    """Stop an API-started run and any sub-conversations it launched."""
    try:
        return cancel_api_run(run_id)
    except LaunchRefused as e:
        raise _refused(e)
