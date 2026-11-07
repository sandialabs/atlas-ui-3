import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { THEME_REGISTRY } from '../themes/themeRegistry'

const tokenStylesheet = readFileSync(
  resolve(process.cwd(), 'src/themes/vendor/retro-design-system/tokens.css'), 'utf8',
)
const license = readFileSync(
  resolve(process.cwd(), 'src/themes/vendor/retro-design-system/LICENSE'), 'utf8',
)
const adapter = readFileSync(
  resolve(process.cwd(), 'src/themes/theme-presets.css'), 'utf8',
)

describe('Retro Design System attribution and CSS isolation', () => {
  it('keeps the original MIT notice and authentic scoped tokens', () => {
    expect(license).toContain('MIT License')
    expect(license).toContain('NovusGFX')
    for (const entry of THEME_REGISTRY.filter(t => t.reference)) {
      expect(tokenStylesheet).toContain(`[data-atlas-theme="${entry.id}"]`)
      expect(tokenStylesheet).toContain(
        `Extracted from styles/${entry.reference}/index.html`,
      )
      expect(adapter).toContain(`[data-atlas-theme="${entry.id}"]`)
    }
    expect(tokenStylesheet).toContain('--cyan-hi: #55ffff;')
    expect(tokenStylesheet).toContain('--title: #000080;')
    expect(tokenStylesheet).toContain('--font-sys: "Chicago"')
  })

  it('does not import global demo styles or external proprietary assets', () => {
    expect(tokenStylesheet).not.toMatch(/\b:root\s*\{/)
    expect(tokenStylesheet).not.toMatch(/@import|@font-face|url\s*\(/)
    expect(adapter).not.toMatch(/@import|@font-face|url\s*\(/)
  })
})
