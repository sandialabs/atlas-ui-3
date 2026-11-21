"""Administrator-controlled allowlist of Atlas themes.

The base Light/Dark themes always remain permitted so disabled build/runtime
feature gates and revoked retro choices have an unambiguous safe fallback.
Only exact known local theme IDs can enter policy; no remote assets or code.
Keep the static retro ID catalog in sync with the frontend generated modes.
"""
from __future__ import annotations

BASE_THEME_IDS = ("dark", "light")
RETRO_THEME_IDS = frozenset((
    "8bit-arcade",
    "amiga-workbench",
    "aqua-osx",
    "bauhaus",
    "bbs-ansi",
    "beos",
    "blueprint-cad",
    "braun-rams",
    "brutalist-web",
    "btop-meters",
    "c64-basic",
    "cassette-futurism",
    "claymorphism",
    "crt-phosphor",
    "default-browser",
    "dos-cga",
    "duotone-poster",
    "flat-2013",
    "frutiger-aero",
    "gameboy-dmg",
    "geocities-web10",
    "glassmorphism",
    "glitch-databend",
    "grid-paper",
    "hypnagogic",
    "ibm-3270",
    "mac7",
    "macos9-platinum",
    "matrix-rain",
    "maximalist-banners",
    "memphis",
    "midnight-commander",
    "monochrome-zen",
    "nethack-ascii",
    "neumorphism",
    "nextstep",
    "op-art",
    "os2-warp",
    "pop-art-lichtenstein",
    "ps1-tech",
    "risograph",
    "swiss-intl",
    "teletext",
    "templeos",
    "tron-vector",
    "vaporwave",
    "vhs-tracking",
    "web20-glossy",
    "win95",
    "winamp-skin",
    "windows-xp-luna",
    "wireframe-sketch",
    "y2k-chrome",
))


def parse_allowed_themes(raw: str | None) -> tuple[str, ...]:
    """Return base themes plus explicitly permitted, known retro identities."""
    added: list[str] = []
    if isinstance(raw, str):
        for value in raw.split(","):
            theme = value.strip().lower()
            if theme in RETRO_THEME_IDS and theme not in added:
                added.append(theme)
    return (*BASE_THEME_IDS, *added)
