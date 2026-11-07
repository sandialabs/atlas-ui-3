# Retro Design System integration (issue #1119)

Last updated: 2026-11-06

The source of the historical aesthetics is **NovusGFX Retro Design System**
(<https://github.com/NovusGFX/retro-design-system>, MIT, reviewed at commit
`8988f50`). Atlas vendors three token sets initially:

- `01-mac-system-7` → `mac7`
- `02-windows-95` → `win95`
- `09-crt-phosphor` → `crt-phosphor`

These are **not** invented substitutes: `src/themes/vendor/retro-design-system/tokens.css`
contains the upstream design tokens with only `:root` selectors replaced by
scoped `[data-atlas-theme="..."]` selectors. The MIT license text is stored
alongside the tokens at `src/themes/vendor/retro-design-system/LICENSE`.
The small Atlas adapter `src/themes/theme-presets.css` maps them onto Atlas's
existing Tailwind color scale and reusable semantic chrome properties. The
upstream demos themselves are not imported because their global body, input,
and reset selectors would break the existing Atlas UI.

The frontend's `src/themes/themeRegistry.js` is the build-time allowlist.
Users may select only registered IDs in Settings → General; unknown saved
values fall back to Dark. No arbitrary runtime stylesheet injection is
allowed. A developer can add more from the 53-theme catalog by explicitly
registering a scoped token file and a tested Atlas adapter.

Atlas initially styles its header border, settings chrome/title bar, theme
choice cards, and focus rings with semantic tokens. Ordinary chat content and
security labels continue using the established component classes; this is an
incremental migration, **not** a promise of pixel-perfect OS emulation.

The radio gallery also links to its MIT-licensed upstream source; the theme
selection never dynamically fetches JavaScript, CSS, images or fonts.

### Reusable controls and live preview

`frontend/src/themes/RetroControls.jsx` implements `RetroWindow`,
`RetroButton`, and `RetroInput`. They are regular semantic HTML elements
(a region, native button, and explicitly labeled text input). Decorative
window glyphs are not clickable or announced as buttons. An optional
onClose callback instead renders a real labeled close button; windows with
no close behavior never show a misleading close affordance. Modal behavior,
focus trapping, and dialog semantics remain the responsibility of their
callers; the reusable window does not impose a modal.

The collapsed **Try live retro controls** section in Settings → General
instantiates these components as an interactive workbench. It echoes an
80-character maximum sample label as ordinary React text; it neither executes
commands nor sends data to an API. Border treatments and control surfaces
use only the registered Retro Design System token adapters. It is an integration example for future feature components.

The same `RetroWindow` and `RetroButton` now power the real General
Settings appearance toggle. The gallery uses `RetroInput` for its real,
client-side theme filter. These preserve their labels, existing settings
callbacks, and modal close guards. This is an incremental migration,
not a replacement of workflow logic.

The legacy Light/Dark quick toggle switches the **base light/dark mode**,
even if a non-default registered theme is active; the gallery controls exact
theme identity. This avoids a dark phosphor preset incorrectly reporting
that it is already light.

The root element exposes:
- `data-theme="dark|light"` for existing selectors
- `data-atlas-theme="<registered id>"` for the actual design

New UI chrome should prefer the semantic `--atlas-font-ui`,
`--atlas-surface`, `--atlas-border`, `--atlas-accent`,
and `--atlas-radius` properties; existing Tailwind grays remain a
compatibility bridge during incremental migration.

## Asset rights and accessibility

This PR contains only styles for classic desktop and phosphor-terminal
interfaces. It contains no historic artwork, proprietary fonts, icons, or
terminal-art rendering pipeline. Asset collections are a separate project;
redistribution would require independent provenance and rights review.
All text must remain accessible DOM content, and classification labels must
remain recognizable in every theme.

Theme previews are decorative; native HTML radio controls provide keyboard
and screen-reader selection. CRT animation and scanline effects have been
intentionally left out of the baseline (including for reduced-motion users).
