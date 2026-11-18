import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { THEME_REGISTRY } from '../themes/themeRegistry'

const read = path => readFileSync(resolve(process.cwd(), 'src/themes', path), 'utf8')
const generated = read('retroChrome.generated.css')
const catalog = read('retroCatalog.generated.css')
describe('complete Retro Design System chrome coverage', () => {
  it('maps all 53 vendored demos onto scoped Atlas window and control classes', () => {
    const retro = THEME_REGISTRY.filter(entry => entry.reference)
    expect(retro).toHaveLength(53)
    for (const entry of retro) {
      expect(
        generated.includes(`[data-atlas-theme="${entry.id}"] .atlas-retro-window`),
        `${entry.id} is missing .atlas-retro-window`,
      ).toBe(true)
      expect(
        generated.includes(`[data-atlas-theme="${entry.id}"] .atlas-retro-window-titlebar`)
        || generated.includes(`--atlas-user-bubble-bg`),
        `${entry.id} is missing a titlebar or accent mapping`,
      ).toBe(true)
    }
    expect((generated.match(/\/\* #\d{2} — /g) || [])).toHaveLength(53)
  })

  it('uses the original System 7, Aqua, and Win95 demo rules rather than invented motifs', () => {
    expect(generated).toMatch(/\[data-atlas-theme="mac7"\] .atlas-retro-window-titlebar[^{]*\{[^}]*repeating-linear-gradient/s)
    expect(generated).toMatch(/\[data-atlas-theme="mac7"\] \.atlas-retro-window-close/)
    expect(generated).toContain('--atlas-font-ui: var(--font-sys)')
    expect(generated).toMatch(/\[data-atlas-theme="aqua-osx"\] .atlas-retro-window-titlebar[^{]*\{[^}]*linear-gradient/s)
    expect(generated).toContain('[data-atlas-theme="aqua-osx"] .atlas-retro-traffic')
    expect(generated).toContain('var(--jelly-red)')
    expect(generated).toContain('--atlas-user-bubble-bg: #4d90fe')
    expect(generated).toMatch(/\[data-atlas-theme="win95"\] \.atlas-theme-desktop[^{]*\{[^}]*background: var\(--bg\)/s)
    expect(generated).toContain('--atlas-user-bubble-bg: #000080')
    expect(read('theme-presets.css')).toContain('.atlas-theme-preview[data-preview="mac7"] .atlas-theme-preview-bar')
  })

  it('keeps matrix-rain green and GeoCities typography from the upstream demos', () => {
    expect(generated).toMatch(/\[data-atlas-theme="matrix-rain"\] .atlas-retro-window[^{]*\{[^}]*var\(--mx\)/s)
    expect(generated).not.toMatch(/\[data-atlas-theme="matrix-rain"\][^{]*\{[^}]*--atlas-user-bubble-bg: #ff/s)
    expect(generated).toMatch(/\[data-atlas-theme="matrix-rain"\][^{]*\{[^}]*--atlas-user-bubble-bg: #62ff9d/s)
    expect(generated).toContain('--atlas-font-ui: var(--font)')
    expect(generated).toMatch(/\[data-atlas-theme="geocities-web10"\] \.atlas-theme-desktop[^{]*\{[^}]*radial-gradient/s)
    expect(generated).toContain('--atlas-user-bubble-bg: #ff3399')
    expect(generated).toMatch(/\[data-atlas-theme="nethack-ascii"\][^{]*\{[^}]*--atlas-user-bubble-bg: #ffff00/s)
    expect(generated).toMatch(/\[data-atlas-theme="bbs-ansi"\][^{]*\{[^}]*--atlas-user-bubble-bg: #ffff55/s)
  })

  it('renders the real close control as the demo close light instead of a second X', () => {
    // Aqua: the red jelly light is the close button; the decorative copy and
    // Atlas's X glyph are hidden so only three lights show.
    expect(generated).toMatch(/\[data-atlas-theme="aqua-osx"\] \.atlas-theme-titlebar > \.atlas-theme-close \{[^}]*order: -1;[^}]*background: var\(--jelly-red\)/s)
    expect(generated).toMatch(/\[data-atlas-theme="aqua-osx"\] \.atlas-theme-titlebar \.atlas-retro-traffic-close,[^{]*\{\s*display: none;/s)
    expect(generated).toMatch(/\[data-atlas-theme="aqua-osx"\] \.atlas-theme-close svg \{\s*display: none;/s)
    // BeOS: the caption is the yellow .tab, sized like a tab, with the red dot as close.
    expect(generated).toMatch(/\[data-atlas-theme="beos"\] \.atlas-theme-titlebar \{[^}]*background: var\(--yellow\)/s)
    expect(generated).toMatch(/\[data-atlas-theme="beos"\] \.atlas-theme-titlebar \{[^}]*width: max-content/s)
    expect(generated).toMatch(/\[data-atlas-theme="beos"\] \.atlas-theme-titlebar > \.atlas-theme-close \{[^}]*background: var\(--red-dot\)/s)
    expect(generated).not.toMatch(/\[data-atlas-theme="beos"\] \.atlas-theme-titlebar \{[^}]*background: #404040/s)
    // Decorative dots outside a caption (Memphis, Zen) must not become close controls.
    expect(generated).not.toMatch(/\[data-atlas-theme="memphis"\] \.atlas-theme-titlebar > \.atlas-theme-close/)
  })

  it('redraws leftover caption details from the demos without blocked CSS', () => {
    // BeOS .tab::after skewX(30deg) wedge becomes a background strip: no
    // transform, clip-path, or pseudo-element content enters Atlas.
    expect(generated).toMatch(/\[data-atlas-theme="beos"\] \.atlas-theme-titlebar \{[^}]*padding-right: calc\(20px \+ 13px\);[^}]*border-right: 0;[^}]*linear-gradient\(to top right, transparent[^}]*var\(--chrome-darker\)[^}]*var\(--chrome\) calc\(50% \+ 0\.75px\)\);[^}]*background-size: 13px 100%/s)
    // GeoCities keeps the upstream yellow marquee text; only the red deepens to pass AA.
    expect(generated).toMatch(/\[data-atlas-theme="geocities-web10"\] \.atlas-theme-titlebar \{[^}]*color: var\(--yellow\);[^}]*background-color: #e30000;/s)
    // Amiga: the caption sits on the .titlebar-inner plate; the close is a .gadget at the left.
    expect(generated).toMatch(/\[data-atlas-theme="amiga-workbench"\] \.atlas-theme-titlebar h2 \{[^}]*background: var\(--wb-white\);[^}]*color: var\(--wb-black\);[^}]*flex: none;/s)
    expect(generated).toMatch(/\[data-atlas-theme="amiga-workbench"\] \.atlas-theme-titlebar > \.atlas-theme-close \{[^}]*order: -1;[^}]*color: #000000;/s)
    // The Settings caption follows the mapped caption colour instead of Tailwind gray.
    expect(generated).toMatch(/\[data-atlas-theme="windows-xp-luna"\] \.atlas-theme-titlebar h2,\s*\[data-atlas-theme="windows-xp-luna"\] \.atlas-theme-titlebar h2 > \[aria-hidden="true"\] \{\s*color: inherit;/s)
    for (const legacy of ['mac7', 'win95', 'crt-phosphor']) {
      expect(generated).not.toContain(`[data-atlas-theme="${legacy}"] .atlas-theme-titlebar h2 > [aria-hidden="true"]`)
    }
  })

  it('maps text-drawn and nested-frame chrome for GeoCities, NetHack, and BBS', () => {
    expect(generated).toMatch(/\[data-atlas-theme="geocities-web10"\] \.atlas-theme-chrome\[role="dialog"\] \{[^}]*border-image: repeating-linear-gradient\(45deg, var\(--yellow\)/s)
    expect(generated).toMatch(/\[data-atlas-theme="geocities-web10"\] \.atlas-retro-window,[^{]*\{[^}]*ridge var\(--hotpink\)/s)
    expect(generated).toMatch(/\[data-atlas-theme="nethack-ascii"\] \.atlas-retro-window,[^{]*\{[^}]*dashed var\(--wall\)/s)
    expect(generated).toMatch(/\[data-atlas-theme="nethack-ascii"\] \.atlas-retro-window-title,[^{]*\{[^}]*var\(--monster-green\)/s)
    expect(generated).toMatch(/\[data-atlas-theme="bbs-ansi"\] \.atlas-retro-window,[^{]*\{[^}]*double var\(--yellow-hi\)/s)
    expect(generated).toMatch(/\[data-atlas-theme="bbs-ansi"\] \.atlas-retro-window-titlebar,[^{]*\{[^}]*solid var\(--red-hi\)/s)
  })

  it('keeps the mapped chrome scoped and free of external assets and animation', () => {
    const uncommented = generated.replace(/\/\*[\s\S]*?\*\//g, '')
    expect(uncommented).not.toMatch(/@import|@font-face|@keyframes|url\s*\(/)
    expect(uncommented).not.toMatch(/(?:^|\n)\s*:root\s*\{|(?:^|\n)\s*\*\s*\{|(?:^|\n)\s*\.window\s*\{/)
    expect(generated).not.toContain('.ans')
    expect(catalog).not.toMatch(/@import|@font-face|url\s*\(/)
  })
})
