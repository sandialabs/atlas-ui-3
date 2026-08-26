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
    // The table scroller is phone-only: display:block drops the table out of
    // table layout, so above the sm breakpoint its columns would stop
    // stretching and leave a bordered gap beside the last one.
    const phoneOnly = css.match(/@media \(max-width: 639px\)\s*\{[\s\S]*?\n\}/)
    expect(phoneOnly).not.toBeNull()
    expect(phoneOnly[0]).toMatch(
      /\.chat-messages \.selectable-markdown table\s*\{[^}]*overflow-x: auto;/
    )
    expect(phoneOnly[0]).toMatch(
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

describe('mobile full-width responsive layout', () => {
  // Split a className string into a Set of exact tokens so that e.g. `p-3`
  // does not match inside `sm:p-3` and an inverted breakpoint would fail.
  const tokens = (el) => new Set(el.className.split(/\s+/))

  it('hides the message avatar below the sm breakpoint', () => {
    const { container } = render(
      <Message message={{ role: 'assistant', content: 'Hello' }} />
    )
    const avatar = container.querySelector('.rounded-full')
    const cls = tokens(avatar)
    expect(cls.has('hidden')).toBe(true)
    expect(cls.has('sm:flex')).toBe(true)
    // No bare `flex` that could override `hidden` depending on Tailwind's
    // emission order — correctness rests on `hidden sm:flex`, not `flex hidden`.
    expect(cls.has('flex')).toBe(false)
  })

  it('collapses the avatar gap on mobile and restores it at sm', () => {
    const { container } = render(
      <Message message={{ role: 'assistant', content: 'Hello' }} />
    )
    const row = container.querySelector('.flex.items-start')
    const cls = tokens(row)
    expect(cls.has('gap-0')).toBe(true)
    expect(cls.has('sm:gap-3')).toBe(true)
  })

  it('uses reduced bubble padding on mobile and full padding at sm', () => {
    const { container } = render(
      <Message message={{ role: 'assistant', content: 'Hello' }} />
    )
    const bubble = container.querySelector('.bg-gray-800.rounded-lg')
    const cls = tokens(bubble)
    expect(cls.has('p-3')).toBe(true)
    expect(cls.has('sm:p-4')).toBe(true)
  })

  it('widens the user bubble max-w on mobile and narrows it at sm', () => {
    const { container } = render(
      <Message message={{ role: 'user', content: 'Hello' }} />
    )
    const bubble = container.querySelector('.user-message-bubble')
    const cls = tokens(bubble)
    expect(cls.has('max-w-[85%]')).toBe(true)
    expect(cls.has('sm:max-w-[70%]')).toBe(true)
  })

  it('drops the compact row indent on mobile and restores it at sm', () => {
    const { container } = render(
      <Message
        message={{
          role: 'assistant',
          type: 'tool_call',
          tool_name: 'test_tool',
          server_name: 'test_server',
          status: 'completed',
          arguments: {},
        }}
      />
    )
    // The compact row is the top-level div inside the rendered container.
    const row = container.querySelector('div')
    const cls = tokens(row)
    expect(cls.has('pl-0')).toBe(true)
    expect(cls.has('sm:pl-11')).toBe(true)
  })

  it('uses flex-1 min-w-0 (not w-full) on thinking and agent bubbles', () => {
    const chatArea = readFileSync(
      resolve(process.cwd(), 'src/components/ChatArea.jsx'),
      'utf8'
    )
    // The agent-pending and thinking bubbles must use flex-1 min-w-0, not
    // w-full, so they don't overflow the row beside the avatar at sm+.
    // Match the full className of each div without embedding the asserted
    // classes in the pattern, so the assertions are real checks.
    const agentBubble = chatArea.match(/className="([^"]*border-purple-700[^"]*)"/)
    // Match the bubble inside the isThinking block by finding the second
    // occurrence of "rounded-lg p-3 sm:p-4" after "isThinking".
    const thinkingStart = chatArea.indexOf('isThinking && (')
    const thinkingSlice = chatArea.slice(thinkingStart)
    const thinkingBubble = thinkingSlice.match(/className="([^"]*rounded-lg[^"]*p-3 sm:p-4)"/)
    for (const [label, match] of [['agent', agentBubble], ['thinking', thinkingBubble]]) {
      expect(match, `${label} bubble not found`).not.toBeNull()
      const cls = new Set(match[1].split(/\s+/))
      expect(cls.has('flex-1'), `${label} bubble must have flex-1`).toBe(true)
      expect(cls.has('min-w-0'), `${label} bubble must have min-w-0`).toBe(true)
      expect(cls.has('w-full'), `${label} bubble must not have w-full`).toBe(false)
    }
  })

  it('reduces the transcript container padding on mobile and restores it at sm', () => {
    const chatArea = readFileSync(
      resolve(process.cwd(), 'src/components/ChatArea.jsx'),
      'utf8'
    )
    const main = chatArea.match(/<main[\s\S]*?className=\{`([^`]*)`\}/)
    expect(main).not.toBeNull()
    const cls = new Set(main[1].split(/\s+/))
    expect(cls.has('p-2')).toBe(true)
    expect(cls.has('sm:p-4')).toBe(true)
  })
})
