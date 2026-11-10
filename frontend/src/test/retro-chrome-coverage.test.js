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

  it('keeps the mapped chrome scoped and free of external assets and animation', () => {
    const uncommented = generated.replace(/\/\*[\s\S]*?\*\//g, '')
    expect(uncommented).not.toMatch(/@import|@font-face|@keyframes|url\s*\(/)
    expect(uncommented).not.toMatch(/(?:^|\n)\s*:root\s*\{|(?:^|\n)\s*\*\s*\{|(?:^|\n)\s*\.window\s*\{/)
    expect(generated).not.toContain('.ans')
    expect(catalog).not.toMatch(/@import|@font-face|url\s*\(/)
  })
})
