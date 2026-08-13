/**
 * Regression tests for PDF/print export (#774).
 *
 * jsdom has no print engine, so these assert the structural contract that
 * makes the PDF match what is on screen:
 *
 *  - the print stylesheet must override horizontal scrollers (overflow-x-auto)
 *    so wide content is not clipped at the right margin of the page
 *  - <pre> blocks must wrap in print, since a scroller cannot be panned on
 *    paper and a long line would otherwise be cut off on the right
 *  - tool call summary rows render inside a <button>, which the print
 *    stylesheet hides by default (`button:not(.no-print-hide)`); the summary
 *    button must opt back in via `no-print-hide` or the entire tool call is
 *    absent from the PDF
 *  - tool call details (input arguments + output) must be in the DOM even
 *    when collapsed, so `print:block` can reveal them in the PDF without the
 *    user expanding every row first
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import Message from '../components/Message'
import { useChat } from '../contexts/ChatContext'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn(),
}))

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

const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8')

// Pull just the @media print block so every assertion below is scoped to it
// rather than to the whole stylesheet (a rule outside print would not fire
// in the PDF and would produce a false positive).
const printBlock = css.match(/@media print\s*\{([\s\S]*)\n\}/)
const printCss = printBlock ? printBlock[1] : ''

describe('print export -- right side not clipped (#774)', () => {
  it('overrides overflow-x-auto so horizontal scrollers show all content', () => {
    // Code blocks, wide tables, and display math carry overflow-x-auto; in
    // print a scroller cannot be panned, so without this override the right
    // edge of the content is clipped to the box width.
    expect(printCss).toMatch(/\[class\*="overflow-x-auto"\]/)
  })

  it('forces <pre> blocks to wrap instead of clipping long lines', () => {
    // The screen layout gives <pre> a horizontal scroller and overflow-wrap:
    // normal so code keeps its indentation. In print that combination clips
    // long lines on the right; the print block must re-enable wrapping.
    expect(printCss).toMatch(/pre[\s\S]{0,60}\{[^}]*overflow:\s*visible/)
    expect(printCss).toMatch(/pre[\s\S]{0,60}\{[^}]*overflow-wrap:\s*anywhere/)
    expect(printCss).toMatch(/pre[\s\S]{0,60}\{[^}]*white-space:\s*pre-wrap/)
  })

  it('also targets the inner <code> and its highlight spans', () => {
    // `.chat-messages pre code { overflow-wrap: normal }` (#747) keeps code
    // snippets on their screen scroller, and every highlight.js token span
    // inherits it. Overriding only <pre> leaves the tokens unbreakable, so a
    // long line still runs off the right edge of the page.
    expect(printCss).toMatch(/pre code\s*,/)
    expect(printCss).toMatch(/pre code \*/)
  })

  it('pins every element to the page width so the layout cannot outgrow it', () => {
    // The chat column is a flex item whose default `min-width: auto` lets it
    // grow to its content's intrinsic width. On screen that is invisible
    // because the column also clips horizontally; lifting that clip for print
    // (the rule above) lets one long code line stretch the layout several
    // page-widths wide, cutting off everything past the first page-width.
    expect(printCss).toMatch(/body \*\s*\{[^}]*min-width:\s*0/)
    expect(printCss).toMatch(/body \*\s*\{[^}]*max-width:\s*100%/)
  })

  it('keeps the existing overflow-y-auto / overflow-hidden overrides', () => {
    // Regression guard: adding overflow-x-auto must not drop the existing
    // selectors that the #150 print stylesheet relied on.
    expect(printCss).toMatch(/\[class\*="overflow-y-auto"\]/)
    expect(printCss).toMatch(/\[class\*="overflow-hidden"\]/)
  })

  it('gives dark tool-call detail boxes a light background so black text stays readable', () => {
    // The argument/result boxes carry bg-gray-900, which on screen is fine
    // with light text but in print (where `* { color: black }` is forced)
    // would be black-on-dark and unreadable. The print block must whiten
    // bg-gray-900 alongside the existing bg-gray-800 / bg-blue-600 rules.
    expect(printCss).toMatch(/\[class\*="rounded-lg"\]\[class\*="bg-gray-900"\]/)
  })
})

