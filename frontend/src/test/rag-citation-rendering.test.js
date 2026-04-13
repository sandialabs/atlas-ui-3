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
  const numberedLinkPattern = /(\d{1,2})\.\s+<a[^>]*href="([^"]*)"[^>]*>([^<]+)<\/a>/g
  const liLinkPattern = /<li><a[^>]*href="([^"]*)"[^>]*>([^<]+)<\/a>/g
  const numberedPlainPattern = /(\d{1,2})\.\s+([^<—\n]+)/g
  const liPlainPattern = /<li>([^<—\n]+)/g
  let m, idx
  while ((m = numberedLinkPattern.exec(refsHtml)) !== null) {
    labels.set(m[1], { label: m[3].trim(), url: m[2] })
  }
  while ((m = numberedPlainPattern.exec(refsHtml)) !== null) {
    if (!labels.has(m[1])) {
      labels.set(m[1], { label: m[2].trim(), url: null })
    }
  }
  if (labels.size === 0) {
    idx = 1
    while ((m = liLinkPattern.exec(refsHtml)) !== null) {
      labels.set(String(idx++), { label: m[2].trim(), url: m[1] })
    }
    if (labels.size === 0) {
      idx = 1
      while ((m = liPlainPattern.exec(refsHtml)) !== null) {
        labels.set(String(idx++), { label: m[1].trim(), url: null })
      }
    }
  }
  return labels
}

const processCitationBadges = (html, scope = '') => {
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
      return `<span class="rag-source-chip" data-ref="${num}"><span role="button" tabindex="0" aria-label="Citation ${num}" class="rag-source-chip-inner rag-source-chip-numonly" data-citation-target="${refId}">${num}</span></span>`
    }
  )
}

const processReferencesSection = (html, scope = '', sourceLabels = new Map()) => {
  const refIdx = html.indexOf('<strong>References</strong>')
  if (refIdx === -1) return html
  const before = html.slice(0, refIdx)
  let after = html.slice(refIdx)
  const prefix = scope ? `rag-ref-${scope}` : 'rag-ref'
  let liCounter = 0
  const anchored = after
    .replace(/<li>(\d{1,2})\.\s/g, (_, num) => `<li id="${prefix}-${num}" class="rag-ref-entry">${num}. `)
    .replace(/<p>(\d{1,2})\.\s/g, (_, num) => `<p id="${prefix}-${num}" class="rag-ref-entry">${num}. `)
    .replace(/<li>(?!\d{1,2}\.\s)/g, () => {
      liCounter++
      return `<li id="${prefix}-${liCounter}" class="rag-ref-entry">`
    })
  const summaryParts = []
  const sorted = [...sourceLabels.entries()].sort((a, b) => Number(a[0]) - Number(b[0]))
  for (const [num, src] of sorted) {
    summaryParts.push(`<span class="rag-summary-ref">[${num}]</span> ${src.label}`)
  }
  const summaryText = summaryParts.length > 0
    ? summaryParts.join('<span class="rag-summary-sep">,</span> ')
    : 'Sources'
  const wrapped = anchored
    .replace(
      /(<p>)?<strong>References<\/strong>(<\/p>)?/,
      `<details class="rag-references-collapse"><summary class="rag-references-summary" aria-label="References: ${summaryParts.length} sources">${summaryText}</summary>`
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

  it('extracts labels from ol/li linked references', () => {
    const html = '<p><strong>References</strong></p><ol><li><a href="https://a.com">Alpha Source</a> — 95%</li><li><a href="https://b.com">Beta Source</a> — 80%</li></ol>'
    const labels = extractSourceLabels(html)
    expect(labels.size).toBe(2)
    expect(labels.get('1')).toEqual({ label: 'Alpha Source', url: 'https://a.com' })
    expect(labels.get('2')).toEqual({ label: 'Beta Source', url: 'https://b.com' })
  })

  it('extracts multiple sources from paragraph format', () => {
    const html = '<p><strong>References</strong></p><p>1. <a href="https://a.com">Alpha</a> — 95%</p><p>2. Beta — 80%</p>'
    const labels = extractSourceLabels(html)
    expect(labels.size).toBe(2)
    expect(labels.get('1').label).toBe('Alpha')
    expect(labels.get('2').label).toBe('Beta')
  })
})

// --------------------------------------------------------------------------
// Tests: source chip rendering
// --------------------------------------------------------------------------

describe('RAG source chip rendering', () => {
  it('converts [1] into a compact number-only citation chip', () => {
    const labels = new Map([['1', { label: 'Eater NM', url: 'https://eater.com/nm' }]])
    const input = '<p>Green chile is iconic [1].</p>'
    const result = processCitationBadges(input, '', labels)
    expect(result).toContain('class="rag-source-chip"')
    expect(result).toContain('rag-source-chip-numonly')
    expect(result).toContain('>1<')
    // Source name should NOT appear inline — it belongs in the Sources footer
    expect(result).not.toContain('eater.com')
  })

  it('falls back to number when no source label available', () => {
    const input = '<p>Fact [1].</p>'
    const result = processCitationBadges(input)
    expect(result).toContain('rag-source-chip')
    expect(result).toContain('data-ref="1"')
  })

  it('converts multiple adjacent citations [1][2] as number-only chips', () => {
    const labels = new Map([
      ['1', { label: 'Eater NM', url: null }],
      ['2', { label: 'Food Network', url: null }],
    ])
    const input = '<p>Both sources agree [1][2].</p>'
    const result = processCitationBadges(input, '', labels)
    expect(result).toContain('data-ref="1"')
    expect(result).toContain('data-ref="2"')
    // No source labels inline
    expect(result).not.toContain('eaternm')
    expect(result).not.toContain('foodnetwork')
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
  it('shows compact [N] Name summary when source labels provided', () => {
    const labels = new Map([
      ['1', { label: 'Auth Guide', url: null }],
      ['2', { label: 'Deploy Guide', url: null }],
    ])
    const input = '<p><strong>References</strong></p>\n<p>1. Auth Guide</p>\n<p>2. Deploy Guide</p>'
    const result = processReferencesSection(input, '', labels)
    expect(result).toContain('<details class="rag-references-collapse">')
    expect(result).toContain('[1]')
    expect(result).toContain('Auth Guide')
    expect(result).toContain('[2]')
    expect(result).toContain('Deploy Guide')
    expect(result).toContain('rag-summary-ref')
  })

  it('falls back to "Sources" when no labels provided', () => {
    const input = '<p><strong>References</strong></p>\n<p>1. Source A</p>'
    const result = processReferencesSection(input)
    expect(result).toContain('>Sources</summary>')
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
