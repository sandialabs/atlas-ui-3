"""Stamp authoritative runtime theme policy on the blocking SPA bootstrap script.

The first paint must not trust localStorage or last-visit config: a deployment
which revokes an allowed retro theme must not show even one unauthorized frame.
"""
from __future__ import annotations

from pathlib import Path

from atlas.core.theme_policy import BASE_THEME_IDS, parse_allowed_themes

BOOTSTRAP_TAG = '<script src="/theme-bootstrap.js"'


def render_spa_index(
    index_path: Path,
    retro_themes_enabled: bool,
    allowed_themes: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Stamp the live runtime gate and vetted allowed theme IDs before first paint."""
    html = index_path.read_text(encoding="utf-8")
    flag = "true" if retro_themes_enabled else "false"
    # Never interpolate arbitrary raw administrator input into HTML attributes.
    safe = parse_allowed_themes(",".join(allowed_themes or BASE_THEME_IDS))
    ids = ",".join(safe)
    stamp = f'{BOOTSTRAP_TAG} data-retro-runtime="{flag}" data-allowed-themes="{ids}"'
    return html.replace(BOOTSTRAP_TAG, stamp, 1)
