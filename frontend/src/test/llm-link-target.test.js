/**
 * Tests for chat-response link target behavior (GH #859)
 *
 * Links rendered in assistant messages must open in a new tab so users are not
 * navigated away from ATLAS. The marked link renderer adds target="_blank"
 * to external links, but DOMPurify 3.x strips `target` by default unless it is
 * explicitly allowlisted in ADD_ATTR. These tests guard the full pipeline
 * (marked.parse -> DOMPurify.sanitize with DOMPURIFY_CONFIG) end to end.
 */

import { describe, it, expect } from 'vitest'
import { marked, DOMPURIFY_CONFIG } from '../utils/markdownRenderer'
import DOMPurify from 'dompurify'

// Mirrors the render path in Message.jsx for assistant content.
function renderAssistant(markdown) {
  const html = marked.parse(markdown)
  return DOMPurify.sanitize(html, DOMPURIFY_CONFIG)
}

describe('chat-response link targets (GH #859)', () => {
  it('opens markdown links in a new tab', () => {
    const out = renderAssistant('See [Google](https://google.com).')
    expect(out).toContain('href="https://google.com"')
    expect(out).toContain('target="_blank"')
  })

  it('opens autolinked (bare) URLs in a new tab', () => {
    const out = renderAssistant('Visit https://example.com today.')
    expect(out).toContain('href="https://example.com"')
    expect(out).toContain('target="_blank"')
  })

  it('adds rel="noopener noreferrer" to external links', () => {
    const out = renderAssistant('See [Google](https://google.com).')
    expect(out).toContain('rel="noopener noreferrer"')
  })

  it('keeps in-app fragment links in the same tab', () => {
    const out = renderAssistant('Jump to [section](#section).')
    expect(out).toContain('href="#section"')
    expect(out).not.toContain('target="_blank"')
  })

  it('preserves target="_blank" across multiple links', () => {
    const out = renderAssistant('[a](https://a.com) and [b](https://b.com).')
    const matches = out.match(/target="_blank"/g)
    expect(matches).toHaveLength(2)
  })
})