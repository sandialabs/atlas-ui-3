/**
 * Tests for chat-response link target behavior (GH #859)
 *
 * Links rendered in assistant messages must open in a new tab so users are not
 * navigated away from ATLAS, while in-app navigation (fragments, server-relative
 * and relative paths, and same-origin absolute URLs) stays in the current tab.
 *
 * The marked link renderer adds target="_blank" to external links, but DOMPurify
 * 3.x strips `target` by default unless it is explicitly allowlisted in ADD_ATTR;
 * an `afterSanitizeAttributes` hook then constrains the allowlist to anchors-only
 * and normalizes the target. These tests guard the full pipeline
 * (marked.parse -> DOMPurify.sanitize with DOMPURIFY_CONFIG) end to end.
 *
 * The `renderAssistant` helper exercises only the link/markdown stage of the
 * Message.jsx render path (it deliberately omits the citation and LaTeX stages,
 * which are independent of link-target handling).
 */

import { describe, it, expect } from 'vitest'
import { marked, DOMPURIFY_CONFIG } from '../utils/markdownRenderer'
import DOMPurify from 'dompurify'

// Mirrors the render path in Message.jsx for assistant content.
function renderAssistant(markdown) {
  const html = marked.parse(markdown)
  return DOMPurify.sanitize(html, DOMPURIFY_CONFIG)
}

// Parse rendered HTML and return its <a> elements for precise per-anchor checks.
function anchors(html) {
  const container = document.createElement('div')
  container.innerHTML = html
  return Array.from(container.querySelectorAll('a'))
}

describe('chat-response external links open in a new tab (GH #859)', () => {
  it('opens markdown links in a new tab with rel=noopener', () => {
    const a = anchors(renderAssistant('See [Google](https://google.com).'))[0]
    expect(a.getAttribute('href')).toBe('https://google.com')
    expect(a.getAttribute('target')).toBe('_blank')
    expect(a.getAttribute('rel')).toBe('noopener noreferrer')
  })

  it('opens autolinked (bare) URLs in a new tab', () => {
    const a = anchors(renderAssistant('Visit https://example.com today.'))[0]
    expect(a.getAttribute('href')).toBe('https://example.com')
    expect(a.getAttribute('target')).toBe('_blank')
    expect(a.getAttribute('rel')).toBe('noopener noreferrer')
  })

  it('opens protocol-relative URLs in a new tab', () => {
    const a = anchors(renderAssistant('See [evil](//evil.example).'))[0]
    expect(a.getAttribute('target')).toBe('_blank')
  })

  it('preserves target="_blank" across multiple links', () => {
    const links = anchors(renderAssistant('[a](https://a.com) and [b](https://b.com).'))
    expect(links).toHaveLength(2)
    expect(links.every((a) => a.getAttribute('target') === '_blank')).toBe(true)
  })
})

describe('chat-response in-app links stay in the same tab (GH #859)', () => {
  it('keeps fragment links same-tab', () => {
    const a = anchors(renderAssistant('Jump to [section](#section).'))[0]
    expect(a.getAttribute('href')).toBe('#section')
    expect(a.getAttribute('target')).toBeNull()
  })

  it('keeps server-relative links same-tab', () => {
    const a = anchors(renderAssistant('Open [settings](/settings).'))[0]
    expect(a.getAttribute('href')).toBe('/settings')
    expect(a.getAttribute('target')).toBeNull()
  })

  it('keeps relative links same-tab', () => {
    const links = anchors(renderAssistant('Go [here](./page) or [there](../up).'))
    expect(links).toHaveLength(2)
    expect(links.every((a) => a.getAttribute('target') === null)).toBe(true)
  })

  it('keeps same-origin absolute URLs same-tab', () => {
    const sameOrigin = window.location.origin + '/about'
    const a = anchors(renderAssistant(`See [about](${sameOrigin}).`))[0]
    expect(a.getAttribute('href')).toBe(sameOrigin)
    expect(a.getAttribute('target')).toBeNull()
  })
})

describe('DOMPurify target allowlist is constrained to safe anchors', () => {
  it('normalizes a raw target=_top anchor to _blank with rel', () => {
    const a = anchors(renderAssistant('<a href="https://evil.example" target="_top">x</a>'))[0]
    expect(a.getAttribute('href')).toBe('https://evil.example')
    expect(a.getAttribute('target')).toBe('_blank')
    expect(a.getAttribute('rel')).toBe('noopener noreferrer')
  })

  it('forces rel=noopener on a raw target=_blank anchor missing rel', () => {
    const a = anchors(renderAssistant('<a href="https://evil.example" target="_blank">x</a>'))[0]
    expect(a.getAttribute('target')).toBe('_blank')
    expect(a.getAttribute('rel')).toBe('noopener noreferrer')
  })

  it('opens a raw external anchor with no target in a new tab', () => {
    const a = anchors(renderAssistant('<a href="https://example.com">x</a>'))[0]
    expect(a.getAttribute('href')).toBe('https://example.com')
    expect(a.getAttribute('target')).toBe('_blank')
    expect(a.getAttribute('rel')).toBe('noopener noreferrer')
  })

  it('strips an injected target from a raw in-app anchor', () => {
    const a = anchors(renderAssistant('<a href="/settings" target="_blank">x</a>'))[0]
    expect(a.getAttribute('href')).toBe('/settings')
    expect(a.getAttribute('target')).toBeNull()
  })

  it('strips target from non-anchor elements', () => {
    const html = renderAssistant('<div target="_blank">nope</div>')
    const container = document.createElement('div')
    container.innerHTML = html
    const div = container.querySelector('div')
    expect(div).not.toBeNull()
    expect(div.getAttribute('target')).toBeNull()
  })
})
