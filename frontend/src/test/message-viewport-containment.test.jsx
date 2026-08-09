/**
 * Regression tests for horizontal overflow on mobile (#747).
 *
 * jsdom has no layout engine, so these assert the structural contract that
 * keeps long content inside the viewport rather than measured pixel widths:
 *
 *  - every message row that sits next to the avatar in a flex row must be
 *    allowed to shrink (min-w-0); with the default min-width:auto a single
 *    unbroken token (long tool name, raw URL, base64 blob) sets the row's
 *    minimum width and pushes the page wider than the screen
 *  - plain-text bodies must have a break opportunity (break-words)
 *  - the transcript container carries the .chat-messages hook that the CSS
 *    containment rules in index.css key off, plus the overflow-x safeguard
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import Message from '../components/Message'
import ToolApprovalMessage from '../components/ToolApprovalMessage'
import { useChat } from '../contexts/ChatContext'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn(),
}))

const LONG_TOOL_NAME = 'basic_memory_discover_topics_across_every_note_in_the_vault'
const LONG_URL =
  'https://example.com/a/very/long/path/that/offers/no/break/opportunity/at-all-1234567890'

const setChat = (settings = {}) => {
  useChat.mockReturnValue({
    appName: 'Atlas',
    downloadFile: vi.fn(),
    isSynthesizing: false,
    sendApprovalResponse: vi.fn(),
    updateSettings: vi.fn(),
    updateToolResult: vi.fn(),
    settings: { autoApproveTools: false, ...settings },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  setChat()
})

afterEach(() => {
  cleanup()
})

describe('message viewport containment (#747)', () => {
  it('lets the assistant bubble shrink beside the avatar', () => {
    const { container } = render(
      <Message message={{ role: 'assistant', content: LONG_URL }} />
    )
    const bubble = container.querySelector('.bg-gray-800.rounded-lg')
    expect(bubble.className).toContain('min-w-0')
    // w-full would claim 100% of the row *plus* the avatar and gap.
    expect(bubble.className).not.toContain('w-full')
  })

  it('lets the user bubble shrink and breaks long words in it', () => {
    const { container } = render(
      <Message message={{ role: 'user', content: LONG_URL }} />
    )
    const bubble = container.querySelector('.user-message-bubble')
    expect(bubble.className).toContain('min-w-0')
    expect(bubble.querySelector('.whitespace-pre-wrap').className).toContain('break-words')
  })

  it('wraps a long tool name instead of widening the compact tool row', () => {
    const { container } = render(
      <Message
        message={{
          role: 'assistant',
          type: 'tool_call',
          tool_name: LONG_TOOL_NAME,
          server_name: 'basic_memory',
          status: 'completed',
          arguments: {},
        }}
      />
    )
    const row = [...container.querySelectorAll('button')].find((b) =>
      b.textContent.includes(LONG_TOOL_NAME)
    )
    expect(row.className).toContain('flex-wrap')
    expect(row.className).toContain('min-w-0')
    const name = [...row.querySelectorAll('span')].find(
      (s) => s.textContent === LONG_TOOL_NAME
    )
    expect(name.className).toContain('min-w-0')
    expect(name.className).toContain('break-words')
  })

  it('keeps the same guarantees for the classic (non-compact) tool row', () => {
    setChat({ compactMessages: false })
    const { container } = render(
      <Message
        message={{
          role: 'assistant',
          type: 'tool_call',
          tool_name: LONG_TOOL_NAME,
          server_name: 'basic_memory',
          status: 'completed',
          arguments: {},
        }}
      />
    )
    const row = [...container.querySelectorAll('button')].find((b) =>
      b.textContent.includes(LONG_TOOL_NAME)
    )
    expect(row.className).toContain('flex-wrap')
    const name = [...row.querySelectorAll('span')].find(
      (s) => s.textContent === LONG_TOOL_NAME
    )
    expect(name.className).toContain('break-words')
  })
})

describe('tool approval actions stay legible (#747)', () => {
  // The containment rule clears min-width on every button in the transcript.
  // In a nowrap row the fixed-width Approve/Reject actions absorbed all of the
  // shrinkage -- rendering as "Ap" and "Re" at 375px -- while the flex-1 text
  // input held its intrinsic minimum. The row must wrap and the buttons must
  // opt out of shrinking instead.
  const approvalMessage = {
    role: 'assistant',
    type: 'tool_approval_request',
    tool_name: 'basic_memory_discover_topics',
    server_name: 'basic_memory',
    status: 'pending',
    arguments: { q: LONG_URL },
    tool_call_id: 'call_1',
  }

  for (const compact of [true, false]) {
    it(`keeps the ${compact ? 'compact' : 'classic'} action row wrappable`, () => {
      const { container } = render(
        <ToolApprovalMessage message={approvalMessage} compact={compact} />
      )
      const input = container.querySelector('input[type="text"]')
      const row = input.parentElement
      expect(row.className).toContain('flex-wrap')
      // A legible floor, so the input drops to its own line rather than
      // collapsing to a sliver next to the buttons.
      expect(input.className).toContain('min-w-[12rem]')
      const actions = [...row.querySelectorAll('button')]
      expect(actions.length).toBeGreaterThanOrEqual(2)
      for (const button of actions) {
        expect(button.className).toContain('shrink-0')
      }
    })
  }
})

describe('transcript containment styles (#747)', () => {
  // vitest runs with the frontend/ directory as its root.
  const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8')

  it('clears the automatic flex minimum inside the transcript', () => {
    expect(css).toMatch(/\.chat-messages :where\([^)]*\)\s*\{\s*min-width: 0;/)
  })

  it('leaves replaced elements out of the shrink list so icons keep their size', () => {
    // Pin the exclusion itself: a "simplification" to :where(*) would squash
    // every svg/img icon in the transcript while still matching the rule above.
    const [, selectorList] = css.match(/\.chat-messages :where\(([^)]*)\)/)
    const tags = selectorList.split(',').map((s) => s.trim())
    expect(tags).not.toContain('svg')
    expect(tags).not.toContain('img')
    expect(tags).not.toContain('*')
    expect(tags).toContain('div')
  })

  it('gives long strings a break opportunity but leaves code alone', () => {
    expect(css).toMatch(/\.chat-messages,[\s\S]*?overflow-wrap: anywhere;/)
    expect(css).toMatch(/\.chat-messages pre[\s\S]*?overflow-wrap: normal;/)
  })

  it('gives display math and wide tables their own scroller', () => {
    // Neither can be broken by overflow-wrap, so without a scroller the
    // transcript's overflow-x-hidden clips them with no affordance (#747).
    expect(css).toMatch(
      /\.chat-messages \.katex-display\s*\{[^}]*overflow-x: auto;/
    )
    expect(css).toMatch(
      /\.chat-messages \.selectable-markdown table\s*\{[^}]*overflow-x: auto;/
    )
    expect(css).toMatch(
      /\.chat-messages \.selectable-markdown table th,[\s\S]*?overflow-wrap: normal;/
    )
  })

  it('applies the .chat-messages hook to the transcript container element', () => {
    const chatArea = readFileSync(
      resolve(process.cwd(), 'src/components/ChatArea.jsx'),
      'utf8'
    )
    // Match the className of the <main> element rather than the file at large:
    // a bare substring check also matches the comments that discuss the class,
    // so moving it to the wrong element would leave the test green.
    const main = chatArea.match(/<main[\s\S]*?className=\{`([^`]*)`\}/)
    expect(main).not.toBeNull()
    expect(main[1]).toContain('chat-messages')
    expect(main[1]).toContain('overflow-x-hidden')
  })
})
