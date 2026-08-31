/**
 * Structured citations for tool-based search (issue #874).
 *
 * Before #862 the sources were a markdown block appended to the answer and the
 * UI scraped them back out. Search is a tool call now: the model may search
 * several times per turn, so the sources arrive as data on the message and are
 * rendered from it. These tests pin that path, and the websocket handler that
 * attaches the data in the first place.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderReferencesSection } from '../utils/ragCitations'
import { createWebSocketHandler, cleanupStreamState } from '../handlers/chat/websocketHandlers'

describe('renderReferencesSection', () => {
  it('renders a collapsible block with one anchored entry per citation', () => {
    const html = renderReferencesSection([
      { n: 1, filename: 'Runbook.md', url: 'https://example.com/runbook', data_source: 'atlas_rag:tech' },
      { n: 2, filename: 'Policy.pdf' },
    ], 'm1')

    expect(html).toContain('<details class="rag-references-collapse">')
    // Anchor IDs match what processCitationBadges points its chips at.
    expect(html).toContain('id="rag-ref-m1-1"')
    expect(html).toContain('id="rag-ref-m1-2"')
    expect(html).toContain('href="https://example.com/runbook"')
    expect(html).toContain('Policy.pdf')
    // Summary names the sources so a collapsed block still says what was read.
    expect(html).toContain('[1]</span> Runbook.md')
  })

  it('renders nothing when there are no citations', () => {
    expect(renderReferencesSection([], 'm1')).toBe('')
    expect(renderReferencesSection(null, 'm1')).toBe('')
    expect(renderReferencesSection(undefined)).toBe('')
  })

  it('orders entries by number regardless of arrival order', () => {
    const html = renderReferencesSection([{ n: 3, filename: 'c' }, { n: 1, filename: 'a' }], 's')
    expect(html.indexOf('rag-ref-s-1')).toBeLessThan(html.indexOf('rag-ref-s-3'))
  })

  it('escapes backend-controlled text instead of rendering it as markup', () => {
    const html = renderReferencesSection([
      { n: 1, filename: '<img src=x onerror=alert(1)>', citation: '</details><script>bad()</script>' },
    ], 's')
    expect(html).not.toContain('<img')
    expect(html).not.toContain('<script>')
    expect(html).toContain('&lt;img')
  })

  it('refuses to link a non-http scheme but still lists the document', () => {
    const html = renderReferencesSection([{ n: 1, filename: 'doc', url: 'javascript:alert(1)' }], 's')
    expect(html).not.toContain('javascript:')
    expect(html).toContain('doc')
  })
})

describe('citations websocket event', () => {
  let deps
  let mapped

  beforeEach(() => {
    cleanupStreamState()
    mapped = []
    deps = {
      addMessage: vi.fn(),
      mapMessages: vi.fn(mapper => mapped.push(mapper)),
      setIsThinking: vi.fn(),
      setCurrentAgentStep: vi.fn(),
      streamToken: vi.fn(),
      streamEnd: vi.fn(),
    }
  })

  const send = (handler, data) => handler(data)

  it('attaches citations to the newest assistant message', () => {
    const handler = createWebSocketHandler(deps)
    send(handler, { type: 'citations', citations: [{ n: 1, filename: 'a.pdf' }] })

    expect(mapped).toHaveLength(1)
    const messages = [
      { role: 'user', content: 'q' },
      { role: 'assistant', content: 'answer [1]' },
    ]
    const next = mapped[0](messages)
    expect(next[1].citations).toEqual([{ n: 1, filename: 'a.pdf' }])
    // The user message is untouched.
    expect(next[0]).toBe(messages[0])
  })

  it('skips tool rows and lands on the answer', () => {
    const handler = createWebSocketHandler(deps)
    send(handler, { type: 'citations', citations: [{ n: 1, filename: 'a.pdf' }] })
    const next = mapped[0]([
      { role: 'assistant', content: 'answer' },
      { role: 'assistant', type: 'tool_call', content: '' },
    ])
    expect(next[0].citations).toBeDefined()
    expect(next[1].citations).toBeUndefined()
  })

  it('retries on response_complete when the answer had not landed yet', () => {
    const handler = createWebSocketHandler(deps)
    send(handler, { type: 'citations', citations: [{ n: 1, filename: 'a.pdf' }] })
    send(handler, { type: 'response_complete' })
    // Once on arrival, once after the stream was committed.
    expect(deps.mapMessages).toHaveBeenCalledTimes(2)
  })

  it('does not carry one turn\'s citations into the next', () => {
    const handler = createWebSocketHandler(deps)
    send(handler, { type: 'citations', citations: [{ n: 1, filename: 'a.pdf' }] })
    send(handler, { type: 'response_complete' })
    deps.mapMessages.mockClear()
    send(handler, { type: 'response_complete' })
    expect(deps.mapMessages).not.toHaveBeenCalled()
  })

  it('settles citations on agent_completion, which agent mode ends with', () => {
    // Agent mode never emits response_complete, so without this the list stays
    // pending and leaks onto whatever answer completes next.
    const handler = createWebSocketHandler(deps)
    send(handler, { type: 'citations', citations: [{ n: 1, filename: 'a.pdf' }] })
    send(handler, { type: 'agent_update', update_type: 'agent_completion', steps: 3 })
    deps.mapMessages.mockClear()
    send(handler, { type: 'response_complete' })
    expect(deps.mapMessages).not.toHaveBeenCalled()
  })

  it('does not leak an agent turn\'s sources onto the next turn\'s answer', () => {
    const handler = createWebSocketHandler(deps)
    send(handler, { type: 'citations', citations: [{ n: 1, filename: 'a.pdf' }] })
    send(handler, { type: 'agent_update', update_type: 'agent_completion', steps: 2 })
    mapped.length = 0
    // A later turn that searched nothing at all.
    send(handler, { type: 'response_complete' })
    expect(mapped).toHaveLength(0)
  })

  it('ignores an empty citation list', () => {
    const handler = createWebSocketHandler(deps)
    send(handler, { type: 'citations', citations: [] })
    expect(deps.mapMessages).not.toHaveBeenCalled()
  })
})
