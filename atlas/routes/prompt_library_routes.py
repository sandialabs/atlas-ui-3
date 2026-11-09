"""REST API for the team-shared prompt library (issue #1169).

Separate from ``/api/user-prompts`` (issue #153), which keeps serving private
custom prompts unchanged. Every handler takes the caller's identity from the
authentication middleware (``get_current_user``) -- never from the request
body -- and delegates the access decision to ``PromptLibraryService``.

Errors never reveal whether an entry the caller cannot see exists: missing and
unauthorized both return 404 "Prompt not found". 403 is only returned to a
caller who can already see the entry but lacks the level an action needs.

Gated on the same flag as custom prompts (``custom_prompts_effective``: the
custom-prompt feature plus chat history, which provides the database).
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from atlas.core.log_sanitizer import get_current_user
from atlas.modules.prompts.prompt_library_service import (
    MAX_CHANGE_DESCRIPTION_LEN,
    MAX_CONTENT_LEN,
    MAX_DESCRIPTION_LEN,
    MAX_GRANTS,
    MAX_TITLE_LEN,
    PromptForbidden,
    PromptLibraryError,
    PromptLibraryService,
    PromptNotFound,
    PromptValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prompts", tags=["prompt-library"])


class CreateLibraryPromptRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=MAX_TITLE_LEN)
    content: str = Field(..., min_length=1, max_length=MAX_CONTENT_LEN)
    description: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION_LEN)


class UpdateLibraryPromptRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=MAX_TITLE_LEN)
    content: Optional[str] = Field(default=None, max_length=MAX_CONTENT_LEN)
    description: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION_LEN)


class PublishRequest(BaseModel):
    change_description: Optional[str] = Field(default=None, max_length=MAX_CHANGE_DESCRIPTION_LEN)


class UserGrant(BaseModel):
    email: str = Field(..., min_length=1, max_length=255)
    permission: str = Field(default="use")


class SharingRequest(BaseModel):
    groups: List[str] = Field(default_factory=list, max_length=MAX_GRANTS)
    users: List[UserGrant] = Field(default_factory=list, max_length=MAX_GRANTS)


class ForkRequest(BaseModel):
    version_id: Optional[str] = Field(default=None, max_length=36)
    title: Optional[str] = Field(default=None, max_length=MAX_TITLE_LEN)


def _library_enabled() -> bool:
    from atlas.infrastructure.app_factory import app_factory

    return app_factory.get_config_manager().app_settings.custom_prompts_effective


def _get_service() -> Optional[PromptLibraryService]:
    from atlas.infrastructure.app_factory import app_factory

    return getattr(app_factory, "prompt_library_service", None)


def _service() -> PromptLibraryService:
    if not _library_enabled():
        raise HTTPException(status_code=404, detail="Feature not enabled")
    service = _get_service()
    if service is None:
        from atlas.infrastructure.app_factory import app_factory

        raise HTTPException(status_code=503, detail=app_factory.chat_history_unavailable_reason())
    return service


def _http_error(e: PromptLibraryError) -> HTTPException:
    if isinstance(e, PromptNotFound):
        return HTTPException(status_code=404, detail=e.message)
    if isinstance(e, PromptForbidden):
        return HTTPException(status_code=403, detail=e.message)
    if isinstance(e, PromptValidationError):
        return HTTPException(status_code=400, detail=e.message)
    return HTTPException(status_code=400, detail=e.message)


@router.get("")
async def list_library_prompts(
    scope: str = Query(default="all"),
    q: Optional[str] = Query(default=None, max_length=200),
    current_user: str = Depends(get_current_user),
):
    """List entries the caller may see: scope all|mine|shared|team, optional search."""
    service = _service()
    try:
        return {"prompts": await service.list_prompts(current_user, scope=scope, query=q)}
    except PromptLibraryError as e:
        raise _http_error(e)


@router.get("/groups")
async def list_shareable_groups(current_user: str = Depends(get_current_user)):
    """Configured groups the caller belongs to and may therefore share with."""
    service = _service()
    return {"groups": await service.shareable_groups(current_user)}


@router.post("")
async def create_library_prompt(
    body: CreateLibraryPromptRequest,
    current_user: str = Depends(get_current_user),
):
    """Create a private entry owned by the caller."""
    service = _service()
    try:
        prompt = await service.create_prompt(
            current_user, body.title, body.content, description=body.description
        )
    except PromptLibraryError as e:
        raise _http_error(e)
    return {"prompt": prompt}


@router.get("/{prompt_id}")
async def get_library_prompt(prompt_id: str, current_user: str = Depends(get_current_user)):
    service = _service()
    try:
        return {"prompt": await service.get_prompt(current_user, prompt_id)}
    except PromptLibraryError as e:
        raise _http_error(e)


@router.put("/{prompt_id}")
async def update_library_prompt(
    prompt_id: str,
    body: UpdateLibraryPromptRequest,
    current_user: str = Depends(get_current_user),
):
    """Update the draft (title, description, content). Published versions never change."""
    service = _service()
    try:
        prompt = await service.update_prompt(
            current_user, prompt_id,
            title=body.title, description=body.description, content=body.content,
        )
    except PromptLibraryError as e:
        raise _http_error(e)
    return {"prompt": prompt}


@router.delete("/{prompt_id}")
async def archive_library_prompt(prompt_id: str, current_user: str = Depends(get_current_user)):
    """Archive (owner only): hidden and unusable for new turns; history keeps its versions."""
    service = _service()
    try:
        await service.archive(current_user, prompt_id)
    except PromptLibraryError as e:
        raise _http_error(e)
    return {"success": True}


@router.post("/{prompt_id}/publish")
async def publish_library_prompt(
    prompt_id: str,
    body: PublishRequest,
    current_user: str = Depends(get_current_user),
):
    """Snapshot the current draft as the next immutable version."""
    service = _service()
    try:
        version = await service.publish(current_user, prompt_id, body.change_description)
    except PromptLibraryError as e:
        raise _http_error(e)
    return {"version": version}


@router.get("/{prompt_id}/versions")
async def list_library_prompt_versions(prompt_id: str, current_user: str = Depends(get_current_user)):
    service = _service()
    try:
        return {"versions": await service.list_versions(current_user, prompt_id)}
    except PromptLibraryError as e:
        raise _http_error(e)


@router.get("/{prompt_id}/versions/{version_id}")
async def get_library_prompt_version(
    prompt_id: str, version_id: str, current_user: str = Depends(get_current_user)
):
    service = _service()
    try:
        return {"version": await service.get_version(current_user, prompt_id, version_id)}
    except PromptLibraryError as e:
        raise _http_error(e)


@router.get("/{prompt_id}/sharing")
async def get_library_prompt_sharing(prompt_id: str, current_user: str = Depends(get_current_user)):
    service = _service()
    try:
        return {"sharing": await service.get_sharing(current_user, prompt_id)}
    except PromptLibraryError as e:
        raise _http_error(e)


@router.put("/{prompt_id}/sharing")
async def set_library_prompt_sharing(
    prompt_id: str,
    body: SharingRequest,
    current_user: str = Depends(get_current_user),
):
    """Replace the grant set (manage permission required)."""
    service = _service()
    try:
        sharing = await service.set_sharing(
            current_user, prompt_id, body.groups, [u.model_dump() for u in body.users]
        )
    except PromptLibraryError as e:
        raise _http_error(e)
    return {"sharing": sharing}


@router.post("/{prompt_id}/fork")
async def fork_library_prompt(
    prompt_id: str,
    body: ForkRequest,
    current_user: str = Depends(get_current_user),
):
    """Copy a published version into a new private entry owned by the caller."""
    service = _service()
    try:
        prompt = await service.fork(current_user, prompt_id, version_id=body.version_id, title=body.title)
    except PromptLibraryError as e:
        raise _http_error(e)
    return {"prompt": prompt}


@router.get("/{prompt_id}/export")
async def export_library_prompt(prompt_id: str, current_user: str = Depends(get_current_user)):
    service = _service()
    try:
        return await service.export_prompt(current_user, prompt_id)
    except PromptLibraryError as e:
        raise _http_error(e)


@router.get("/{prompt_id}/audit")
async def list_library_prompt_audit(prompt_id: str, current_user: str = Depends(get_current_user)):
    service = _service()
    try:
        return {"events": await service.list_audit_events(current_user, prompt_id)}
    except PromptLibraryError as e:
        raise _http_error(e)


@router.get("/{prompt_id}/resolve")
async def resolve_library_prompt(
    prompt_id: str,
    version_id: Optional[str] = Query(default=None, max_length=36),
    current_user: str = Depends(get_current_user),
):
    """Which exact version a chat turn would run for this selection, re-checked now.

    Lets the client confirm a saved selection (a workspace, a reopened
    conversation) before sending. The chat path performs the same check
    itself; this endpoint is advisory, never a substitute.
    """
    service = _service()
    try:
        version = await service.resolve_for_execution(current_user, prompt_id, version_id)
    except PromptLibraryError as e:
        # Same not-found answer as every other unauthorized lookup.
        raise HTTPException(status_code=404, detail=e.message)
    return {
        "prompt_id": version["prompt_id"],
        "version_id": version["id"],
        "version_number": version["version_number"],
        "title": version["title"],
    }