describe('print export -- tool calls render in the PDF (#774)', () => {
  const toolCallMessage = {
    role: 'assistant',
    type: 'tool_call',
    tool_name: 'basic_fns_bash',
    server_name: 'basic_fns',
    status: 'completed',
    arguments: { command: 'echo hello' },
    result: { stdout: 'hello\n' },
  }

  it('the compact tool summary button opts out of the print hide rule', () => {
    const { container } = render(<Message message={toolCallMessage} />)
    const summary = [...container.querySelectorAll('button')].find((b) =>
      b.textContent.includes('basic_fns_bash')
    )
    expect(summary).not.toBeNull()
    // The print stylesheet hides `button:not(.no-print-hide)`. Without this
    // class the entire tool call row is absent from the PDF.
    expect(summary.className).toContain('no-print-hide')
  })

  it('the classic (non-compact) tool summary button also opts out', () => {
    setChat({ compactMessages: false })
    const { container } = render(<Message message={toolCallMessage} />)
    const summary = [...container.querySelectorAll('button')].find((b) =>
      b.textContent.includes('basic_fns_bash')
    )
    expect(summary).not.toBeNull()
    expect(summary.className).toContain('no-print-hide')
  })

  it('renders tool call details even when collapsed, hidden on screen but visible in print', () => {
    // Default state is collapsed (toolDetailsCollapsed defaults to true), so
    // the details block must be in the DOM with `hidden print:block` -- the
    // `print:block` class is what the print stylesheet reveals. If the
    // details were conditionally rendered only when expanded, the PDF would
    // show the tool name but not its input/output.
    const { container } = render(<Message message={toolCallMessage} />)
    const labels = [...container.querySelectorAll('.text-xs.font-semibold')]
    const inputLabel = labels.find((el) => el.textContent === 'Input Arguments')
    const outputLabel = labels.find((el) => el.textContent === 'Output Result')
    expect(inputLabel).toBeTruthy()
    expect(outputLabel).toBeTruthy()

    // The details wrapper must carry the hidden + print:block pair so it is
    // hidden on screen (collapsed) but rendered in the PDF.
    const detailsWrapper = inputLabel.closest('.space-y-3')
    expect(detailsWrapper.className).toContain('hidden')
    expect(detailsWrapper.className).toContain('print:block')
  })

  it('renders tool call details without hidden when expanded', () => {
    // When the user expands the row on screen, the details block must not
    // carry `hidden` -- otherwise the screen view would disagree with the
    // expanded state and the print view would render an already-visible block
    // the same as a collapsed one.
    localStorage.setItem('toolDetailsCollapsed', 'false')
    const { container } = render(<Message message={toolCallMessage} />)
    const inputLabel = [...container.querySelectorAll('.text-xs.font-semibold')].find(
      (el) => el.textContent === 'Input Arguments'
    )
    const detailsWrapper = inputLabel.closest('.space-y-3')
    expect(detailsWrapper.className).not.toContain('hidden')
  })

  it('does not hide the tool call summary for an in-progress call', () => {
    // An in-progress tool call (status: calling) still renders a summary
    // button; the print export must show that the call happened even if it
    // never produced a result.
    const { container } = render(
      <Message message={{ ...toolCallMessage, status: 'calling', result: null }} />
    )
    const summary = [...container.querySelectorAll('button')].find((b) =>
      b.textContent.includes('basic_fns_bash')
    )
    expect(summary).not.toBeNull()
    expect(summary.className).toContain('no-print-hide')
  })
})

describe('print export -- non-tool buttons stay hidden (#774 guard)', () => {
  it('does not slap no-print-hide on the copy / edit / correct buttons', () => {
    // Regression guard: only the tool summary button should opt out of the
    // print hide rule. The copy/edit/correct buttons on an assistant message
    // are interactive chrome and must stay hidden in the PDF.
    const { container } = render(
      <Message
        message={{ role: 'assistant', content: 'hello' }}
        userIndex={null}
        onRewind={vi.fn()}
      />
    )
    const buttons = [...container.querySelectorAll('button')]
    for (const button of buttons) {
      expect(button.className).not.toContain('no-print-hide')
    }
  })
})