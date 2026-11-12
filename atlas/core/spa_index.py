"""Serve the SPA entry (index.html) with run-time flags the first paint needs.

public/theme-bootstrap.js runs before React and before /api/config returns.
Without help it can only trust the config cached by the previous visit, so a
deployment that just turned FEATURE_RETRO_THEMES_ENABLED off showed a saved
retro theme for one load (and one that turned it on showed the base theme).
Stamping the live flag onto the bootstrap <script> tag removes that one-time
flash: the attribute wins over the cache.
"""

from pathlib import Path

BOOTSTRAP_TAG = '<script src="/theme-bootstrap.js"'


def render_spa_index(index_path: Path, retro_themes_enabled: bool) -> str:
    """Return index.html with data-retro-runtime="true|false" on the bootstrap tag.

    The page is returned unchanged when the tag is missing (custom builds).
    """
    html = index_path.read_text(encoding="utf-8")
    flag = "true" if retro_themes_enabled else "false"
    return html.replace(BOOTSTRAP_TAG, f'{BOOTSTRAP_TAG} data-retro-runtime="{flag}"', 1)
