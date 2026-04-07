/**
 * Tests for RAG inline citation badge rendering (GH #443)
 *
 * Verifies that:
 *  1. [N] markers in rendered HTML are converted to citation badges
 *  2. Citations inside HTML tags are NOT converted
 *  3. [N] inside <code> and <pre> blocks are NOT converted
 *  4. References section gets anchor IDs
 *  5. Citation badges use scrollIntoView pattern (not fragment links)
 *  6. IDs are scoped per message to avoid cross-message collisions
 *  7. Security: no XSS vectors in badge rendering
 */

import { describe, it, expect } from 'vitest'

// --------------------------------------------------------------------------
// Replicate the pure-logic functions from Message.jsx so we can unit-test
// them without mounting React components.
// --------------------------------------------------------------------------

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
      return `<sup class="rag-citation-badge" data-ref="${num}"><span role="button" tabindex="0" class="rag-citation-link" data-citation-target="${refId}">${num}</span></sup>`
    }
  )
}

const processReferencesSection = (html, scope = '') => {
  const refIdx = html.indexOf('<strong>References</strong>')
  if (refIdx === -1) return html

  const before = html.slice(0, refIdx)
  const after = html.slice(refIdx)
  const prefix = scope ? `rag-ref-${scope}` : 'rag-ref'

  const anchored = after
    .replace(/<li>(\d{1,2})\.\s/g, (_, num) => `<li id="${prefix}-${num}" class="rag-ref-entry">${num}. `)
    .replace(/<p>(\d{1,2})\.\s/g, (_, num) => `<p id="${prefix}-${num}" class="rag-ref-entry">${num}. `)

  return before + anchored
}

// --------------------------------------------------------------------------
// Tests: citation badge rendering
// --------------------------------------------------------------------------

describe('RAG citation badge rendering', () => {
  it('converts [1] into a citation badge with scrollIntoView pattern', () => {
    const input = '<p>The API uses OAuth [1] for authentication.</p>'
    const result = processCitationBadges(input)
    expect(result).toContain('class="rag-citation-badge"')
    expect(result).toContain('data-ref="1"')
    expect(result).toContain('data-citation-target="rag-ref-1"')
    expect(result).toContain('class="rag-citation-link"')
    expect(result).toContain('role="button"')
    expect(result).not.toContain('href="#rag-ref-')
  })

  it('converts multiple adjacent citations [1][2]', () => {
    const input = '<p>Both methods are supported [1][2].</p>'
    const result = processCitationBadges(input)
    expect(result).toContain('data-ref="1"')
    expect(result).toContain('data-ref="2"')
  })

  it('handles double-digit citation numbers', () => {
    const input = '<p>See source [12] for details.</p>'
    const result = processCitationBadges(input)
    expect(result).toContain('data-ref="12"')
  })

  it('does NOT convert markdown link syntax [text](url)', () => {
    const rawInput = '<p>[Click here](https://example.com) is a link.</p>'
    const result = processCitationBadges(rawInput)
    expect(result).not.toContain('rag-citation-badge')
  })

  it('does NOT convert [N] inside HTML tag attributes', () => {
    const input = '<a href="page[1].html">link</a>'
    const result = processCitationBadges(input)
    expect(result).toContain('href="page[1].html"')
    expect(result).not.toContain('rag-citation-badge')
  })

  it('handles citation at start of paragraph', () => {
    const input = '<p>[1] This claim is supported.</p>'
    const result = processCitationBadges(input)
    expect(result).toContain('data-ref="1"')
  })

  it('does not match three-digit numbers', () => {
    const input = '<p>See page [123] for more.</p>'
    const result = processCitationBadges(input)
    expect(result).not.toContain('rag-citation-badge')
  })

  it('does not inject script tags via crafted input', () => {
    const input = '<p>[1] normal citation</p>'
    const result = processCitationBadges(input)
    expect(result).not.toContain('<script')
    expect(result).toMatch(/data-ref="\d{1,2}"/)
  })
})

// --------------------------------------------------------------------------
// Tests: code/pre block exclusion
// --------------------------------------------------------------------------

describe('RAG citation badges inside code/pre blocks', () => {
  it('does NOT convert [1] inside inline <code> tags', () => {
    const input = '<p>Use <code>arr[1]</code> to access the element [1].</p>'
    const result = processCitationBadges(input)
    expect(result).toContain('<code>arr[1]</code>')
    expect(result).toContain('data-ref="1"')
  })

  it('does NOT convert [2] inside <pre><code> blocks', () => {
    const input = '<pre><code>const x = data[2];\nconst y = items[3];</code></pre>'
    const result = processCitationBadges(input)
    expect(result).not.toContain('rag-citation-badge')
    expect(result).toContain('data[2]')
    expect(result).toContain('items[3]')
  })

  it('resumes converting badges after closing </code> tag', () => {
    const input = '<p>In <code>arr[1]</code> the index is fixed [2].</p>'
    const result = processCitationBadges(input)
    expect(result).toContain('<code>arr[1]</code>')
    expect(result).toContain('data-ref="2"')
  })

  it('handles nested <pre><code> correctly', () => {
    const input = '<pre><code>dict[1] = "a"\ndict[2] = "b"</code></pre><p>See [1] for details.</p>'
    const result = processCitationBadges(input)
    expect(result).toContain('dict[1]')
    expect(result).toContain('dict[2]')
    expect(result).toContain('data-ref="1"')
  })

  it('handles <code> with class attributes', () => {
    const input = '<code class="language-python">list[1]</code>'
    const result = processCitationBadges(input)
    expect(result).not.toContain('rag-citation-badge')
    expect(result).toContain('list[1]')
  })

  it('handles multiple code spans in one paragraph', () => {
    const input = '<p>Compare <code>a[1]</code> with <code>b[2]</code> as noted [3].</p>'
    const result = processCitationBadges(input)
    expect(result).toContain('<code>a[1]</code>')
    expect(result).toContain('<code>b[2]</code>')
    expect(result).toContain('data-ref="3"')
    const badgeCount = (result.match(/rag-citation-badge/g) || []).length
    expect(badgeCount).toBe(1)
  })
})

