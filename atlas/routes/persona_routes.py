"""REST API for admin-preconfigured personas / system prompts (issue #880).

Personas are markdown files on disk (see
:mod:`atlas.modules.prompts.persona_library`). They are read-only from the
client's perspective: the UI lists the ones the caller's groups allow and sends
the selected prompt text as ``custom_system_prompt`` on the next chat turn,
exactly like a user-authored prompt.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from atlas.core.auth import is_user_in_group
from atlas.core.log_sanitizer import get_current_user
from atlas.modules.prompts.persona_library import get_persona_library

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/personas", tags=["personas"])


@router.get("")
async def list_personas(current_user: str = Depends(get_current_user)):
    """List the preconfigured personas visible to the authenticated user."""
    library = get_persona_library()
    personas = await library.personas_for_user(current_user, is_user_in_group)
    return {"personas": [p.to_dict() for p in personas]}


@router.get("/{persona_id}")
async def get_persona(persona_id: str, current_user: str = Depends(get_current_user)):
    """Return one persona, if the user is allowed to see it."""
    library = get_persona_library()
    personas = await library.personas_for_user(current_user, is_user_in_group)
    for persona in personas:
        if persona.id == persona_id:
            return {"persona": persona.to_dict()}
    # 404 for both "missing" and "not allowed" so the endpoint does not confirm
    # the existence of personas gated behind a group the caller is not in.
    raise HTTPException(status_code=404, detail="Persona not found")
