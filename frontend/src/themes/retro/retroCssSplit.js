/**
 * Per-theme split of the generated Retro Design System CSS (issue #1119).
 *
 * build-retro-catalog.py writes three stylesheets in which every top-level
 * rule is scoped to exactly one [data-atlas-theme="<id>"]: the vendored
 * tokens, the catalog adapters, and the generated chrome. Instead of bundling
 * all three (about 300 kB for 53 themes), retroCssPlugin.js splits them here
 * into one stylesheet per theme ID, emitted as /assets/retro/<id>.css and
 * loaded on demand by retroThemeCss.js (and by public/theme-bootstrap.js for
 * the first paint).
 *
 * Pure string processing, shared by the Vite plugin, its tests, and nothing
 * at run time in the browser.
 */

// Order matters: tokens define the variables the catalog and chrome read.
export const RETRO_CSS_SOURCES = Object.freeze([
  'src/themes/vendor/retro-design-system/tokens.css',
  'src/themes/retroCatalog.generated.css',
  'src/themes/retroChrome.generated.css',
])

// Served by Vite in dev and by FastAPI's /assets mount in production.
export const RETRO_CSS_DIR = 'assets/retro'

export const RETRO_CSS_HEADER =
  '/* Atlas retro theme CSS, split at build time from the generated sheets; do not edit.\n' +
  ' * Retro Design System / NovusGFX, MIT, 8988f50 (src/themes/vendor/retro-design-system/LICENSE). */\n'

const THEME_SCOPE = /\[data-atlas-theme=(["']?)([a-z0-9-]+)\1\]/g

/** Split one stylesheet into its top-level rules ([selector, body]). */
export function topLevelRules(css) {
  const source = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const rules = []
  let i = 0
  while (i < source.length) {
    const open = source.indexOf('{', i)
    if (open === -1) {
      if (source.slice(i).trim()) throw new Error(`Unterminated CSS near: ${source.slice(i, i + 80)}`)
      break
    }
    const selector = source.slice(i, open).trim()
    let depth = 0
    let quote = null
    let j = open
    for (; j < source.length; j++) {
      const ch = source[j]
      if (quote) {
        if (ch === '\\') j++
        else if (ch === quote) quote = null
      } else if (ch === '"' || ch === "'") quote = ch
      else if (ch === '{') depth++
      else if (ch === '}' && --depth === 0) break
    }
    if (depth !== 0) throw new Error(`Unbalanced CSS block for selector: ${selector.slice(0, 80)}`)
    rules.push([selector, source.slice(open, j + 1)])
    i = j + 1
  }
  return rules
}

/** The single theme ID a top-level rule is scoped to, or an error. */
export function ruleThemeId(selector) {
  if (selector.startsWith('@')) throw new Error(`Unexpected at-rule in retro CSS: ${selector.slice(0, 80)}`)
  const ids = new Set([...selector.matchAll(THEME_SCOPE)].map(match => match[2]))
  if (ids.size !== 1) {
    throw new Error(`Retro CSS rule must be scoped to exactly one theme, got ${ids.size}: ${selector.slice(0, 120)}`)
  }
  // Every comma-separated selector must carry the scope, or the rule would
  // leak onto other themes once split out.
  for (const part of selector.split(',')) {
    if (!THEME_SCOPE.test(part)) throw new Error(`Unscoped selector in retro CSS: ${part.trim().slice(0, 120)}`)
    THEME_SCOPE.lastIndex = 0
  }
  return [...ids][0]
}

/**
 * Split the generated sheets (in RETRO_CSS_SOURCES order) into
 * { [themeId]: css }. Rules keep their source order within each theme.
 */
export function splitRetroCss(sheets) {
  const out = new Map()
  for (const css of sheets) {
    for (const [selector, body] of topLevelRules(css)) {
      const id = ruleThemeId(selector)
      if (!out.has(id)) out.set(id, [])
      out.get(id).push(`${selector} ${body}`)
    }
  }
  return Object.fromEntries([...out].map(([id, rules]) => [id, RETRO_CSS_HEADER + rules.join('\n') + '\n']))
}
