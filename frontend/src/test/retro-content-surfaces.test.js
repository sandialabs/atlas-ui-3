import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// Structural guards for the browser contrast fixes in issue #1119 review:
// the real-component check (scripts/check-retro-browser.py) measures the
// result; these keep the mechanisms from being removed by accident.
const read = path => readFileSync(resolve(process.cwd(), path), 'utf8')
const presets = read('src/themes/theme-presets.css')
const catalog = read('src/themes/retroCatalog.generated.css')
const message = read('src/components/Message.jsx')
const chatArea = read('src/components/ChatArea.jsx')

const RETRO = ':root[data-atlas-theme]:not([data-atlas-theme="dark"]):not([data-atlas-theme="light"])'
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

describe('retro transcript and window content surfaces', () => {
  it('backs compact transcript rows and the composer with a panel plate in retro themes only', () => {
    expect(message).toMatch(/className="atlas-transcript-row min-w-0"/)
    expect(chatArea).toMatch(/atlas-composer-plate/)
    expect(presets).toContain(`${RETRO} .atlas-transcript-row {\n  background-color: rgb(var(--gray-800));`)
    expect(presets).toContain(`${RETRO} .atlas-composer-plate {\n  background-color: rgb(var(--gray-800));`)
  })

  it('re-keys the gray ramp inside off-key windows and header strips', () => {
    for (const [id, part] of [['memphis', 'Window'], ['gameboy-dmg', 'Window'], ['op-art', 'Window'],
      ['dos-cga', 'Header'], ['midnight-commander', 'Header']]) {
      const block = catalog.match(new RegExp(
        `/\\* ${part} content ramp: ${id} [^*]*\\*/\\n([^{]+)\\{([^}]*)\\}`))
      expect(block, `${id} ${part}`).not.toBeNull()
      const vars = Object.fromEntries([...block[2].matchAll(/--gray-(\d+):\s*([\d ]+);/g)]
        .map(([, step, value]) => [step, triplet(value)]))
      for (const text of ['50', '100', '200', '300', '400', '500']) {
        for (const surface of ['900', '800', '700']) {
          expect(ratio(vars[text], vars[surface]), `${id} gray-${text} on ${surface}`)
            .toBeGreaterThanOrEqual(4.5)
        }
      }
    }
  })

  it('keeps fixed-palette avatar and status fills paired with fixed AA text', () => {
    expect(message).toMatch(/bg-green-700 text-white/)
    expect(message).toMatch(/bg-yellow-600 text-black/)
    expect(message).not.toMatch(/hover:text-white/)
  })
})
