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
import { act, cleanup, render } from '@testing-library/react'
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
// in the PDF and would produce a false positive). The braces are matched by
// counting rather than with a regex: `[\s\S]*` is greedy and would run to the
// last `}` in the file, swallowing every later at-rule and letting a
// non-print rule satisfy these assertions.
const extractPrintBlock = (source) => {
  const start = source.search(/@media print\s*\{/)
  if (start === -1) return ''
  const open = source.indexOf('{', start)
  let depth = 0
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1
    else if (source[i] === '}') {
      depth -= 1
      if (depth === 0) return source.slice(open + 1, i)
    }
  }
  return ''
}

const printCss = extractPrintBlock(css)

describe('print export -- the print block is extracted exactly', () => {
  it('stops at the closing brace of @media print', () => {
    // Guard for the assertions below: if the extraction over-ran, a rule from
    // a later at-rule could satisfy them. `@media (hover: none)` follows the
    // print block in index.css and must not be included.
    expect(printCss).not.toMatch(/@media \(hover/)
    expect(printCss).toMatch(/@page/)
  })
})

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

  it('reaches scrollers that come from a stylesheet rule, not a utility class', () => {
    // `.chat-messages .katex-display` and the markdown table get `overflow-x:
    // auto` from index.css itself, so the `[class*="overflow-x-auto"]`
    // selector cannot match them -- long equations and wide tables kept their
    // clipped right edge in the PDF until they were named explicitly.
    expect(printCss).toMatch(/\.chat-messages \.katex-display/)
    expect(printCss).toMatch(/\.chat-messages \.selectable-markdown table/)
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

describe('print export -- the rules the DOM changes depend on', () => {
  it('hides buttons only when they have not opted out', () => {
    // The `no-print-hide` class on the tool summary and download buttons is
    // meaningless unless the hide rule carries this exception; an edit to
    // either half silently drops tool rows from the PDF.
    expect(printCss).toMatch(/button:not\(\.no-print-hide\)/)
  })

  it('reveals print-only blocks', () => {
    // Collapsed tool details are mounted with `hidden print:block`, and
    // `hidden` (display: none) only loses to this !important rule.
    expect(printCss).toMatch(/\.print\\:block\s*\{[^}]*display:\s*block\s*!important/)
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

  const findLabel = (container, text) =>
    [...container.querySelectorAll('.text-xs.font-semibold')].find((el) => el.textContent === text)

  it('does not mount collapsed details on screen', () => {
    // MCP results are not size-bounded, so a collapsed row must not serialize
    // its arguments and result into hidden <pre> elements just in case the
    // user prints later.
    const { container } = render(<Message message={toolCallMessage} />)
    expect(findLabel(container, 'Input Arguments')).toBeUndefined()
    expect(findLabel(container, 'Output Result')).toBeUndefined()
  })

  it('mounts collapsed details on beforeprint, hidden on screen but visible in print', () => {
    // Default state is collapsed (toolDetailsCollapsed defaults to true). When
    // the browser starts printing, the details must appear in the DOM carrying
    // `hidden print:block` -- `print:block` is what the print stylesheet
    // reveals, so the PDF shows the tool's input/output for a row the user
    // never expanded, while the block stays out of the on-screen layout.
    const { container } = render(<Message message={toolCallMessage} />)

    act(() => {
      window.dispatchEvent(new Event('beforeprint'))
    })

    const inputLabel = findLabel(container, 'Input Arguments')
    const outputLabel = findLabel(container, 'Output Result')
    expect(inputLabel).toBeTruthy()
    expect(outputLabel).toBeTruthy()

    const detailsWrapper = inputLabel.closest('.space-y-3')
    expect(detailsWrapper.className).toContain('hidden')
    expect(detailsWrapper.className).toContain('print:block')

    // ...and unmounted again once the print job is over.
    act(() => {
      window.dispatchEvent(new Event('afterprint'))
    })
    expect(findLabel(container, 'Input Arguments')).toBeUndefined()
  })

  it('mounts details without hidden when the user expanded the row', () => {
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

  it('does not mount an empty details wrapper for a call with nothing to show', () => {
    // A classic-mode row with no arguments and no result has no details; an
    // empty wrapper would print as a band of indented whitespace.
    setChat({ compactMessages: false })
    const { container } = render(
      <Message message={{ ...toolCallMessage, arguments: {}, result: null, status: 'calling' }} />
    )
    act(() => {
      window.dispatchEvent(new Event('beforeprint'))
    })
    expect(container.querySelector('.space-y-3')).toBeNull()
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

describe('print export -- tool output file names (#774)', () => {
  it('prints the download file names under their label', () => {
    // The "N file(s) available for download:" label is plain text, but each
    // file name is a download <button> -- so the label printed with nothing
    // under it. The buttons opt out of the hide rule so the names survive.
    const { container } = render(
      <Message
        message={{
          role: 'assistant',
          type: 'tool_call',
          tool_name: 'basic_fns_plot',
          status: 'completed',
          arguments: { kind: 'scatter' },
          result: { meta_data: { output_files: ['chart.png'] } },
        }}
      />
    )
    const downloadButton = [...container.querySelectorAll('button')].find((b) =>
      b.textContent.includes('chart.png')
    )
    expect(downloadButton).toBeTruthy()
    expect(downloadButton.className).toContain('no-print-hide')
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