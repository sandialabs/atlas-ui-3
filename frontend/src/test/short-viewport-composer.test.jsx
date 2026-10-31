/**
 * Short windows were giving the transcript a thin strip (issue #1135).
 *
 * jsdom has no layout engine, so these tests pin the behavior the CSS then
 * sizes: the short-viewport class, the collapsed file list, the single file
 * count, the one-line agent status, and the composer height budget in
 * index.css. They do not measure pixels.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen, fireEvent, act, cleanup, within } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ChatArea from '../components/ChatArea'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'
import { SHORT_VIEWPORT_QUERY } from '../hooks/useShortViewport'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext', () => ({
  useMarketplace: () => ({ isComplianceAccessible: () => true }),
  useOptionalMarketplace: () => null,
}))
vi.mock('../contexts/WSContext')

// vitest rewrites import.meta.url, so read the stylesheet from the frontend
// package root (the directory `npm test` is run from).
const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8')

describe('short-viewport composer (issue #1135)', () => {
  let short = false
  const originalMatchMedia = window.matchMedia

  const chatContext = (overrides = {}) => ({
    messages: [{ role: 'assistant', content: 'Earlier answer that should stay readable.' }],
    isWelcomeVisible: false,
    isThinking: false,
    isStreaming: false,
    sendChatMessage: vi.fn(),
    currentModel: 'gpt-4',
    models: [{ name: 'gpt-4', display_name: 'GPT-4', supports_tools: true }],
    tools: [{ key: 'srv_tool', name: 'lookup', server: 'srv' }],
    prompts: [],
    selectedTools: new Set(['srv_tool']),
    selectedPrompts: new Set(),
    toggleTool: vi.fn(),
    togglePrompt: vi.fn(),
    sessionFiles: { files: [], total_files: 0, categories: {} },
    agentModeAvailable: true,
    agentModeEnabled: true,
    setAgentModeEnabled: vi.fn(),
    isAgentRunning: true,
    currentAgentStep: 2,
    agentPendingQuestion: null,
    setAgentPendingQuestion: vi.fn(),
    stopAgent: vi.fn(),
    stopStreaming: vi.fn(),
    answerAgentQuestion: vi.fn(),
    followUpSuggestions: [],
    setFollowUpSuggestions: vi.fn(),
    settings: {},
    features: { workspaces: true, tools: true },
    workspaces: [],
    ...overrides,
  })

  beforeEach(() => {
    short = false
    vi.clearAllMocks()
    window.matchMedia = (query) => ({
      matches: query === SHORT_VIEWPORT_QUERY ? short : false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })
    useWS.mockReturnValue({ isConnected: true, connectionStatus: 'Connected', sendMessage: vi.fn() })
    useChat.mockReturnValue(chatContext())
  })

  afterEach(() => {
    cleanup()
    window.matchMedia = originalMatchMedia
    document.documentElement.classList.remove('atlas-short-viewport')
  })

  const renderChat = () => render(
    <BrowserRouter>
      <ChatArea />
    </BrowserRouter>
  )

  const dropFile = (name) => {
    const reader = {
      readAsDataURL: vi.fn(),
      result: 'data:text/plain;base64,ZmlsZQ==',
      onload: null,
    }
    vi.spyOn(global, 'FileReader').mockImplementation(() => reader)
    const file = new File([new Blob(['file'])], name, { type: 'text/plain' })
    const chat = screen.getByRole('main').parentElement
    fireEvent.drop(chat, {
      preventDefault: vi.fn(),
      stopPropagation: vi.fn(),
      dataTransfer: {
        items: [{ kind: 'file', type: file.type }],
        files: [file],
        setData: vi.fn(),
        getData: vi.fn(),
      },
    })
    act(() => {
      reader.onload({ target: { result: reader.result } })
    })
    global.FileReader.mockRestore()
  }

  it('caps the composer so the transcript keeps about half a short viewport', () => {
    // The budget is the contract the layout depends on. 7rem is the header
    // and compliance banner; 11rem is the message box, the control row, and
    // the footer padding; the aux panels scroll in what is left of 50vh.
    expect(css).toContain('html.atlas-short-viewport [data-testid="composer-footer"]')
    expect(css).toContain('max-height: max(4.5rem, calc(50vh - 18rem))')
    expect(css).toContain('html.atlas-short-viewport [data-testid="feedback-button"]')
    expect(css).toContain('bottom: 0.5rem !important')
  })

  it('collapses attached files, drops the duplicate count, and keeps controls reachable', () => {
    short = true
    renderChat()

    expect(document.documentElement.classList.contains('atlas-short-viewport')).toBe(true)
    const footer = screen.getByTestId('composer-footer')
    const status = within(footer).getByTestId('agent-run-status')
    expect(status).toHaveTextContent(/agent is running/i)
    expect(status).toHaveTextContent(/step 2/i)
    expect(within(status).getByText(/using tools as needed/i)).toHaveClass('sr-only')
    expect(footer.querySelector('.composer-status-row')).not.toBeNull()
    expect(within(footer).getByRole('button', { name: /stop agent run/i })).toBeInTheDocument()
    expect(within(footer).queryByRole('button', { name: 'Workspaces' })).not.toBeInTheDocument()
    expect(screen.queryByText(/file\(s\) uploaded/)).not.toBeInTheDocument()

    dropFile('notes.txt')

    const toggle = screen.getByTestId('uploaded-files-toggle')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveAttribute('aria-controls', 'uploaded-files-panel')
    expect(toggle).toHaveTextContent('1 file attached')
    expect(screen.queryByText('notes.txt')).not.toBeInTheDocument()

    fireEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    const panel = document.getElementById('uploaded-files-panel')
    expect(panel).not.toBeNull()
    expect(within(panel).getByText('notes.txt')).toBeInTheDocument()
    expect(screen.queryByText(/file\(s\) uploaded/)).not.toBeInTheDocument()
  })

  it('shows the newline hint only while the message box is focused', () => {
    short = true
    vi.useFakeTimers()
    try {
      renderChat()
      expect(screen.queryByText(/Press Shift \+ Enter/)).not.toBeInTheDocument()
      const box = screen.getByPlaceholderText(/Type a message/)
      fireEvent.focus(box)
      expect(screen.getByText(/Press Shift \+ Enter/)).toBeInTheDocument()
      fireEvent.blur(box)
      expect(screen.queryByText(/Press Shift \+ Enter/)).not.toBeInTheDocument()
    } finally {
      cleanup()
      vi.clearAllTimers()
      vi.useRealTimers()
    }
  })

  it('keeps the full composer on a tall window', () => {
    short = false
    renderChat()

    expect(document.documentElement.classList.contains('atlas-short-viewport')).toBe(false)
    expect(screen.getByRole('button', { name: 'Workspaces' })).toBeInTheDocument()
    expect(screen.getByText(/Press Shift \+ Enter/)).toBeInTheDocument()
    expect(within(screen.getByTestId('agent-run-status')).getByText(/using tools as needed/i))
      .not.toHaveClass('sr-only')
    expect(document.querySelector('.composer-status-row')).toBeNull()

    dropFile('notes.txt')

    expect(screen.queryByTestId('uploaded-files-toggle')).not.toBeInTheDocument()
    expect(screen.getByText('notes.txt')).toBeInTheDocument()
    expect(screen.queryByText(/file\(s\) uploaded/)).not.toBeInTheDocument()
  })
})
