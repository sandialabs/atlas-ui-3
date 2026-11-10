# Retro Design System in Atlas (issue #1119)

Atlas's developer-controlled theme registry includes the **complete
53-design Retro Design System catalog**, plus the existing Light and Dark
modes (**55 selectable themes**).

Source: [NovusGFX/retro-design-system](https://github.com/NovusGFX/retro-design-system),
MIT license, pinned at commit
`8988f50a514e7f845dbe5ad190faa2afd926f0b4`.

## Source, regeneration, and attribution

All 53 original upstream token files, the complete `manifest.json`, the
MIT license, and a copy of each standalone `styles/*/index.html` demo are
vendored under `frontend/src/themes/vendor/retro-design-system/`. The
offline generator:

```bash
python3 frontend/scripts/build-retro-catalog.py
python3 frontend/scripts/build-retro-catalog.py --check
```

produces five deterministic assets:

- `vendor/retro-design-system/tokens.css`: all 53 source token sets, with
  **only their root selectors scoped to their registered theme ID**.
- `retroCatalog.generated.js`: names, eras, years, tags, palette previews,
  source identifiers, and base light/dark modes from the upstream manifest.
- `retroCatalog.generated.css`: safe semantic Atlas color/control adapters
  for designs not already covered by the initial three hand-reviewed gray
  ramps, plus surface-validated link, syntax, and header foregrounds for
  all 55 themes.
- `retroChrome.generated.css`: each design's original demo CSS, scoped to
  `[data-atlas-theme]` and mapped onto Atlas surfaces (`RetroWindow`,
  `RetroButton`, `RetroInput`, Settings chrome, chat accents, header, and
  the desktop/backdrop). Global resets, scripts, webfonts, remote assets,
  and animations are stripped. Every design therefore uses its upstream
  window, titlebar, button, input, background, and font stack rather than
  an invented motif.
- `frontend/public/theme-bootstrap.js`: a synchronous, registry-derived theme
  ID/base-mode map applied before first paint, with the same Dark fallback as React.

The three previously shipped theme IDs (`mac7`, `win95`,
`crt-phosphor`) and their explicit adapters remain compatible with saved
preferences. All other IDs derive deterministically from their upstream
manifest slugs. For example, `03-windows-xp-luna` becomes
`windows-xp-luna`.

The generated assets are static: **no network fetch, remotely installed CSS,
arbitrary script, external font, logo, bitmap or historical art**.

After `cd frontend && npm run build`, verify the rendered CSS against all
55 modes using an installed Chrome/Chromium binary:

```bash
python3 frontend/scripts/check-retro-browser.py --chrome /path/to/chrome
```

The headless browser checks computed body, window, title, button and input
text contrast, assistant links, highlighted syntax, fixed classification/log
badges, header controls (including hover), user bubbles, and hovered/disabled
labels (at least 4.5:1). Transparent surfaces are composited through their
ancestors, and badge colors must remain identical across the catalog. It also
checks the keyboard-focus outline against both
its button and surrounding window (at least 3:1), focus visibility, and
matching dark/light color-scheme. The Build Artifacts GitHub workflow runs
this check on the built frontend using the runner's installed Chrome. This
is a targeted theme contrast test, not a blanket accessibility certification.
The source token parser rejects additional selectors, non-token CSS, and external
resource imports. The demo CSS importer likewise rejects remote resources
and does not emit animation or webfonts. The original token files, demos,
and license remain inspectable and attributed. CI verifies the generated
output is up to date.

Two upstream designs, **Brutalist Web (#40)** and **Default Browser (#47)**,
intentionally have *empty token sets*: they derive their appearance from
regular browser semantics and standalone examples. The generator retains
their exact empty source tokens, while using the source manifest's palette
(or the browser white-background fallback) to provide an Atlas control theme.

## What does "import the whole collection" mean?

All 53 design identities are selectable and have scoped source tokens, a
catalog entry, preview colors, semantic surface/text colors, a source-driven
font stack, and real interactive window/button/input styling. The 53 original
standalone HTML demo pages **are not embedded or run inside Atlas**.
The generator copies their CSS only: global `*` / `body` resets, scripts,
`@font-face`, remote assets, and animations are dropped, and the remaining
rules are scoped to `[data-atlas-theme]` then aliased onto Atlas
components. This is still not a pixel-perfect standalone-page reproduction
inside a production chat interface, but the chrome, type, and backgrounds
come from the original design code rather than invented motif adapters.
These are real, accessible React components with theme-aware CSS; they
are not literal re-creations of 53 entire desktop environments.

The catalog includes #30, **BBS ANSI Art**, because that is one of the 53
upstream designs. Here it is **only CSS color tokens and semantic styling**:
there are no `.ans` files, ANSI escape-sequence interpreters, CP437
renderers, artpack assets, or terminal emulators.

## Theme selection

`ThemeContext` preserves legacy `data-theme="dark|light"` selectors and
stores exact registered IDs in `atlas-theme` local storage; unknown IDs
fall back to Dark. `data-atlas-theme` identifies the selected design.
The General Settings gallery provides accessible native radio controls,
search over names/tags/eras, a category filter, and a source attribution link.

To make the full 55-theme gallery practical, users can **favorite the current
theme** and filter to favorites. Favorites persist locally under
`atlas-theme-favorites`; storage values are parsed defensively, deduplicated,
and restricted to registered theme IDs, with a session-only fallback when
storage is disabled. No favorites are sent to a server or used to load CSS.

The currently active theme is always named above the gallery. When search,
category, or favorites filtering hides it, **Show selected theme** resets the
filters and returns keyboard focus to the selected native radio. This avoids
losing the active selection within a large filtered collection.


The Light/Dark shortcut replaces the selected retro preset with the opposite
classic **Light** or **Dark** theme (based on the preset’s base mode). It does
not mutate the user's configuration for models, tools, or conversation settings.

## Live components and accessibility

`frontend/src/themes/RetroControls.jsx` exports semantic `RetroWindow`,
`RetroButton`, and `RetroInput`. The General Appearance setting uses
real retro components, not a visual mock. The collapsed **Try live retro
controls** section contains a local, bounded interactive workbench that
neither executes commands nor makes API requests.

`RetroWindow` renders a labeled section (not an implicit modal); optional
`onClose` renders a real labeled button. Native inputs and radio controls
remain keyboard and screen-reader accessible. Accent outlines retain
visible focus. The generator constrains small gray utility text to a
WCAG AA minimum 4.5:1 contrast ratio against the themed panel surface,
with automated regression tests. This is a *specific contrast check*,
not a claim of full-page accessibility certification.

Theme-specific colors must not silently recolor compliance or security
classification indicators. Optional scanlines, animations, and CRT effects
are excluded from this integration. No new sound, motion, or shader runtime
is added. The registry is an incremental UI theming layer, not a replacement
for Atlas application behavior.
