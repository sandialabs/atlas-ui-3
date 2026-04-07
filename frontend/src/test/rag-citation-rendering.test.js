/**
 * Tests for RAG inline source chip rendering (GH #443)
 *
 * Verifies that:
 *  1. [N] markers in rendered HTML are converted to source chips
 *  2. Citations inside HTML tags and code/pre blocks are NOT converted
 *  3. References section gets anchor IDs and is collapsible
 *  4. Source labels are extracted and shown in chips
 *  5. IDs are scoped per message to avoid cross-message collisions
 */

import { describe, it, expect } from 'vitest'

// --------------------------------------------------------------------------
// Replicate the pure-logic functions from Message.jsx
// --------------------------------------------------------------------------

const extractSourceLabels = (html) => {
  const labels = new Map()
  const refIdx = html.indexOf('<strong>References</strong>')
  if (refIdx === -1) return labels
  const refsHtml = html.slice(refIdx)
  const linkPattern = /(\d{1,2})\.\s+<a[^>]*href="([^"]*)"[^>]*>([^<]+)<\/a>/g
  const plainPattern = /(\d{1,2})\.\s+([^<—\n]+)/g
  let m
  while ((m = linkPattern.exec(refsHtml)) !== null) {
    labels.set(m[1], { label: m[3].trim(), url: m[2] })
  }
  while ((m = plainPattern.exec(refsHtml)) !== null) {
    if (!labels.has(m[1])) {
      labels.set(m[1], { label: m[2].trim(), url: null })
    }
  }
  return labels
}

const chipLabel = (label, url) => {
  if (url) {
    try {
      const domain = new URL(url).hostname.replace(/^www\./, '')
      if (domain.length <= 25) return domain
    } catch { /* fall through */ }
  }
  const slug = label.toLowerCase().replace(/[^a-z0-9]+/g, '').slice(0, 20)
  return slug || label.slice(0, 20)
}

