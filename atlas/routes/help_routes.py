"""Help page endpoints (issue #1107).

``GET /api/help`` is the single place the browser asks "what should the Help
page show?". The answer depends on the configured display mode:

``local``
    The local Markdown, exactly as before.
``remote_markdown``
    The document fetched from the configured documentation host, with its
    image references resolved against that host.
``external_link``
    No content at all -- just the URL the Help button should open in a new
    tab. Nothing is fetched in this mode.

``GET /api/help/image`` serves an image the hosted document references. It
only ever talks to the configured documentation host (or a host the operator
listed), so it is a proxy with a fixed allowlist rather than an open fetcher.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from atlas.core.log_sanitizer import get_current_user
from atlas.infrastructure.app_factory import app_factory
from atlas.modules.help.help_config import (
    HELP_MODE_EXTERNAL_LINK,
    HELP_MODE_LOCAL,
    report_help_config_problem,
    resolve_help_config,
)
from atlas.modules.help.local_content import load_local_help_content
from atlas.modules.help.remote_content import (
    HelpFetchError,
    HelpPolicyError,
    fetch_remote_image,
    fetch_remote_markdown,
    rewrite_markdown_images,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["help"])


@router.get("/help")
async def get_help(current_user: str = Depends(get_current_user)):  # noqa: ARG001
    """Return the Help content for the configured display mode."""
    config_manager = app_factory.get_config_manager()
    app_settings = config_manager.app_settings
    config = resolve_help_config(app_settings)
    report_help_config_problem(config)

    if config.mode == HELP_MODE_EXTERNAL_LINK:
        # Nothing is fetched and nothing is rendered: the browser opens the
        # configured documentation site in a new tab.
        return {
            "mode": config.mode,
            "requested_mode": config.requested_mode,
            "content": "",
            "content_url": config.content_url,
            "link_url": config.content_url,
            "cached": False,
            "stale": False,
            "source": "external_link",
            "error": config.error,
        }

    local_content, local_path = load_local_help_content(config_manager, app_settings)

    if config.mode == HELP_MODE_LOCAL:
        return {
            "mode": config.mode,
            "requested_mode": config.requested_mode,
            "content": local_content,
            "content_url": "",
            "link_url": "",
            "cached": False,
            "stale": False,
            "source": local_path or "local",
            "error": config.error,
        }

    result = await fetch_remote_markdown(config)
    if result.content:
        document_url = result.source_url or config.content_url
        return {
            "mode": config.mode,
            "requested_mode": config.requested_mode,
            "content": rewrite_markdown_images(result.content, document_url, config),
            "content_url": config.content_url,
            "link_url": "",
            "cached": result.cached,
            "stale": result.stale,
            "source": document_url,
            "error": config.error or result.error,
        }

    # The host could not be reached and nothing was cached: fall back to the
    # local document rather than showing an empty Help page.
    return {
        "mode": HELP_MODE_LOCAL,
        "requested_mode": config.requested_mode,
        "content": local_content,
        "content_url": config.content_url,
        "link_url": "",
        "cached": False,
        "stale": False,
        "source": local_path or "local",
        "error": (
            f"{config.error} " if config.error else ""
        ) + (
            result.error
            or "The hosted documentation could not be reached and no cached copy was available."
        ),
        "fallback": "local",
    }


@router.get("/help/image")
async def get_help_image(
    url: str = Query(..., description="Absolute URL of the image to fetch"),
    current_user: str = Depends(get_current_user),  # noqa: ARG001
):
    """Serve an image referenced by the hosted Help document.

    Only the configured documentation host (and any host the operator listed
    in ``HELP_ALLOWED_HOSTS``) may be fetched.
    """
    config_manager = app_factory.get_config_manager()
    config = resolve_help_config(config_manager.app_settings)

    if not config.content_url:
        raise HTTPException(
            status_code=404,
            detail="No external documentation is configured, so there are no Help images to serve.",
        )

    try:
        body, content_type = await fetch_remote_image(url, config)
    except HelpPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except HelpFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=300"},
    )
