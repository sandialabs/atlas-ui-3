"""Runtime availability helpers for the Agent Portal."""

from __future__ import annotations

import importlib
import logging
import sys

logger = logging.getLogger(__name__)


def is_agent_portal_supported() -> bool:
    return not sys.platform.startswith("win")


def is_agent_portal_enabled(app_settings) -> bool:
    return bool(getattr(app_settings, "feature_agent_portal_enabled", False)) and is_agent_portal_supported()


def load_agent_portal_router():
    if not is_agent_portal_supported():
        logger.info("Agent Portal is disabled on Windows hosts")
        return None
    return importlib.import_module("atlas.routes.agent_portal_routes").router