const processCitationBadges = (html, scope = '', sourceLabels = new Map()) => {
  let insideCode = 0
  return html.replace(
    /(<\/?(?:code|pre)[^>]*>)|(<[^>]*>)|(?<!\]\()(\[(\d{1,2})\])(?!\()/gi,
    (match, codeTag, otherTag, bracket, num) => {
      if (codeTag) {
        if (codeTag[1] === '/') {
          insideCode = Math.max(0, insideCode - 1)
        } else {
          insideCode++
        }
        return codeTag
      }
      if (otherTag) return otherTag
      if (insideCode > 0) return match
      const refId = scope ? `rag-ref-${scope}-${num}` : `rag-ref-${num}`
      const src = sourceLabels.get(num)
      const displayLabel = src ? chipLabel(src.label, src.url) : num
      return `<span class="rag-source-chip" data-ref="${num}"><span role="button" tabindex="0" class="rag-source-chip-inner" data-citation-target="${refId}">${displayLabel}<span class="rag-source-chip-num">${num}</span></span></span>`
    }
  )
}

const processReferencesSection = (html, scope = '') => {
  const refIdx = html.indexOf('<strong>References</strong>')
  if (refIdx === -1) return html
  const before = html.slice(0, refIdx)
  let after = html.slice(refIdx)
  const prefix = scope ? `rag-ref-${scope}` : 'rag-ref'
  const anchored = after
    .replace(/<li>(\d{1,2})\.\s/g, (_, num) => `<li id="${prefix}-${num}" class="rag-ref-entry">${num}. `)
    .replace(/<p>(\d{1,2})\.\s/g, (_, num) => `<p id="${prefix}-${num}" class="rag-ref-entry">${num}. `)
  const wrapped = anchored
    .replace(
      /(<p>)?<strong>References<\/strong>(<\/p>)?/,
      '<details class="rag-references-collapse"><summary class="rag-references-summary">Sources</summary>'
    ) + '</details>'
  return before + wrapped
}

// --------------------------------------------------------------------------
// Tests: source label extraction
// --------------------------------------------------------------------------

describe('extractSourceLabels', () => {
  it('extracts labels from linked references', () => {
    const html = '<p><strong>References</strong></p><p>1. <a href="https://example.com">Eater NM</a> — 95%</p>'
    const labels = extractSourceLabels(html)
    expect(labels.get('1')).toEqual({ label: 'Eater NM', url: 'https://example.com' })
  })

  it('extracts labels from plain text references', () => {
    const html = '<p><strong>References</strong></p><p>1. New Mexico Magazine — 90%</p>'
    const labels = extractSourceLabels(html)
    expect(labels.get('1').label).toBe('New Mexico Magazine')
  })

  it('returns empty map when no References section', () => {
    const html = '<p>Just some text.</p>'
    const labels = extractSourceLabels(html)
    expect(labels.size).toBe(0)
  })

  it('extracts multiple sources', () => {
    const html = '<p><strong>References</strong></p><p>1. <a href="https://a.com">Alpha</a> — 95%</p><p>2. Beta — 80%</p>'
    const labels = extractSourceLabels(html)
    expect(labels.size).toBe(2)
    expect(labels.get('1').label).toBe('Alpha')
    expect(labels.get('2').label).toBe('Beta')
  })
})

// --------------------------------------------------------------------------
// Tests: chipLabel
// --------------------------------------------------------------------------

describe('chipLabel', () => {
  it('extracts domain from URL when available', () => {
    expect(chipLabel('Eater NM', 'https://www.eater.com/food')).toBe('eater.com')
  })

  it('strips www. prefix from domain', () => {
    expect(chipLabel('Anything', 'https://www.example.org/page')).toBe('example.org')
  })

  it('produces lowercase slug from label when no URL', () => {
    expect(chipLabel('New Mexico Magazine')).toBe('newmexicomagazine')
  })

  it('truncates long slugs at 20 chars', () => {
    const result = chipLabel('A Very Long Source Name That Exceeds The Limit')
    expect(result.length).toBeLessThanOrEqual(20)
  })

  it('falls back to label text when URL is invalid', () => {
    expect(chipLabel('eater', 'not-a-url')).toBe('eater')
  })
})

// --------------------------------------------------------------------------
// Tests: source chip rendering
// --------------------------------------------------------------------------

describe('RAG source chip rendering', () => {
  it('converts [1] into a source chip with domain name', () => {
    const labels = new Map([['1', { label: 'Eater NM', url: 'https://eater.com/nm' }]])
    const input = '<p>Green chile is iconic [1].</p>'
    const result = processCitationBadges(input, '', labels)
    expect(result).toContain('class="rag-source-chip"')
    expect(result).toContain('class="rag-source-chip-inner"')
    expect(result).toContain('eater.com') // domain extracted from URL
    expect(result).toContain('class="rag-source-chip-num"')
    expect(result).toContain('>1<')
  })

  it('falls back to number when no source label available', () => {
    const input = '<p>Fact [1].</p>'
    const result = processCitationBadges(input)
    expect(result).toContain('rag-source-chip')
    expect(result).toContain('data-ref="1"')
  })

  it('converts multiple adjacent citations [1][2] with slug labels', () => {
    const labels = new Map([
      ['1', { label: 'Eater NM', url: null }],
      ['2', { label: 'Food Network', url: null }],
    ])
    const input = '<p>Both sources agree [1][2].</p>'
    const result = processCitationBadges(input, '', labels)
    expect(result).toContain('eaternm') // slug from label
    expect(result).toContain('foodnetwork')
  })

  it('does NOT convert [N] inside HTML tag attributes', () => {
    const input = '<a href="page[1].html">link</a>'
    const result = processCitationBadges(input)
    expect(result).toContain('href="page[1].html"')
    expect(result).not.toContain('rag-source-chip')
  })

  it('does not match three-digit numbers', () => {
    const input = '<p>See page [123] for more.</p>'
    const result = processCitationBadges(input)
    expect(result).not.toContain('rag-source-chip')
  })
})

// --------------------------------------------------------------------------
// Tests: code/pre block exclusion
// --------------------------------------------------------------------------

describe('RAG source chips inside code/pre blocks', () => {
  it('does NOT convert [1] inside inline <code> tags', () => {
    const input = '<p>Use <code>arr[1]</code> to access [1].</p>'
    const result = processCitationBadges(input)
    expect(result).toContain('<code>arr[1]</code>')
    expect(result).toContain('data-ref="1"')
  })

  it('does NOT convert inside <pre><code> blocks', () => {
    const input = '<pre><code>data[2]</code></pre>'
    const result = processCitationBadges(input)
    expect(result).not.toContain('rag-source-chip')
    expect(result).toContain('data[2]')
  })

  it('resumes converting after closing </code>', () => {
    const input = '<p><code>arr[1]</code> see [2].</p>'
    const result = processCitationBadges(input)
    expect(result).toContain('<code>arr[1]</code>')
    expect(result).toContain('data-ref="2"')
  })

  it('handles multiple code spans in one paragraph', () => {
    const input = '<p>Compare <code>a[1]</code> with <code>b[2]</code> as noted [3].</p>'
    const result = processCitationBadges(input)
    expect(result).toContain('<code>a[1]</code>')
    expect(result).toContain('<code>b[2]</code>')
    expect(result).toContain('data-ref="3"')
    const chipCount = (result.match(/rag-source-chip"/g) || []).length
    expect(chipCount).toBe(1)
  })
})

// --------------------------------------------------------------------------
// Tests: per-message scoping
// --------------------------------------------------------------------------

describe('RAG citation ID scoping per message', () => {
  it('uses scoped IDs when scope is provided', () => {
    const input = '<p>Claim [1].</p>'
    const result = processCitationBadges(input, 'msg42')
    expect(result).toContain('data-citation-target="rag-ref-msg42-1"')
  })

  it('two different scopes produce different target IDs', () => {
    const input = '<p>Fact [1].</p>'
    const resultA = processCitationBadges(input, 'msgA')
    const resultB = processCitationBadges(input, 'msgB')
    expect(resultA).toContain('rag-ref-msgA-1')
    expect(resultB).toContain('rag-ref-msgB-1')
    expect(resultA).not.toContain('msgB')
  })

  it('scoped badges match scoped reference anchors', () => {
    const badges = processCitationBadges('<p>[1].</p>', 'r5')
    const refs = processReferencesSection(
      '<p><strong>References</strong></p>\n<p>1. Source A</p>', 'r5'
    )
    expect(badges).toContain('data-citation-target="rag-ref-r5-1"')
    expect(refs).toContain('id="rag-ref-r5-1"')
  })

  it('different message scopes do NOT collide', () => {
    const refs1 = processReferencesSection(
      '<p><strong>References</strong></p>\n<p>1. Alpha</p>', 'first'
    )
    const refs2 = processReferencesSection(
      '<p><strong>References</strong></p>\n<p>1. Beta</p>', 'second'
    )
    expect(refs1).toContain('id="rag-ref-first-1"')
    expect(refs2).toContain('id="rag-ref-second-1"')
    expect(refs1).not.toContain('second')
  })
})

// --------------------------------------------------------------------------
// Tests: collapsible references section
// --------------------------------------------------------------------------

describe('RAG references section', () => {
  it('wraps references in a collapsible details element', () => {
    const input = '<p><strong>References</strong></p>\n<p>1. Source A</p>'
    const result = processReferencesSection(input)
    expect(result).toContain('<details class="rag-references-collapse">')
    expect(result).toContain('<summary class="rag-references-summary">Sources</summary>')
    expect(result).toContain('</details>')
  })

  it('adds anchor IDs and rag-ref-entry class to reference entries', () => {
    const input = '<p><strong>References</strong></p>\n<p>1. Source A</p>'
    const result = processReferencesSection(input)
    expect(result).toContain('id="rag-ref-1"')
    expect(result).toContain('class="rag-ref-entry"')
  })

  it('does not modify content before the References heading', () => {
    const input = '<p>Some text</p>\n<p>1. Item</p>\n<p><strong>References</strong></p>\n<p>1. Source</p>'
    const result = processReferencesSection(input)
    const parts = result.split('rag-references-collapse')
    expect(parts[0]).not.toContain('rag-ref-')
  })

  it('returns unchanged HTML when no References heading present', () => {
    const input = '<p>1. First item</p><p>2. Second item</p>'
    const result = processReferencesSection(input)
    expect(result).toBe(input)
  })
})
