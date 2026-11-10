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

const catalogAdapter = readFileSync(
  resolve(process.cwd(), 'src/themes/retroCatalog.generated.css'), 'utf8',
)
const manifest = JSON.parse(readFileSync(
  resolve(process.cwd(), 'src/themes/vendor/retro-design-system/manifest.json'), 'utf8',
))

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
  const source = ['win95','mac7','crt-phosphor'].includes(id) ? adapter : catalogAdapter
  const rule = source.match(new RegExp(`\\[data-atlas-theme="${id}"\\]\\s*\\{([^}]+)\\}`))
  if (!rule) throw new Error(`Missing adapter for ${id}`)
  const rgb = rule[1].match(new RegExp(`--gray-${weight}:\\s*(\\d+)\\s+(\\d+)\\s+(\\d+);`))
  if (!rgb) throw new Error(`Missing gray ${weight} for ${id}`)
  return rgb.slice(1).map(Number)
}

describe('Retro Design System attribution and CSS isolation', () => {
  it('keeps the original MIT notice and authentic scoped tokens', () => {
    expect(license).toContain('MIT License')
    expect(license).toContain('NovusGFX')
    expect(manifest.count).toBe(53)
    expect(THEME_REGISTRY.filter(t => t.reference)).toHaveLength(53)
    for (const entry of THEME_REGISTRY.filter(t => t.reference)) {
      expect(tokenStylesheet).toContain(`[data-atlas-theme="${entry.id}"]`)
      expect(tokenStylesheet).toContain(
        `Extracted from styles/${entry.reference}/index.html`,
      )
      expect(adapter + catalogAdapter).toContain(`[data-atlas-theme="${entry.id}"]`)
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

  it('derives content accents against their real conversation and control states', () => {
    const index = readFileSync('src/index.css', 'utf8')
    for (const { id, mode, reference } of THEME_REGISTRY) {
      const blocks = [...catalogAdapter.matchAll(new RegExp(`\\[data-atlas-theme="${id}"\\]\\s*\\{([^}]+)\\}`, 'g'))]
      const tokens = blocks.at(-1)[1]
      const gray = weight => reference ? themeGray(id, weight) :
        index.match(new RegExp(`\\[data-theme="${mode}"\\]\\s*\\{([^}]+)\\}`))[1]
          .match(new RegExp(`--gray-${weight}:\\s*(\\d+)\\s+(\\d+)\\s+(\\d+);`)).slice(1).map(Number)
      for (const [name, weight] of Object.entries({
        link: 800, 'syntax-keyword': 900, 'syntax-string': 900,
        'syntax-number': 900, 'syntax-comment': 900, 'header-menu-accent': 800,
        'header-accent': 700, 'header-hover-accent': 600,
      })) {
        const hex = tokens.match(new RegExp(`--atlas-${name}: #([0-9a-f]{6});`))[1]
        const fg = [0, 2, 4].map(n => parseInt(hex.slice(n, n+2), 16))
        expect(contrast(fg, gray(weight)), `${id} ${name}`).toBeGreaterThanOrEqual(4.5)
      }
      expect(contrast(gray(200), gray(700)), `${id} header control`).toBeGreaterThanOrEqual(4.5)
      expect(contrast(gray(50), gray(600)), `${id} disabled source`).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('includes styles for empty-token designs without inventing original source tokens', () => {
    for (const [id, sourceSlug] of [
      ['brutalist-web', '40-brutalist-web'],
      ['default-browser', '47-default-browser'],
    ]) {
      expect(THEME_REGISTRY.find(t => t.id === id)?.reference).toBe(sourceSlug)
      expect(tokenStylesheet).toContain(`[data-atlas-theme="${id}"]`)
      expect(catalogAdapter).toContain(`[data-atlas-theme="${id}"]`)
    }
  })

  it('does not import global demo styles or external proprietary assets', () => {
    expect(tokenStylesheet).not.toMatch(/\b:root\s*\{/)
    expect(tokenStylesheet).not.toMatch(/@import|@font-face|url\s*\(/)
    expect(adapter).not.toMatch(/@import|@font-face|url\s*\(/)
    expect(catalogAdapter).not.toMatch(/@import|@font-face|url\s*\(/)
  })

  it('themes chat accents and Settings footer from mapped demo chrome, not invariant blue', () => {
    const index = readFileSync('src/index.css', 'utf8')
    const settings = readFileSync('src/components/SettingsPanel.jsx', 'utf8')
    const chat = readFileSync('src/components/ChatArea.jsx', 'utf8')
    expect(index).toContain('var(--atlas-user-bubble-bg')
    expect(settings).toContain('atlas-theme-backdrop')
    expect(settings).toMatch(/<RetroButton[\s\S]*Cancel[\s\S]*<RetroButton[\s\S]*Save Changes/)
    expect(chat).toContain('atlas-theme-accent-fill')
    expect(index).toContain('.atlas-status-red')
  })
})
