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
  ID/base-mode map applied before first paint, with the same Dark fallback as
  React; for a retro ID it also writes the `<link>` to that theme's stylesheet.
- `baseThemes.generated.css`: the content aliases for Atlas Dark and Light
  only. It always ships; everything retro is in the files above.
- `retroThemeModes.generated.js`: a frozen id-to-base-mode map that is all the
  app keeps of the catalog when it is compiled out, so a saved retro ID still
  resolves to its Light or Dark base.

### Feature flags

| Flag | Where | Default | Mechanism |
|---|---|---|---|
| `VITE_FEATURE_RETRO_THEMES` | build (Vite, Docker `ARG`) | `true` | `vite.config.js` aliases `@atlas/retro-themes` to `themes/retro/retroThemes.enabled.js` (exports the catalog) or `retroThemes.disabled.js` (empty catalog), and `retroCssPlugin` emits the per-theme stylesheets only when on (see *Per-theme CSS loading*). Only the literal `false` disables it; see `themes/retro/retroThemesBuildFlag.js`. `index.html` passes the value to the bootstrap as `data-retro-themes` and the CSS version as `data-retro-css` (empty when off). |
| `FEATURE_RETRO_THEMES_ENABLED` | backend `AppSettings` | `false` | Published as `features.retro_themes` in `/api/config/shell` and `/api/config`. `RetroThemeFeatureBridge` in `App.jsx` forwards it to `ThemeContext.setRetroThemesEnabled`. |

`ThemeContext` keeps the user's `selectedTheme` separately from the applied
`theme`: `theme = effectiveThemeId(selectedTheme, retroThemesAvailable)`, so a
retro choice falls back to its base mode while the gallery is unavailable and
comes back unchanged when it is re-enabled. The bootstrap applies the same rule
before React mounts. For the run-time flag it reads `data-retro-runtime`, which
the backend stamps on the bootstrap `<script>` tag every time it serves
`index.html` (`atlas/core/spa_index.py`), and only falls back to the cached
`chatui-config-cache` when that attribute is absent (Vite dev server).
`ThemeContext` starts from the same value, so the first paint and React agree
and a flag that was just flipped never flashes the old state, not even once. Vitest resolves the
alias to the enabled module; `retro-themes-build-off.test.jsx` mocks it to the
disabled one.

### Per-theme CSS loading

The three generated retro stylesheets are not bundled. Every top-level rule in
them is scoped to exactly one `[data-atlas-theme="<id>"]`, so at build time
`themes/retro/retroCssPlugin.js` splits them (`retroCssSplit.js`; tokens, then
catalog adapters, then chrome) into one minified file per retro theme,
`dist/assets/retro/<id>.css`, served by the existing `/assets` mount (and by a
middleware on the Vite dev server). The split fails the build if a rule is
unscoped, scoped to two themes, or an at-rule. The main bundle keeps only the
shared CSS (`index.css`, `theme-presets.css`, `baseThemes.generated.css`).

- **First paint.** When the bootstrap applies a retro ID (build and run-time
  flags on) it `document.write`s `<link rel="stylesheet"
  data-atlas-theme-css="<id>" href="/assets/retro/<id>.css?v=<version>">`,
  a parser-inserted sheet that blocks rendering like a static one, so there is
  no unstyled frame. `<version>` is a hash of the generated sources
  (`VITE_RETRO_CSS_VERSION`, stamped on the tag as `data-retro-css`). The
  plugin moves the bundle CSS `<link>` above the bootstrap so the retro sheet
  stays last in the cascade, as it was when it was bundled.
- **Switching.** `themes/retroThemeCss.js` appends the new theme's `<link>`,
  caches one load per ID, and adopts the bootstrap's `<link>`. `ThemeContext`
  keeps the previous theme applied until the new sheet has loaded, then
  switches; a theme already loaded switches synchronously.
- **Failure.** A sheet that errors (or never settles within 15 s) is removed and
  the theme renders as its Light/Dark base, as when the flag is off; the
  saved choice is kept, and choosing the theme again retries.
- **Flags off.** A `VITE_FEATURE_RETRO_THEMES=false` build emits no retro CSS
  and an empty version, and nothing is ever requested; with
  `FEATURE_RETRO_THEMES_ENABLED` off the effective theme is Light/Dark, which
  needs no extra CSS.
- **Gallery.** Swatches use the catalog's `preview` colours (JS data), so
  browsing the gallery loads no theme CSS; only applying a theme does.

Initial CSS for the default build (`npm run build`): 131.2 kB (26.7 kB gzip)
with Dark or Light, plus 3.1 to 7.4 kB (avg 4.9 kB, 1.5 kB gzip) for a retro
theme; previously every page loaded 378.3 kB (59.8 kB gzip). All 53 retro
files total 258.8 kB.

### Content tones and surfaces

Transcript text that used fixed Tailwind palette utilities now uses semantic
classes defined in `index.css` and fed per theme by the generator:
`atlas-fg-danger|warning|alert|info|success|agent` (log levels, system
notices, tool/approval/agent headings) and `atlas-text-gray-600|700` (dimmer
metadata such as sidebar timestamps and message counts, since the gray-600/700
steps are surface tints in the retro ramps). Each value is the closest shade of
the original hue that reaches 4.55:1 on every transcript surface.

Two structural rules keep that true on real pages:

- Compact transcript rows (tool calls, approvals, logs, system notices, agent
  status) and the composer get a gray-800 plate (`.atlas-transcript-row`,
  `.atlas-composer-plate`) in retro themes, so they never sit directly on desktop
  wallpaper such as the XP Bliss gradient or the Windows 95 teal.
