"""Load the local Help Markdown (the pre-#1107 source, still the default).

The search order and the legacy ``help-config.json`` fallback are unchanged
from the implementation that lived in ``atlas/routes/config_routes.py``; this
module only gives them a home that both the config route and the dedicated
Help route can share.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


def _fallback_search_paths(filename: str) -> List[Path]:
    """The search list used when the config manager cannot supply one."""
    atlas_root = Path(__file__).resolve().parents[2]
    project_root = atlas_root.parent
    return [
        project_root / "config" / "overrides" / filename,
        project_root / "config" / "defaults" / filename,
        atlas_root / "configfilesadmin" / filename,
        atlas_root / "configfiles" / filename,
        atlas_root / filename,
        project_root / filename,
    ]


def _search_paths(config_manager, filename: str) -> List[Path]:
    """Candidate paths for a config file, mirroring ConfigManager search."""
    try:
        return list(config_manager._search_paths(filename))  # type: ignore[attr-defined]
    except AttributeError:
        return _fallback_search_paths(filename)


def load_local_help_content(config_manager, app_settings) -> Tuple[str, Optional[str]]:
    """Return ``(content, file_path)`` for the local Help document.

    ``content`` is empty and ``file_path`` is ``None`` when no Help document
    can be found; the caller decides what to do about that.
    """
    help_config_filename = app_settings.help_config_file
    try:
        help_paths = _search_paths(config_manager, help_config_filename)
    except Exception as exc:  # noqa: BLE001 - a broken search must not break the page
        logger.warning("Error locating help content: %s", exc)
        return "", None

    found_path = None
    for candidate in help_paths:
        if candidate.exists():
            found_path = candidate
            break

    # Legacy fallback: if help.md was not found, try help-config.json
    if not found_path and help_config_filename.endswith(".md"):
        legacy_filename = help_config_filename.rsplit(".", 1)[0] + "-config.json"
        for candidate in _search_paths(config_manager, legacy_filename):
            if candidate.exists():
                found_path = candidate
                logger.info("Using legacy help config %s (migrate to help.md)", candidate)
                break

    if not found_path:
        logger.warning(
            "Help content file not found in any of these locations: %s",
            [str(p) for p in help_paths],
        )
        return "", None

    try:
        with open(found_path, "r", encoding="utf-8") as handle:
            content = handle.read()
    except Exception as exc:  # noqa: BLE001 - unreadable file must not break the page
        logger.warning("Error loading help content: %s", exc)
        return "", str(found_path)

    logger.info("Loaded help content from %s", found_path)
    return content, str(found_path)
