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

// WCAG relative luminance: check the dim utility text on themed panel surfaces.
function luminance([r, g, b]) {
  const linear = [r, g, b].map(v => {
    const x = v / 255
    return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
}

function contrast(a, b) {
  const x = luminance(a)
  const y = luminance(b)
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

function themeGray(id, weight) {
  const rule = adapter.match(new RegExp(`\\[data-atlas-theme="${id}"\\]\\s*\\{([^}]+)\\}`))
  if (!rule) throw new Error(`Missing adapter for ${id}`)
  const rgb = rule[1].match(new RegExp(`--gray-${weight}:\\s*(\\d+)\\s+(\\d+)\\s+(\\d+);`))
  if (!rgb) throw new Error(`Missing gray ${weight} for ${id}`)
  return rgb.slice(1).map(Number)
}

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
    expect(tokenStylesheet).toContain('--ink: #33ff33;')
    expect(tokenStylesheet).toContain('--title: #000080;')
    expect(tokenStylesheet).toContain('--font-sys: "Chicago"')
  })

  it('keeps small dim text legible against themed panel surfaces (WCAG AA)', () => {
    for (const { id } of THEME_REGISTRY.filter(t => t.reference)) {
      const panel = themeGray(id, 800)
      for (const textToken of [400, 500]) {
        const ratio = contrast(themeGray(id, textToken), panel)
        expect(ratio, `${id} gray-${textToken}: ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5)
      }
    }
  })

  it('does not import global demo styles or external proprietary assets', () => {
    expect(tokenStylesheet).not.toMatch(/\b:root\s*\{/)
    expect(tokenStylesheet).not.toMatch(/@import|@font-face|url\s*\(/)
    expect(adapter).not.toMatch(/@import|@font-face|url\s*\(/)
  })
})