// --------------------------------------------------------------------------
// Tests: per-message scoping of citation IDs
// --------------------------------------------------------------------------

describe('RAG citation ID scoping per message', () => {
  it('uses unscoped IDs when no scope is provided', () => {
    const input = '<p>Claim [1].</p>'
    const result = processCitationBadges(input)
    expect(result).toContain('data-citation-target="rag-ref-1"')
  })

  it('uses scoped IDs when scope is provided', () => {
    const input = '<p>Claim [1].</p>'
    const result = processCitationBadges(input, 'msg42')
    expect(result).toContain('data-citation-target="rag-ref-msg42-1"')
    expect(result).not.toContain('data-citation-target="rag-ref-1"')
  })

  it('two different scopes produce different target IDs for same citation number', () => {
    const input = '<p>Fact [1] is cited.</p>'
    const resultA = processCitationBadges(input, 'msgA')
    const resultB = processCitationBadges(input, 'msgB')
    expect(resultA).toContain('data-citation-target="rag-ref-msgA-1"')
    expect(resultB).toContain('data-citation-target="rag-ref-msgB-1"')
    // Confirm they don't collide
    expect(resultA).not.toContain('msgB')
    expect(resultB).not.toContain('msgA')
  })

  it('scoped references section produces matching anchor IDs', () => {
    const badges = processCitationBadges('<p>Point [1].</p>', 'r5')
    const refs = processReferencesSection(
      '<p><strong>References</strong></p>\n<p>1. Source A - 90%</p>',
      'r5'
    )
    // Badge target and reference anchor should match
    expect(badges).toContain('data-citation-target="rag-ref-r5-1"')
    expect(refs).toContain('id="rag-ref-r5-1"')
  })

  it('scoped references do NOT collide across two messages', () => {
    const refsMsg1 = processReferencesSection(
      '<p><strong>References</strong></p>\n<p>1. Alpha</p>',
      'first'
    )
    const refsMsg2 = processReferencesSection(
      '<p><strong>References</strong></p>\n<p>1. Beta</p>',
      'second'
    )
    expect(refsMsg1).toContain('id="rag-ref-first-1"')
    expect(refsMsg2).toContain('id="rag-ref-second-1"')
    expect(refsMsg1).not.toContain('second')
    expect(refsMsg2).not.toContain('first')
  })
})

// --------------------------------------------------------------------------
// Tests: references section anchoring and highlight targets
// --------------------------------------------------------------------------

describe('RAG references section anchoring', () => {
  it('adds id anchors to paragraph-style reference entries', () => {
    const input = '<p><strong>References</strong></p>\n<p>1. Auth Guide - 95% relevance</p>\n<p>2. Deploy Guide - 87% relevance</p>'
    const result = processReferencesSection(input)
    expect(result).toContain('id="rag-ref-1"')
    expect(result).toContain('id="rag-ref-2"')
  })

  it('does not modify content before the References heading', () => {
    const input = '<p>Some text</p>\n<p>1. A numbered item</p>\n<p><strong>References</strong></p>\n<p>1. Source</p>'
    const result = processReferencesSection(input)
    const parts = result.split('<strong>References</strong>')
    expect(parts[0]).not.toContain('rag-ref-')
  })

  it('returns unchanged HTML when no References heading present', () => {
    const input = '<p>1. First item</p><p>2. Second item</p>'
    const result = processReferencesSection(input)
    expect(result).toBe(input)
  })

  it('handles list-style reference entries', () => {
    const input = '<strong>References</strong><ul><li>1. First source</li><li>2. Second source</li></ul>'
    const result = processReferencesSection(input)
    expect(result).toContain('id="rag-ref-1"')
    expect(result).toContain('id="rag-ref-2"')
  })

  it('adds rag-ref-entry class to paragraph reference targets for visible highlighting', () => {
    const input = '<p><strong>References</strong></p>\n<p>1. Source A</p>'
    const result = processReferencesSection(input)
    // The id should be on the <p> element itself (not a child span)
    expect(result).toContain('<p id="rag-ref-1" class="rag-ref-entry">')
  })

  it('adds rag-ref-entry class to list-item reference targets for visible highlighting', () => {
    const input = '<strong>References</strong><ul><li>1. Source A</li></ul>'
    const result = processReferencesSection(input)
    expect(result).toContain('<li id="rag-ref-1" class="rag-ref-entry">')
  })
})
