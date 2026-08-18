"""REST API routes for per-user workspaces.

A workspace is a named bundle of chat-context selections -- active prompt, RAG
data sources, and MCP tools -- so a user can switch between contexts like
"Work" / "Home" / "Project A" in one click. Applying a workspace is a
client-side operation: the frontend restores the saved selections, which then
flow through the normal chat payload. This module only owns persistence.

Mirrors ``user_prompt_routes``: same enable-gate shape, same ownership model
(every query is scoped to the authenticated user), same 404-when-disabled
behaviour so a disabled feature exposes no surface at all.
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from atlas.core.log_sanitizer import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])

MAX_NAME_LEN = 200
MAX_DESCRIPTION_LEN = 500


class WorkspaceConfig(BaseModel):
    """The selection snapshot a workspace restores.

    Extra keys are rejected rather than silently stored so the client and the
    persisted shape cannot drift apart unnoticed; the repository normalizes the
    values (dedupe, caps) on the way to the database.
    """

    model_config = {"extra": "forbid"}

    active_prompt_key: Optional[str] = None
    selected_tools: list[str] = Field(default_factory=list)
    selected_prompts: list[str] = Field(default_factory=list)
    selected_data_sources: list[str] = Field(default_factory=list)
    rag_enabled: bool = False


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=MAX_NAME_LEN)
    description: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION_LEN)
    config: WorkspaceConfig = Field(default_factory=WorkspaceConfig)


class UpdateWorkspaceRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=MAX_NAME_LEN)
    description: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION_LEN)
    config: Optional[WorkspaceConfig] = None


def _get_repo():
    """Get the workspace repository from the app factory."""
    from atlas.infrastructure.app_factory import app_factory
    return getattr(app_factory, "workspace_repository", None)


def _workspaces_enabled() -> bool:
    """Return whether the workspace feature is enabled and usable."""
    from atlas.infrastructure.app_factory import app_factory
    settings = app_factory.get_config_manager().app_settings
    return settings.workspaces_effective


def _require_enabled() -> None:
    if not _workspaces_enabled():
        raise HTTPException(status_code=404, detail="Feature not enabled")


def _require_repo():
    repo = _get_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Chat history is not enabled")
    return repo


def _config_payload(config: Optional[WorkspaceConfig]) -> Optional[Any]:
    return config.model_dump() if config is not None else None


@router.get("")
async def list_workspaces(current_user: str = Depends(get_current_user)):
    """List all workspaces for the authenticated user."""
    _require_enabled()
    repo = _get_repo()
    if repo is None:
        return {"workspaces": [], "error": "Chat history is not enabled"}
    return {"workspaces": repo.list_workspaces(user_email=current_user)}


@router.post("")
async def create_workspace(
    body: CreateWorkspaceRequest,
    current_user: str = Depends(get_current_user),
):
    """Create a new workspace."""
    _require_enabled()
    repo = _require_repo()
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    workspace = repo.create_workspace(
        user_email=current_user,
        name=body.name,
        config=_config_payload(body.config),
        description=body.description,
    )
    return {"workspace": workspace}


@router.put("/{workspace_id}")
async def update_workspace(
    workspace_id: str,
    body: UpdateWorkspaceRequest,
    current_user: str = Depends(get_current_user),
):
    """Update a workspace owned by the user."""
    _require_enabled()
    repo = _require_repo()
    if body.name is not None and not body.name.strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    workspace = repo.update_workspace(
        workspace_id=workspace_id,
        user_email=current_user,
        name=body.name,
        config=_config_payload(body.config),
        description=body.description,
    )
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return {"workspace": workspace}


@router.delete("/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    current_user: str = Depends(get_current_user),
):
    """Delete a workspace owned by the user."""
    _require_enabled()
    repo = _require_repo()
    deleted = repo.delete_workspace(workspace_id=workspace_id, user_email=current_user)
    if not deleted:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return {"success": True}
