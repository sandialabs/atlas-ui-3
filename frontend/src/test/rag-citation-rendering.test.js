/**
 * Tests for RAG inline citation badge rendering (GH #443)
 *
 * Verifies that:
 *  1. [N] markers in rendered HTML are converted to citation badges
 *  2. Citations inside HTML tags are NOT converted
 *  3. References section gets anchor IDs
 */

import { describe, it, expect } from 'vitest'

// --------------------------------------------------------------------------
// Replicate the pure-logic functions from Message.jsx so we can unit-test
// them without mounting React components.
// --------------------------------------------------------------------------

const processCitationBadges = (html) => {
  return html.replace(
    /(<[^>]*>)|(?<!\]\()(\[(\d{1,2})\])(?!\()/g,
    (match, tag, bracket, num) => {
      if (tag) return tag
      return `<sup class="rag-citation-badge" data-ref="${num}"><a href="#rag-ref-${num}" class="rag-citation-link">${num}</a></sup>`
    }
  )
}

const processReferencesSection = (html) => {
  const refIdx = html.indexOf('<strong>References</strong>')
  if (refIdx === -1) return html

  const before = html.slice(0, refIdx)
  const after = html.slice(refIdx)

  const anchored = after
    .replace(/<li>(\d{1,2})\.\s/g, (_, num) => `<li id="rag-ref-${num}">${num}. `)
    .replace(/<p>(\d{1,2})\.\s/g, (_, num) => `<p><span id="rag-ref-${num}"></span>${num}. `)

  return before + anchored
}

// --------------------------------------------------------------------------
// Tests
// --------------------------------------------------------------------------

describe('RAG citation badge rendering', () => {
  it('converts [1] into a citation badge', () => {
    const input = '<p>The API uses OAuth [1] for authentication.</p>'
    const result = processCitationBadges(input)
    expect(result).toContain('class="rag-citation-badge"')
    expect(result).toContain('data-ref="1"')
    expect(result).toContain('href="#rag-ref-1"')
    expect(result).toContain('class="rag-citation-link"')
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
    // Should not add a badge inside the tag
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
    // [123] should not become a badge (only 1-2 digit refs)
    expect(result).not.toContain('rag-citation-badge')
  })
})

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
    // Only the "1." after References should get an anchor
    const parts = result.split('<strong>References</strong>')
    expect(parts[0]).not.toContain('rag-ref-')
  })

  it('returns unchanged HTML when no References heading present', () => {
    const input = '<p>1. First item</p><p>2. Second item</p>'
    const result = processReferencesSection(input)
    expect(result).toBe(input)
  })
})
