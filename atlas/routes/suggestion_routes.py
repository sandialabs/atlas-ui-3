"""Follow-up question suggestion routes.

Provides an endpoint for generating AI-powered follow-up question suggestions
based on the current conversation history.
"""

import json
import logging
import re
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from atlas.core.log_sanitizer import get_current_user
from atlas.infrastructure.app_factory import app_factory

logger = logging.getLogger(__name__)

suggestion_router = APIRouter(prefix="/api", tags=["suggestions"])

_SUGGESTION_SYSTEM_PROMPT = (
    "You are a helpful assistant. Based on the conversation provided, generate exactly 3 "
    "short, relevant follow-up questions the user might want to ask next. "
    "Return ONLY a JSON array of 3 question strings with no extra text or explanation. "
    'Example: ["What does that mean?", "Can you give an example?", "How does that compare to X?"]'
)


class SuggestFollowupsRequest(BaseModel):
    """Request body for follow-up suggestion generation."""

    messages: List[Dict] = Field(..., description="Conversation messages (role/content pairs)")
    model: str = Field(..., description="LLM model to use for generating suggestions")


class SuggestFollowupsResponse(BaseModel):
    """Response containing generated follow-up questions."""

    questions: List[str]


@suggestion_router.post("/suggest_followups", response_model=SuggestFollowupsResponse)
async def suggest_followups(
    request: SuggestFollowupsRequest,
    current_user: str = Depends(get_current_user),
) -> SuggestFollowupsResponse:
    """Generate follow-up question suggestions based on conversation history.

    Requires the ``FEATURE_FOLLOWUP_SUGGESTIONS_ENABLED`` feature flag to be set.
    Returns an empty list when the feature is disabled or no suggestions can be generated.
    """
    config_manager = app_factory.get_config_manager()
    if not config_manager.app_settings.feature_followup_suggestions_enabled:
        raise HTTPException(status_code=404, detail="Feature not enabled")

    llm = app_factory.get_llm_caller()

    # Filter conversation to only user/assistant messages with content
    conv_messages = [
        {"role": m.get("role", "user"), "content": m.get("content", "")}
        for m in request.messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]

    if not conv_messages:
        return SuggestFollowupsResponse(questions=[])

    # Build prompt: system instruction followed by the conversation
    suggestion_messages = [
        {"role": "system", "content": _SUGGESTION_SYSTEM_PROMPT},
        *conv_messages,
        {"role": "user", "content": "Generate 3 follow-up questions based on the conversation above."},
    ]

    try:
        response = await llm.call_plain(
            request.model,
            suggestion_messages,
            temperature=0.7,
            user_email=current_user,
        )

        # Extract JSON array from response (LLM may wrap it in backticks or prose).
        # Use a greedy pattern to match the outermost array including nested structures.
        match = re.search(r"\[.*\]", response, re.DOTALL)
        if match:
            try:
                questions = json.loads(match.group())
            except json.JSONDecodeError as parse_exc:
                logger.warning("Could not parse follow-up suggestions JSON: %s", parse_exc)
                return SuggestFollowupsResponse(questions=[])
            questions = [q.strip() for q in questions if isinstance(q, str) and q.strip()]
            return SuggestFollowupsResponse(questions=questions[:3])
    except Exception as exc:
        logger.warning("Failed to generate follow-up suggestions: %s", exc)

    return SuggestFollowupsResponse(questions=[])
