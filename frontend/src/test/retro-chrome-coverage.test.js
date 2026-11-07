import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { THEME_REGISTRY } from '../themes/themeRegistry'

const read = path => readFileSync(resolve(process.cwd(), 'src/themes', path), 'utf8')
const generated = read('retroChrome.generated.css')
const manual = read('retroChrome.css') + read('theme-presets.css')
const handReviewed = new Set([
  'mac7', 'win95', 'windows-xp-luna', 'aqua-osx',
  'amiga-workbench', 'nextstep', 'beos', 'crt-phosphor',
])
const targets = ['.atlas-retro-window', '.atlas-retro-window-titlebar',
  '.atlas-retro-button', '.atlas-retro-input']

describe('complete Retro Design System chrome coverage', () => {
  it('gives all 53 designs actual scoped window and control treatments', () => {
    const retro = THEME_REGISTRY.filter(entry => entry.reference)
    expect(retro).toHaveLength(53)
    expect(handReviewed.size).toBe(8)
    for (const entry of retro) {
      const source = handReviewed.has(entry.id) ? manual : generated
      for (const target of targets) {
        expect(
          source.includes(`[data-atlas-theme="${entry.id}"] ${target}`),
          `${entry.id} is missing ${target}`,
        ).toBe(true)
      }
    }
    expect((generated.match(/\/\* #\d{2} — /g) || [])).toHaveLength(45)
  })


  it('keeps System 7 window controls and the Settings caption faithfully scoped', () => {
    const css = read('theme-presets.css')
    expect(css).toMatch(/\[data-atlas-theme="mac7"\] \.atlas-retro-window-titlebar\s*\{[^}]*background-image: repeating-linear-gradient/s)
    expect(css).toMatch(/\[data-atlas-theme="mac7"\] \.atlas-retro-window-title\s*\{[^}]*font-size: 12px;/s)
    expect(css).toMatch(/\[data-atlas-theme="mac7"\] \.atlas-retro-window-close\s*\{[^}]*left: 7px;/s)
    expect(css).toMatch(/\[data-atlas-theme="mac7"\] \.atlas-theme-titlebar > button\[aria-label="Close tools and settings"\]\s*\{[^}]*left: 7px;/s)
    expect(css).toMatch(/\[data-atlas-theme="mac7"\] \.atlas-retro-input\s*\{[^}]*border-radius: 0;/s)
    expect(css).toContain('.atlas-theme-preview[data-preview="mac7"] .atlas-theme-preview-bar')
    expect(css).not.toMatch(/@font-face|@import|url\s*\(/)
  })

  it('keeps the extra design chrome scoped and free of external assets and animation', () => {
    for (const source of [generated, read('retroChrome.css')]) {
      expect(source).not.toMatch(/@import|@font-face|@keyframes|url\s*\(/)
      expect(source).not.toMatch(/\b(?:body|html)\s*\{|(?:^|\n)\s*\.window\s*\{/)
    }
    expect(generated).not.toContain('.ans')
  })
})
