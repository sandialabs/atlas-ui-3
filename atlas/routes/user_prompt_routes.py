"""REST API routes for the per-user custom prompt library (issue #153).

Users can create, list, edit, and delete reusable system prompts. The active
prompt's text is sent with chat messages and replaces the default system
prompt for that turn (see ChatService / MessageBuilder).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from atlas.core.log_sanitizer import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user-prompts", tags=["user-prompts"])

# Generous cap so the stored content stays a "system prompt" and not an upload.
MAX_CONTENT_LEN = 50_000
MAX_TITLE_LEN = 200


class CreatePromptRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=MAX_TITLE_LEN)
    content: str = Field(..., min_length=1, max_length=MAX_CONTENT_LEN)


class UpdatePromptRequest(BaseModel):
    title: str | None = Field(default=None, max_length=MAX_TITLE_LEN)
    content: str | None = Field(default=None, max_length=MAX_CONTENT_LEN)


def _get_repo():
    """Get the user prompt repository from the app factory."""
    from atlas.infrastructure.app_factory import app_factory
    return getattr(app_factory, "user_prompt_repository", None)


def _custom_prompts_enabled() -> bool:
    """Return whether the custom prompt library feature is enabled."""
    from atlas.infrastructure.app_factory import app_factory
    settings = app_factory.get_config_manager().app_settings
    return bool(settings.feature_custom_prompts_enabled and settings.feature_chat_history_enabled)


def _require_enabled() -> None:
    if not _custom_prompts_enabled():
        raise HTTPException(status_code=404, detail="Feature not enabled")


@router.get("")
async def list_prompts(current_user: str = Depends(get_current_user)):
    """List all custom prompts for the authenticated user."""
    _require_enabled()
    repo = _get_repo()
    if repo is None:
        return {"prompts": [], "error": "Chat history is not enabled"}
    return {"prompts": repo.list_prompts(user_email=current_user)}


@router.post("")
async def create_prompt(
    body: CreatePromptRequest,
    current_user: str = Depends(get_current_user),
):
    """Create a new custom prompt."""
    _require_enabled()
    repo = _get_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Chat history is not enabled")
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")
    prompt = repo.create_prompt(
        user_email=current_user, title=body.title, content=body.content
    )
    return {"prompt": prompt}


@router.put("/{prompt_id}")
async def update_prompt(
    prompt_id: str,
    body: UpdatePromptRequest,
    current_user: str = Depends(get_current_user),
):
    """Update an existing custom prompt owned by the user."""
    _require_enabled()
    repo = _get_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Chat history is not enabled")
    if body.title is not None and not body.title.strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    # Reject blank content on update too — create requires non-empty content, and
    # a whitespace-only prompt would silently fall back to the default when used.
    if body.content is not None and not body.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")
    prompt = repo.update_prompt(
        prompt_id=prompt_id,
        user_email=current_user,
        title=body.title,
        content=body.content,
    )
    if prompt is None:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"prompt": prompt}


@router.delete("/{prompt_id}")
async def delete_prompt(
    prompt_id: str,
    current_user: str = Depends(get_current_user),
):
    """Delete a custom prompt owned by the user."""
    _require_enabled()
    repo = _get_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Chat history is not enabled")
    deleted = repo.delete_prompt(prompt_id=prompt_id, user_email=current_user)
    if not deleted:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"success": True}
