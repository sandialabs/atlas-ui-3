"""REST API routes for conversation history management."""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from atlas.core.log_sanitizer import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class DeleteMultipleRequest(BaseModel):
    ids: List[str]


class AddTagRequest(BaseModel):
    name: str


class UpdateTitleRequest(BaseModel):
    title: str


def _get_repo():
    """Get the conversation repository from the app factory."""
    from atlas.infrastructure.app_factory import app_factory
    repo = getattr(app_factory, "conversation_repository", None)
    if repo is None:
        return None
    return repo


@router.get("")
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tag: Optional[str] = Query(default=None),
    current_user: str = Depends(get_current_user),
):
    """List conversations for the authenticated user."""
    repo = _get_repo()
    if repo is None:
        return {"conversations": [], "error": "Chat history is not enabled"}

    conversations = repo.list_conversations(
        user_email=current_user,
        limit=limit,
        offset=offset,
        tag_name=tag,
    )
    return {"conversations": conversations}


@router.get("/search")
async def search_conversations(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: str = Depends(get_current_user),
):
    """Search conversations by content or title."""
    repo = _get_repo()
    if repo is None:
        return {"conversations": [], "error": "Chat history is not enabled"}

    conversations = repo.search_conversations(
        user_email=current_user,
        query=q,
        limit=limit,
    )
    return {"conversations": conversations}


@router.get("/export")
async def export_all_conversations(
    current_user: str = Depends(get_current_user),
):
    """Export all conversations with full messages for the authenticated user.

    Returns a JSON document containing all conversations and their messages,
    suitable for download/backup.
    """
    repo = _get_repo()
    if repo is None:
        return {"error": "Chat history is not enabled"}

    conversations = repo.export_all_conversations(current_user)
    export_data = {
        "export_date": datetime.now(timezone.utc).isoformat(),
        "user_email": current_user,
        "conversation_count": len(conversations),
        "conversations": conversations,
    }
    return JSONResponse(
        content=export_data,
        headers={
            "Content-Disposition": "attachment; filename=conversations-export.json",
        },
    )


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    current_user: str = Depends(get_current_user),
):
    """Get a full conversation with all messages."""
    repo = _get_repo()
    if repo is None:
        return {"error": "Chat history is not enabled"}

    conversation = repo.get_conversation(conversation_id, current_user)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    current_user: str = Depends(get_current_user),
):
    """Delete a single conversation."""
    repo = _get_repo()
    if repo is None:
        return {"error": "Chat history is not enabled"}

    deleted = repo.delete_conversation(conversation_id, current_user)
    return {"deleted": deleted}


@router.post("/delete")
async def delete_multiple_conversations(
    body: DeleteMultipleRequest,
    current_user: str = Depends(get_current_user),
):
    """Delete multiple conversations."""
    repo = _get_repo()
    if repo is None:
        return {"error": "Chat history is not enabled"}

    count = repo.delete_conversations(body.ids, current_user)
    return {"deleted_count": count}


@router.delete("")
async def delete_all_conversations(
    current_user: str = Depends(get_current_user),
):
    """Delete all conversations for the authenticated user."""
    repo = _get_repo()
    if repo is None:
        return {"error": "Chat history is not enabled"}

    count = repo.delete_all_conversations(current_user)
    return {"deleted_count": count}


@router.post("/{conversation_id}/tags")
async def add_tag(
    conversation_id: str,
    body: AddTagRequest,
    current_user: str = Depends(get_current_user),
):
    """Add a tag to a conversation."""
    repo = _get_repo()
    if repo is None:
        return {"error": "Chat history is not enabled"}

    tag_id = repo.add_tag(conversation_id, body.name, current_user)
    if tag_id is None:
        return {"error": "Conversation not found"}
    return {"tag_id": tag_id, "name": body.name}


@router.delete("/{conversation_id}/tags/{tag_id}")
async def remove_tag(
    conversation_id: str,
    tag_id: str,
    current_user: str = Depends(get_current_user),
):
    """Remove a tag from a conversation."""
    repo = _get_repo()
    if repo is None:
        return {"error": "Chat history is not enabled"}

    removed = repo.remove_tag(conversation_id, tag_id, current_user)
    return {"removed": removed}


@router.patch("/{conversation_id}/title")
async def update_title(
    conversation_id: str,
    body: UpdateTitleRequest,
    current_user: str = Depends(get_current_user),
):
    """Update the title of a conversation."""
    repo = _get_repo()
    if repo is None:
        return {"error": "Chat history is not enabled"}

    updated = repo.update_title(conversation_id, body.title, current_user)
    return {"updated": updated}


@router.get("/tags/list")
async def list_tags(
    current_user: str = Depends(get_current_user),
):
    """List all tags for the authenticated user."""
    repo = _get_repo()
    if repo is None:
        return {"tags": [], "error": "Chat history is not enabled"}

    tags = repo.list_tags(current_user)
    return {"tags": tags}
