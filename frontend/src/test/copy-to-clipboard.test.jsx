/**
 * Regression tests for copy-to-clipboard behavior (#1151).
 *
 * jsdom has no layout or real clipboard, so the tests assert the structural
 * contract that makes the feature work, plus the observable button states:
 *
 * - every copy path flashes visible success AND failure feedback on the
 *   triggering button (previously failures were console-only)
 * - a long code block copies its full contents, not just the visible part
 *   (the copy core reads textContent of the whole code element)
 * - the code-block toolbar is sticky so it stays visible while a long block
 *   scrolls in the transcript, and the container must not clip it
 * - the Canvas panel has a working Copy button with feedback, and the Copy
 *   buttons the shared markdown renderer emits inside canvas content are
 *   actually wired up (the reported "Canvas Copy button does not work" bug:
 *   only Message.jsx had the delegated click listener)
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, cleanup } from '@testing-library/react'
import { marked } from 'marked'

import '../utils/markdownRenderer' // configure the shared marked singleton
import { copyCodeBlock, copyMessageContent, copyTextToClipboard } from '../utils/clipboard'
import { useCodeBlockCopy } from '../hooks/useCodeBlockCopy'
import CanvasPanel from '../components/CanvasPanel'
import { useChat } from '../contexts/ChatContext'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn(),
}))

const mockWriteText = vi.fn()

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  // Reset implementations too, so no test inherits a resolved/rejected
  // writeText from an earlier one (tests must pass when run alone with -t).
  mockWriteText.mockReset().mockResolvedValue(undefined)
  Object.assign(navigator, { clipboard: { writeText: mockWriteText } })
  document.execCommand = vi.fn(() => true)
})

afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
  vi.useRealTimers()
})

// Flush microtask chains (clipboard promise callbacks) under fake timers.
const flush = async () => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
}

// Build a code block exactly like markdownRenderer.js emits it.
const mountCodeBlock = (codeText) => {
  const container = document.createElement('div')
  container.className = 'code-block-container relative bg-gray-900 rounded-lg'
  const header = document.createElement('div')
  header.className = 'code-block-header sticky top-0 z-10 flex'
  const button = document.createElement('button')
  button.className = 'copy-button bg-gray-700 border-gray-600'
  button.setAttribute('data-action', 'copy-code')
  button.textContent = 'Copy'
  header.appendChild(button)
  container.appendChild(header)
  const pre = document.createElement('pre')
  pre.className = 'p-4 overflow-x-auto rounded-b-lg'
  const code = document.createElement('code')
  code.textContent = codeText
  pre.appendChild(code)
  container.appendChild(pre)
  document.body.appendChild(container)
  return { container, button }
}

describe('code-block copy button feedback (#1151)', () => {
  it('flashes success on the button when the Clipboard API succeeds', async () => {
    mockWriteText.mockResolvedValue(undefined)
    const { button } = mountCodeBlock('print("hi")')

    copyCodeBlock(button)
    await flush()

    expect(mockWriteText).toHaveBeenCalledWith('print("hi")')
    expect(button.classList.contains('bg-green-600')).toBe(true)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    // The flash is temporary: original markup and classes return.
    expect(button.innerHTML).toBe('Copy')
    expect(button.classList.contains('bg-green-600')).toBe(false)
    expect(button.classList.contains('bg-gray-700')).toBe(true)
  })

  it('falls back to execCommand and flashes success when the Clipboard API is unavailable', async () => {
    Object.assign(navigator, { clipboard: {} }) // no writeText -> fallback
    const { button } = mountCodeBlock('print("hi")')

    copyCodeBlock(button)
    await flush()

    expect(document.execCommand).toHaveBeenCalledWith('copy')
    expect(button.classList.contains('bg-green-600')).toBe(true)
  })

  it('flashes visible failure when the Clipboard API rejects and the fallback fails too', async () => {
    mockWriteText.mockRejectedValue(new Error('permission denied'))
    document.execCommand = vi.fn(() => false)
    const { button } = mountCodeBlock('print("hi")')

    copyCodeBlock(button)
    await flush()

    expect(button.classList.contains('bg-red-600')).toBe(true)
    expect(button.classList.contains('bg-green-600')).toBe(false)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(button.innerHTML).toBe('Copy')
    expect(button.classList.contains('bg-red-600')).toBe(false)
  })

  it('labels the flash for screen readers, not just with colour and an icon', async () => {
    mockWriteText.mockRejectedValue(new Error('denied'))
    document.execCommand = vi.fn(() => false)
    const { button } = mountCodeBlock('x')

    copyCodeBlock(button)
    await flush()
    expect(button.querySelector('.sr-only').textContent).toBe('Copy failed')
    expect(button.querySelector('svg').getAttribute('aria-hidden')).toBe('true')
  })

  it('swaps the neutral hover background so the flash shows while the pointer is still on the button', async () => {
    mockWriteText.mockResolvedValue(undefined)
    const { button } = mountCodeBlock('x')
    button.className = 'copy-button bg-gray-700 hover:bg-gray-600 border border-gray-600'

    copyCodeBlock(button)
    await flush()
    expect(button.classList.contains('hover:bg-gray-600')).toBe(false)
    expect(button.classList.contains('hover:bg-green-700')).toBe(true)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    // The original class list comes back verbatim.
    expect(button.className).toBe('copy-button bg-gray-700 hover:bg-gray-600 border border-gray-600')
  })

  it('does not leave the button stuck in the success state after two quick clicks', async () => {
    mockWriteText.mockResolvedValue(undefined)
    const { button } = mountCodeBlock('x')

    copyCodeBlock(button)
    await flush()
    copyCodeBlock(button) // second click before the first flash restored
    await flush()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(button.innerHTML).toBe('Copy')
    expect(button.classList.contains('bg-green-600')).toBe(false)
  })
})

describe('message copy button feedback (#1151)', () => {
  const NEUTRAL = 'copy-message-button bg-gray-700 hover:bg-gray-600 border border-gray-600 text-gray-200'
  const mountMessageButton = () => {
    const button = document.createElement('button')
    button.className = NEUTRAL
    const icon = document.createElement('span')
    icon.textContent = 'icon'
    button.appendChild(icon)
    document.body.appendChild(button)
    return { button, icon }
  }

  it('flashes red when both copy paths fail, then restores the original classes', async () => {
    mockWriteText.mockRejectedValue(new Error('permission denied'))
    document.execCommand = vi.fn(() => false)
    const { button } = mountMessageButton()

    copyMessageContent('hello', button)
    await flush()
    expect(button.classList.contains('bg-red-600')).toBe(true)
    expect(button.classList.contains('hover:bg-gray-600')).toBe(false)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(button.className).toBe(NEUTRAL)
  })

  it('does not get stuck green after two quick clicks', async () => {
    mockWriteText.mockResolvedValue(undefined)
    const { button } = mountMessageButton()

    copyMessageContent('hello', button)
    await flush()
    copyMessageContent('hello', button) // second click inside the 2 s flash
    await flush()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(button.className).toBe(NEUTRAL)
  })

  it('does not get stuck green after a success followed by a failure', async () => {
    mockWriteText.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('denied'))
    document.execCommand = vi.fn(() => false)
    const { button } = mountMessageButton()

    copyMessageContent('hello', button)
    await flush()
    copyMessageContent('hello', button)
    await flush()
    expect(button.classList.contains('bg-red-600')).toBe(true)
    expect(button.classList.contains('bg-green-600')).toBe(false)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(button.className).toBe(NEUTRAL)
  })

  it('never replaces the React-rendered children of the message button', async () => {
    mockWriteText.mockResolvedValue(undefined)
    const { button, icon } = mountMessageButton()

    copyMessageContent('hello', button)
    await flush()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(button.firstChild).toBe(icon)
  })
})

describe('full-content copying for long code blocks (#1151)', () => {
  it('copies the full contents of a long code block, not just the visible part', async () => {
    mockWriteText.mockResolvedValue(undefined)
    const lines = Array.from({ length: 2000 }, (_, i) => `line ${i}: value_${i}`)
    const longCode = lines.join('\n')
    const { button } = mountCodeBlock(longCode)

    copyCodeBlock(button)
    await flush()

    expect(mockWriteText).toHaveBeenCalledTimes(1)
    expect(mockWriteText.mock.calls[0][0]).toBe(longCode)
    expect(mockWriteText.mock.calls[0][0].split('\n')).toHaveLength(2000)
  })

  it('copies full contents of rendered markdown code blocks', async () => {
    mockWriteText.mockResolvedValue(undefined)
    const longCode = Array.from({ length: 1000 }, (_, i) => `value = ${i}`).join('\n')
    const html = marked.parse('```\n' + longCode + '\n```')

    const wrapper = document.createElement('div')
    wrapper.innerHTML = html
    const button = wrapper.querySelector('[data-action="copy-code"]')
    copyCodeBlock(button)
    await flush()

    expect(mockWriteText.mock.calls[0][0]).toBe(longCode)
  })
})

describe('sticky code-block toolbar (#1151)', () => {
  it('renders the toolbar sticky so it stays visible while a long block scrolls', () => {
    const html = marked.parse('```python\nprint("hi")\n```')
    expect(html).toMatch(/code-block-header[^"]*sticky top-0 z-10/)

    const wrapper = document.createElement('div')
    wrapper.innerHTML = html
    const header = wrapper.querySelector('.code-block-header')
    // The Copy button rides inside the toolbar, so it sticks with it.
    expect(header.querySelector('[data-action="copy-code"]')).not.toBeNull()
  })

  it('preserves syntax highlighting and the horizontal scroller', () => {
    const html = marked.parse('```python\nprint("hi")\n```')
    expect(html).toContain('hljs language-python')
    expect(html).toMatch(/<pre class="p-4 overflow-x-auto[^"]*rounded-b-lg/)
  })
})

describe('transcript CSS contract for the sticky toolbar (#1151)', () => {
  // vitest runs with the frontend/ directory as its root.
  const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8')

  it('does not clip the code-block container, so the sticky toolbar tracks the transcript scroller', () => {
    // An overflow-hidden ancestor becomes the sticky containing box; the
    // toolbar would pin to a container that never scrolls instead of the
    // transcript scroller it must follow.
    expect(css).not.toMatch(/\.code-block-container\s*\{[^}]*overflow-hidden/)
  })

  it('keeps the copy button in the toolbar flow instead of the container', () => {
    const rule = css.match(/(?<![\w.-])\.copy-button\s*\{[^}]*\}/)
    expect(rule).not.toBeNull()
    expect(rule[0]).not.toContain('absolute')
  })

  it('offsets the sticky toolbar by the transcript padding so it sits flush with the scroller top', () => {
    // Sticky offsets are measured inside the scroller's padding (p-2 sm:p-4).
    expect(css).toMatch(/\.chat-messages \.code-block-header\s*\{\s*top:\s*-0\.5rem;/)
    expect(css).toMatch(/@media \(min-width: 640px\)\s*\{\s*\.chat-messages \.code-block-header\s*\{\s*top:\s*-1rem;/)
  })

  it('reveals the copy button for keyboard focus, not just hover', () => {
    expect(css).toMatch(/\.code-block-container:focus-within \.copy-button/)
    expect(css).toMatch(/\.copy-button:focus-visible/)
  })

  it('keeps the copy button visible on touch devices', () => {
    const touchBlock = css.match(/@media \(hover: none\)\s*\{[\s\S]*?\n\}/)
    expect(touchBlock).not.toBeNull()
    expect(touchBlock[0]).toMatch(/\.copy-button/)
  })
})

describe('Canvas panel copy (#1151)', () => {
  const baseChat = {
    canvasContent: null,
    customUIContent: null,
    canvasFiles: [],
    currentCanvasFileIndex: 0,
    setCurrentCanvasFileIndex: vi.fn(),
    downloadFile: vi.fn(),
  }
  const setChat = (over = {}) => useChat.mockReturnValue({ ...baseChat, ...over })
  const renderPanel = () => render(<CanvasPanel isOpen onClose={() => {}} />)

  it('drops a clipboard result that resolves after the displayed content changed', async () => {
    let resolveWrite
    mockWriteText.mockImplementation(() => new Promise((r) => { resolveWrite = r }))
    setChat({ canvasContent: 'first' })
    const { rerender } = renderPanel()

    await act(async () => {
      fireEvent.click(screen.getByTestId('canvas-copy-button'))
      await vi.advanceTimersByTimeAsync(0)
    })
    setChat({ canvasContent: 'second' })
    rerender(<CanvasPanel isOpen onClose={() => {}} />)
    await act(async () => {
      resolveWrite()
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(screen.getByTestId('canvas-copy-button').title).toBe('Copy canvas content to clipboard')
  })

  it('disables Copy for custom UI with no content instead of copying "undefined"', () => {
    setChat({ customUIContent: { type: 'html_injection' } })
    renderPanel()
    expect(screen.getByTestId('canvas-copy-button')).toBeDisabled()
  })

  it('exposes the copy outcome to assistive technology', async () => {
    setChat({ canvasContent: 'text' })
    renderPanel()
    const copyButton = screen.getByTestId('canvas-copy-button')

    await act(async () => {
      fireEvent.click(copyButton)
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(copyButton.getAttribute('aria-label')).toBe('Copied to clipboard')
    expect(screen.getByRole('status').textContent).toBe('Copied to clipboard')
  })

  it('copies the displayed custom-UI error, not stale canvas content hidden behind it', async () => {
    setChat({
      canvasContent: 'stale markdown that is not on screen',
      customUIContent: { type: 'error', content: 'Tool UI failed to render' },
    })
    renderPanel()
    const copyButton = screen.getByTestId('canvas-copy-button')

    await act(async () => {
      fireEvent.click(copyButton)
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(mockWriteText).toHaveBeenCalledWith('Tool UI failed to render')
  })

  it('clears "Copied" feedback when the displayed content changes', async () => {
    setChat({ canvasContent: 'first' })
    const { rerender } = renderPanel()
    const copyButton = screen.getByTestId('canvas-copy-button')

    await act(async () => {
      fireEvent.click(copyButton)
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(copyButton.title).toBe('Copied to clipboard')

    setChat({ canvasContent: 'second' })
    rerender(<CanvasPanel isOpen onClose={() => {}} />)
    expect(screen.getByTestId('canvas-copy-button').title).toBe('Copy canvas content to clipboard')
  })

  it('copies the displayed canvas content and shows success feedback', async () => {
    setChat({ canvasContent: '# Atlas report\n\nGenerated **summary**.' })
    renderPanel()
    const copyButton = screen.getByTestId('canvas-copy-button')
    expect(copyButton).not.toBeDisabled()

    await act(async () => {
      fireEvent.click(copyButton)
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(mockWriteText).toHaveBeenCalledWith('# Atlas report\n\nGenerated **summary**.')
    expect(copyButton.title).toBe('Copied to clipboard')
    expect(copyButton.className).toContain('bg-green-600')

    // Feedback is temporary.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(copyButton.title).toBe('Copy canvas content to clipboard')
    expect(copyButton.className).toContain('bg-gray-700')
  })

  it('shows failure feedback when the clipboard is unavailable', async () => {
    mockWriteText.mockRejectedValue(new Error('permission denied'))
    document.execCommand = vi.fn(() => false)
    setChat({ canvasContent: 'plain text content' })
    renderPanel()
    const copyButton = screen.getByTestId('canvas-copy-button')

    await act(async () => {
      fireEvent.click(copyButton)
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(copyButton.title).toBe('Copy failed')
    expect(copyButton.className).toContain('bg-red-600')
    // A cross replaces the copy icon so failure is not signalled by colour alone.
    expect(copyButton.querySelector('svg.lucide-x')).not.toBeNull()
  })

  it('copies the content of the currently displayed inline canvas file', async () => {
    const file = {
      filename: 'script.py',
      type: 'code',
      isInline: true,
      content_base64: btoa('print("hello from file")'),
    }
    setChat({ canvasFiles: [file] })
    renderPanel()
    const copyButton = screen.getByTestId('canvas-copy-button')
    expect(copyButton).not.toBeDisabled()

    await act(async () => {
      fireEvent.click(copyButton)
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(mockWriteText).toHaveBeenCalledWith('print("hello from file")')
    expect(copyButton.title).toBe('Copied to clipboard')
  })

  it('wires up the Copy buttons that canvas-rendered markdown emits (the reported bug)', async () => {
    mockWriteText.mockResolvedValue(undefined)
    setChat({ canvasContent: '```python\nprint("from canvas")\n```' })
    const { container: panelWrapper } = renderPanel()

    // The shared markdown renderer emits the toolbar Copy button inside the
    // canvas content; before the fix its clicks were handled nowhere here.
    // Scope to the panel: earlier tests leave manually-mounted code blocks in
    // document.body, and the leaked button must not be picked up.
    const codeCopyButton = panelWrapper.querySelector('[data-action="copy-code"]')
    expect(codeCopyButton).not.toBeNull()

    await act(async () => {
      fireEvent.click(codeCopyButton)
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(mockWriteText).toHaveBeenCalledWith('print("from canvas")')
    expect(codeCopyButton.classList.contains('bg-green-600')).toBe(true)
  })

  it('disables copying for image files and for an empty canvas', async () => {
    // Image content has nothing clipboard-worthy to copy.
    URL.createObjectURL = vi.fn(() => 'blob:image')
    const imageFile = {
      filename: 'chart.png',
      type: 'image',
      isInline: true,
      content_base64: btoa('notreallyanimage'),
    }
    setChat({ canvasFiles: [imageFile] })
    const { unmount } = renderPanel()
    expect(screen.getByTestId('canvas-copy-button')).toBeDisabled()
    unmount()
    cleanup()

    // Same for the empty state.
    setChat({})
    renderPanel()
    expect(screen.getByTestId('canvas-copy-button')).toBeDisabled()
  })

  it('copies plain text via copyTextToClipboard and reports failure as false', async () => {
    mockWriteText.mockResolvedValue(undefined)
    await expect(copyTextToClipboard('text')).resolves.toBe(true)
    expect(mockWriteText).toHaveBeenCalledWith('text')

    mockWriteText.mockRejectedValue(new Error('denied'))
    document.execCommand = vi.fn(() => false)
    await expect(copyTextToClipboard('text')).resolves.toBe(false)
  })
})

describe('screen-reader announcement of the copy outcome (#1151)', () => {
  it('announces through one persistent live region, filled after it is cleared', async () => {
    mockWriteText.mockResolvedValue(undefined)
    const { button } = mountCodeBlock('x')

    copyCodeBlock(button)
    await flush()
    const region = document.getElementById('atlas-copy-status')
    expect(region).not.toBeNull()
    expect(region.getAttribute('aria-live')).toBe('polite')
    expect(region.textContent).toBe('') // cleared first ...

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })
    expect(region.textContent).toBe('Copied') // ... then filled, so it is a change
    // The in-button text is not itself a live region.
    expect(button.querySelector('[aria-live]')).toBeNull()
  })

  it('announces message copy failures too, not only colour', async () => {
    mockWriteText.mockRejectedValue(new Error('denied'))
    document.execCommand = vi.fn(() => false)
    const button = document.createElement('button')
    button.className = 'copy-message-button bg-gray-700'
    document.body.appendChild(button)

    copyMessageContent('hello', button)
    await flush()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })
    expect(document.getElementById('atlas-copy-status').textContent).toBe('Copy failed')
  })
})

describe('useCodeBlockCopy (#1151)', () => {
  const Rendered = ({ html, show = true }) => {
    const ref = useCodeBlockCopy()
    if (!show) return null
    return <div ref={ref} dangerouslySetInnerHTML={{ __html: html }} />
  }

  it('makes renderer Copy buttons work in any container that uses it', async () => {
    const html = marked.parse('```python\nprint("help")\n```')
    const { container } = render(<Rendered html={html} />)

    fireEvent.click(container.querySelector('[data-action="copy-code"]'))
    await flush()
    expect(mockWriteText).toHaveBeenCalledWith('print("help")')
  })

  it('attaches when the container mounts after the first render (splash screen)', async () => {
    const html = marked.parse('```\nsplash code\n```')
    const { container, rerender } = render(<Rendered html={html} show={false} />)
    rerender(<Rendered html={html} show />)

    fireEvent.click(container.querySelector('[data-action="copy-code"]'))
    await flush()
    expect(mockWriteText).toHaveBeenCalledWith('splash code')
  })

  it('is wired into the Help page and splash screen', () => {
    for (const file of ['src/components/HelpPage.jsx', 'src/components/SplashScreen.jsx']) {
      const src = readFileSync(resolve(process.cwd(), file), 'utf8')
      expect(src).toMatch(/useCodeBlockCopy\(\)/)
      expect(src).toMatch(/ref=\{contentRef\}/)
    }
  })
})
