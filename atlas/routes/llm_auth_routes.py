"""LLM Authentication routes for per-user API key management.

Allows users to upload their own API keys for LLM models configured with
api_key_source: "user". Reuses MCPTokenStorage with key prefix "llm:{model}"
so LLM and MCP tokens coexist in the same encrypted store.

Updated: 2026-02-08
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from atlas.core.log_sanitizer import get_current_user, sanitize_for_logging
from atlas.infrastructure.app_factory import app_factory
from atlas.modules.mcp_tools.token_storage import get_token_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm/auth", tags=["llm-auth"])


class LLMTokenUpload(BaseModel):
    """Request body for uploading an LLM API key."""
    token: str
    expires_at: Optional[float] = None


@router.get("/status")
async def get_llm_auth_status(current_user: str = Depends(get_current_user)):
    """Get authentication status for all models with api_key_source: user."""
    try:
        config_manager = app_factory.get_config_manager()
        llm_config = config_manager.llm_config
        token_storage = get_token_storage()

        models_status = []
        for model_name, model_config in llm_config.models.items():
            api_key_source = getattr(model_config, "api_key_source", "system")
            if api_key_source != "user":
                continue

            storage_key = f"llm:{model_name}"
            stored_token = token_storage.get_token(current_user, storage_key)

            model_info = {
                "model_name": model_name,
                "description": model_config.description or "",
                "authenticated": stored_token is not None,
                "is_expired": stored_token.is_expired() if stored_token else False,
                "expires_at": stored_token.expires_at if stored_token else None,
            }
            models_status.append(model_info)

        return {
            "models": models_status,
            "user": current_user,
        }

    except Exception as e:
        logger.error("Error getting LLM auth status: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while fetching LLM auth status",
        )


@router.post("/{model_name}/token")
async def upload_llm_token(
    model_name: str,
    token_data: LLMTokenUpload,
    current_user: str = Depends(get_current_user),
):
    """Upload an API key for a model with api_key_source: user."""
    try:
        config_manager = app_factory.get_config_manager()
        llm_config = config_manager.llm_config

        if model_name not in llm_config.models:
            raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")

        model_config = llm_config.models[model_name]
        if getattr(model_config, "api_key_source", "system") != "user":
            raise HTTPException(
                status_code=400,
                detail=f"Model '{model_name}' does not accept per-user API keys",
            )

        if not token_data.token or not token_data.token.strip():
            raise HTTPException(status_code=400, detail="API key cannot be empty")

        token_storage = get_token_storage()
        storage_key = f"llm:{model_name}"

        stored = token_storage.store_token(
            user_email=current_user,
            server_name=storage_key,
            token_value=token_data.token.strip(),
            token_type="api_key",
            expires_at=token_data.expires_at,
        )

        logger.info(
            "User uploaded API key for LLM model '%s'",
            sanitize_for_logging(model_name),
        )

        return {
            "message": f"API key stored for model '{model_name}'",
            "model_name": model_name,
            "expires_at": stored.expires_at,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error uploading LLM token: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while uploading API key",
        )


@router.delete("/{model_name}/token")
async def remove_llm_token(
    model_name: str,
    current_user: str = Depends(get_current_user),
):
    """Remove stored API key for a model."""
    try:
        token_storage = get_token_storage()
        storage_key = f"llm:{model_name}"

        removed = token_storage.remove_token(current_user, storage_key)
        if not removed:
            raise HTTPException(
                status_code=404,
                detail=f"No API key found for model '{model_name}'",
            )

        logger.info(
            "User removed API key for LLM model '%s'",
            sanitize_for_logging(model_name),
        )

        return {
            "message": f"API key removed for model '{model_name}'",
            "model_name": model_name,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error removing LLM token: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while removing API key",
        )