- Status notices use `.atlas-notice-danger|warning|alert|info|success|agent`
  (composer warning rows via `ComposerNotice`, the agent-run strip, the
  Agent Mode no-tools callout, toasts, error boxes, admin status pills, log
  level badges, extraction-mode file chips). Each tone is an *opaque* tint
  (16% of the hue over the theme's gray-800, or over the window colour inside
  a content ramp) with text re-solved to 4.55:1 on that tint, generated as
  `--atlas-notice-<tone>-bg|fg|border`. This replaces translucent fills such as
  `bg-yellow-900/40 text-yellow-300`, whose contrast depended on whatever they
  happened to cover.
- Routed pages and admin views (LogViewer, Files page and panels, Telemetry,
  Admin dashboard, cards and modals, Help, Marketplace) use the theme-aware
  dark-first gray ramp. LogViewer and the Files page were written light-first
  with Tailwind `dark:` variants, which follow the OS preference
  (`darkMode: 'media'`), not the Atlas theme; those variants are gone. Coloured
  status text uses `atlas-fg-*`, solid coloured buttons use a shade that keeps
  white text at AA (yellow buttons use black text), and text on the gray-600
  tint uses gray-50.
- Chat-side panels (composer `ToolSelector`, `PromptSelector`,
  `WorkspaceSelector`, `ContextUsageMeter`; the Tools tab `ToolsPanel`;
  `GatewayModelPicker`, sidebar "Load more", `CanvasPanel`,
  `ElicitationDialog`, `TokenInputModal`, `PromptManager`,
  `CaptureConsentSection`) follow the same rules: selected menu rows and
  connect/OAuth notices use `.atlas-notice-<tone>` instead of
  `bg-<hue>-900/30` or `bg-<hue>-600/20`, and secondary text uses gray-400
  (gray-300 inside menus whose selected row is tinted), never gray-500.
- Where a theme's windows or header strip are painted in a different key from
  its palette (Memphis and Op Art white windows, Game Boy LCD green, DOS CGA
  gray menubar), the generator emits a window/header "content ramp" that
  re-keys the gray steps and tones inside `.atlas-theme-chrome`,
  `.atlas-retro-window` and `.atlas-theme-header`.

The three previously shipped theme IDs (`mac7`, `win95`,
`crt-phosphor`) and their explicit adapters remain compatible with saved
preferences. All other IDs derive deterministically from their upstream
manifest slugs. For example, `03-windows-xp-luna` becomes
`windows-xp-luna`.

The generated assets are static: **no remote fetch, remotely installed CSS,
arbitrary script, external font, logo, bitmap or historical art**. The only
request is the same-origin `/assets/retro/<id>.css` for the applied theme.

After `cd frontend && npm run build`, verify the rendered CSS against all
55 modes using an installed Chrome/Chromium binary. The check measures each
theme with only the shared bundle and that theme's `assets/retro/<id>.css`
enabled, as the lazy loader ships it, and fails if retro CSS leaks into the
bundle or a registered theme has no file:

```bash
python3 frontend/scripts/check-retro-browser.py --chrome /path/to/chrome
```

The check builds `frontend/scripts/contrast-fixture/` (Vite library build)
and renders the **real** React components in every theme: `Header`,
`ConversationTreeRow` rows, `Message` (user and assistant bubbles, tool calls
in every state, tool logs at every level, system notices, agent status),
`ToolApprovalMessage` in compact and classic layouts, `AgentQuestionPrompt`,
`RetroWindow`/`RetroButton`/`RetroWorkbench`, and `SettingsPanel` with the
theme gallery, plus the composer warning rows (`ComposerNotice`,
`AgentRunStatus`, `EnabledToolsIndicator`, `EnabledDataSourcesIndicator`,
`AgentModeNoToolsBanner`), toasts of every kind, and the routed pages
`LogViewer`, `FilesPage`, `AllFilesView`, `SessionFilesView`,
`TelemetryDashboard` (all five tabs), `AdminDashboard` with its cards,
`AdminModal` (health and MCP-health views), `HelpPage` and `MarketplacePanel`,
and the chat-side panels listed above in their open, populated states (the
fixture opens each composer popover and expands every Tools-tab server row)
and error states (gateway load failure, canvas error, token rejection,
prompt-load, capture-consent and Settings Globus error boxes).
Only the context providers that need a backend (chat, WebSocket, marketplace,
Globus) are replaced by static stubs, and `fetch` answers from canned,
synthetic responses shaped like the real endpoints
(`contrast-fixture/stubs/fetchFixtures.js`); the page waits until those loads
settle, and the check fails if any of the 38 regions renders no measurable
text. Every visible
text node is measured at rest and again with its hover styles forced, against
its composited background: alpha and `opacity` are blended through the
ancestors, and gradient backgrounds are sampled at every colour stop. Text
below 4.5:1 fails. Text whose result depends on the gradient position (some
stops pass, some fail) or that sits on a `url()` image is listed as
**UNMEASURABLE** with its ratio range; nothing is skipped silently.
Full-cover `::before`/`::after` boxes with a background on any ancestor are
composited as well (under the content for a negative `z-index`, otherwise as
a tint over text and backdrop); the page self-tests that detection. The
shipped CSS currently has none, because the generator folds demo overlays such
as scanlines into the fills, so every theme reports `pseudoOverlays: []`.
Partial-size decorations and `text-shadow` are not modelled. Disabled
controls are measured too; WCAG 1.4.3 exempts inactive components, so below
4.5:1 they are listed as **DISABLED** instead of failing. Use
`--report out.json` for the full per-theme list. It also checks the
keyboard-focus outline against both its button and surrounding window (at
least 3:1 on the solid fills, with gradient stops below 3:1 reported as
unmeasurable), a 3px width, that classification badges are identical across
themes, and matching dark/light color-scheme. The Build Artifacts GitHub workflow runs
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
