# Retro Design System integration (issue #1119)

The source of the historical aesthetics is **NovusGFX Retro Design System**
(<https://github.com/NovusGFX/retro-design-system>, MIT, reviewed at commit
`8988f50`). Atlas vendors only four token sets initially:

- `01-mac-system-7` → `mac7`
- `02-windows-95` → `win95`
- `09-crt-phosphor` → `crt-phosphor`
- `30-bbs-ansi` → `bbs-ansi`

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

The root element exposes:
- `data-theme="dark|light"` for existing selectors
- `data-atlas-theme="<registered id>"` for the actual design

New UI chrome should prefer the semantic `--atlas-font-ui`,
`--atlas-surface`, `--atlas-border`, `--atlas-accent`,
and `--atlas-radius` properties; existing Tailwind grays remain a
compatibility bridge during incremental migration.

## Art canon and accessibility

The CSS theme is **not** a license to redistribute historic BBS art.
ANSI/CP437 artifacts require separate manifests preserving creator,
artpack, provenance, rights, and the interaction mapping. This first
integration includes no historical art, proprietary fonts or OS icons.
The BBS museum's provenance model remains applicable to a future artifact
renderer. All text must remain accessible DOM content, and classification
labels must remain recognizable in every theme.

Theme previews are decorative; native HTML radio controls provide keyboard
and screen-reader selection. CRT animation and scanline effects have been
intentionally left out of the baseline (including for reduced-motion users).
