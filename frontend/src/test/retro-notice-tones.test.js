import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// "Cover the other pages" follow-up on PR #1175: status notices (composer
// warnings, toasts, error boxes, admin status pills, log levels) use opaque
// generated tints instead of translucent fixed-palette fills, and the routed
// pages use the theme-aware ramp instead of light-first / OS dark: classes.
// scripts/check-retro-browser.py measures the rendered result in all 55
// themes; these keep the mechanism in place.
const read = path => readFileSync(resolve(process.cwd(), path), 'utf8')
const generated = read('src/themes/baseThemes.generated.css') + read('src/themes/retroCatalog.generated.css')
const indexCss = read('src/index.css')
const TONES = ['danger', 'warning', 'alert', 'info', 'success', 'agent']

const triplet = value => value.trim().split(/\s+/).map(Number)
const lum = rgb => {
  const [r, g, b] = rgb.map(v => {
    const c = v / 255
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const ratio = (a, b) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

describe('generated status notice tints', () => {
  const blocks = [...generated.matchAll(/([^{}]+)\{([^{}]*--atlas-notice-[^{}]*)\}/g)]

  it('every theme (55) defines all six notice tones', () => {
    const themes = new Set(blocks.flatMap(([, selector]) =>
      [...selector.matchAll(/\[data-atlas-theme="([^"]+)"\]\s*(?:,|$)/g)].map(m => m[1])))
    expect(themes.size).toBe(55)
  })

  it('notice text reaches 4.5:1 on its own opaque tint everywhere it is defined', () => {
    let checked = 0
    for (const [, selector, body] of blocks) {
      for (const tone of TONES) {
        const bg = body.match(new RegExp(`--atlas-notice-${tone}-bg:\\s*([\\d ]+);`))
        const fg = body.match(new RegExp(`--atlas-notice-${tone}-fg:\\s*([\\d ]+);`))
        expect(bg && fg, `${selector.trim()} ${tone}`).toBeTruthy()
        expect(ratio(triplet(fg[1]), triplet(bg[1])), `${selector.trim()} ${tone}`).toBeGreaterThanOrEqual(4.5)
        checked += 1
      }
    }
    expect(checked).toBeGreaterThanOrEqual(55 * TONES.length)
  })

  it('the classes paint opaque rgb() fills, never a translucent tint', () => {
    for (const tone of TONES) {
      expect(indexCss).toContain(`.atlas-notice-${tone} {\r\n  background-color: rgb(var(--atlas-notice-${tone}-bg));`)
    }
  })
})

describe('composer warnings, toasts and routed pages', () => {
  it('composer warning rows, the agent-run strip and toasts use the notice tints', () => {
    expect(read('src/components/ComposerNotice.jsx')).toMatch(/atlas-notice-\$\{tone\}/)
    expect(read('src/components/AgentRunStatus.jsx')).toMatch(/atlas-notice-info/)
    expect(read('src/components/AgentModeNoToolsBanner.jsx')).toMatch(/atlas-notice-alert/)
    const toasts = read('src/components/ui/ToastProvider.jsx')
    expect(toasts).toMatch(/'atlas-notice-success'/)
    expect(toasts).toMatch(/'atlas-notice-danger'/)
    const chatArea = read('src/components/ChatArea.jsx')
    // Resting fills only; a phone hover tint on the icon-only Stop button stays.
    expect(chatArea).not.toMatch(/(?<![:\w-])bg-(yellow|red|blue|amber|green)-9\d\d\/[2-9]\d/)
    expect(chatArea).toMatch(/<ComposerNotice tone="warning"/)
  })

  it.each([
    'LogViewer', 'FilesPage', 'AllFilesView', 'SessionFilesView', 'TelemetryDashboard',
    'AdminDashboard', 'AdminModal', 'HelpPage', 'MarketplacePanel',
    'admin/BannerMessagesCard', 'admin/ConfigViewerCard', 'admin/FeedbackViewerCard',
    'admin/HelpConfigCard', 'admin/MCPConfigurationCard', 'admin/MCPDetailsView', 'admin/MCPServerManager',
  ])('%s: no OS dark: variants, light-first fills, translucent tints or raw gray-600/700 text', name => {
    const source = read(`src/components/${name}.jsx`)
    expect(source).not.toMatch(/\bdark:/)
    expect(source).not.toMatch(/\bbg-white\b/)
    expect(source).not.toMatch(/bg-(red|yellow|amber|orange|green|emerald|blue|sky|cyan|purple|pink)-9\d\d\/\d+/)
    expect(source).not.toMatch(/(?<![\w-])text-gray-[67]00\b/)
  })
})
