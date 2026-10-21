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

``GET /api/help/image`` serves an image the hosted document references. The
request carries the *identifier* of an image, not a URL: the identifier is
looked up in the registry of the images the document references, so the
proxy serves the images of that document and never an arbitrary path of the
documentation host. It only ever talks to the configured documentation host
(or a host the operator listed), so it is a proxy with a fixed allowlist
rather than an open fetcher.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from atlas.core.auth import is_user_in_group
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
    register_image_targets,
    resolve_image_target,
    rewrite_markdown_images,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["help"])

# What a reader sees when the deployment's Help configuration is wrong. The
# detailed message names the settings an operator has to change, so it is
# for the operator: everyone else gets this.
NEUTRAL_CONFIG_ERROR = (
    "The Help page is not configured correctly. Please contact your administrator."
)


async def _config_error_for(current_user: str, app_settings, message: Optional[str]):
    """The configuration error, in the detail the reader may see.

    The message names the environment variables an operator has to change.
    That is useful in the Admin Dashboard and in the log, and it is not
    something every reader needs to see, so a reader who is not an
    administrator gets a neutral notice instead.
    """
    if not message:
        return None
    try:
        if await is_user_in_group(current_user, app_settings.admin_group):
            return message
    except Exception:  # noqa: BLE001 - the notice must not fail the page
        logger.warning("Could not check the admin group for the Help notice", exc_info=True)
    return NEUTRAL_CONFIG_ERROR


@router.get("/help")
async def get_help(current_user: str = Depends(get_current_user)):  # noqa: ARG001
    """Return the Help content for the configured display mode."""
    config_manager = app_factory.get_config_manager()
    app_settings = config_manager.app_settings
    config = resolve_help_config(app_settings)
    report_help_config_problem(config)
    config_error = await _config_error_for(current_user, app_settings, config.error)

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
            "error": config_error,
            "fetch_error": None,
            "fallback": None,
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
            # A configuration problem and a documentation host that is down
            # are different things, so they are reported separately: the
            # first needs an operator to change the configuration, the
            # second usually resolves itself.
            "error": config_error,
            "fetch_error": None,
            "fallback": None,
        }

    result = await fetch_remote_markdown(config)
    if result.content:
        document_url = result.source_url or config.content_url
        rewritten = rewrite_markdown_images(result.content, document_url, config)
        # The image proxy serves the images of this document, and only
        # those. The registry is keyed by the configured URL, so a
        # redirected document is found under the same key.
        register_image_targets(config.content_url, rewritten.image_targets)
        return {
            "mode": config.mode,
            "requested_mode": config.requested_mode,
            "content": rewritten.markdown,
            "content_url": config.content_url,
            "link_url": "",
            "cached": result.cached,
            "stale": result.stale,
            "source": document_url,
            "error": config_error,
            "fetch_error": result.error,
            "fallback": None,
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
        "error": config_error,
        "fetch_error": result.error
        or "The hosted documentation could not be reached and no cached copy was available.",
        "fallback": "local",
    }


@router.get("/help/image")
async def get_help_image(
    h: str = Query(..., description="Identifier of the image to serve"),
    current_user: str = Depends(get_current_user),  # noqa: ARG001
):
    """Serve an image referenced by the hosted Help document.

    The request carries the identifier of an image, not a URL: the
    identifier is looked up in the registry of the images the hosted
    document references, so the proxy can only serve the images of that
    document and never an arbitrary path of the documentation host.
    """
    config_manager = app_factory.get_config_manager()
    config = resolve_help_config(config_manager.app_settings)

    if not config.content_url:
        raise HTTPException(
            status_code=404,
            detail="No external documentation is configured, so there are no Help images to serve.",
        )

    url = await resolve_image_target(h, config)
    if url is None:
        raise HTTPException(
            status_code=404,
            detail="That image is not referenced by the hosted documentation.",
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
