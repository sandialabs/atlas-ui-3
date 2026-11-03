/**
 * Phone-width composer layout.
 *
 * Under Tailwind's sm breakpoint (640px) the composer switches to a mobile
 * layout instead of the desktop row squeezed down. jsdom has no layout engine,
 * so these tests pin the contract the CSS then sizes -- one stop control, a
 * flexible message box, a one-row toolbar, the mobile block in index.css
 * winning over the short-window rules, and the viewport meta tag -- rather
 * than measuring pixels. The pixel checks were done with Playwright at
 * 360/375/390px widths.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen, cleanup, within } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ChatArea from '../components/ChatArea'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext', () => ({
  useMarketplace: () => ({ isComplianceAccessible: () => true }),
  useOptionalMarketplace: () => null,
}))
vi.mock('../contexts/WSContext')

const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8')
const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8')

const chatContext = (overrides = {}) => ({
  messages: [{ role: 'assistant', content: 'Earlier answer.' }],
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
  currentAgentStep: 1,
  agentPendingQuestion: null,
  setAgentPendingQuestion: vi.fn(),
  stopAgent: vi.fn(),
  stopStreaming: vi.fn(),
  answerAgentQuestion: vi.fn(),
  followUpSuggestions: [],
  setFollowUpSuggestions: vi.fn(),
  settings: {},
  features: { tools: true },
  workspaces: [],
  ...overrides,
})

describe('phone-width composer layout', () => {
  const originalMatchMedia = window.matchMedia

  beforeEach(() => {
    vi.clearAllMocks()
    window.matchMedia = (query) => ({
      matches: false,
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
  })

  const renderChat = () => render(
    <BrowserRouter>
      <ChatArea />
    </BrowserRouter>
  )

  // A phone's stop control must never be able to scroll away. The aux stack
  // above the message box is height-capped and scrolls on short windows, so
  // the phone's single Stop lives in the pinned composer row instead, and
  // nothing on it or its ancestors may hide it.
  const HIDING = ['hidden', 'max-sm:hidden', 'sr-only', 'invisible']
  const expectPinnedPhoneStop = () => {
    const stop = screen.getByRole('button', { name: 'Stop agent' })
    expect(stop.closest('.composer-pinned')).not.toBeNull()
    expect(stop.closest('.composer-aux')).toBeNull()
    for (let el = stop; el; el = el.parentElement) {
      for (const cls of HIDING) expect(el.classList.contains(cls)).toBe(false)
    }
    // The status row keeps its Stop for wider screens only.
    const statusStop = within(screen.getByTestId('agent-run-status'))
      .getByRole('button', { name: 'Stop agent run' })
    expect(statusStop).toHaveClass('max-sm:hidden')
    expect(statusStop.closest('.composer-aux')).not.toBeNull()
  }

  it('keeps one pinned stop control on phones with a warning above the status', () => {
    // A disconnect banner renders above the status row in the aux stack.
    useWS.mockReturnValue({ isConnected: false, connectionStatus: 'Reconnecting', sendMessage: vi.fn() })
    renderChat()

    expect(screen.getByTestId('ws-disconnected-banner')).toBeInTheDocument()
    expectPinnedPhoneStop()
  })

  it('keeps the pinned stop control while the agent waits for an answer', () => {
    useChat.mockReturnValue(chatContext({
      isAgentRunning: false,
      agentPendingQuestion: 'Which file should I use?',
    }))
    renderChat()

    expectPinnedPhoneStop()
  })

  it('lets the message box take the remaining row width', () => {
    renderChat()

    const box = screen.getByPlaceholderText(/Type a message/)
    // min-w-0 lets the flex item shrink and grow with the row instead of
    // being sized by its placeholder.
    expect(box.parentElement).toHaveClass('flex-1', 'min-w-0')
    expect(screen.getByRole('button', { name: 'Upload files' })).toHaveClass('w-12', 'h-12')
    expect(screen.getByTestId('send-button')).toHaveClass('w-12', 'h-12')
  })

  it('keeps the chat controls on one row under sm and wrapping from sm up', () => {
    renderChat()

    const controls = screen.getByRole('group', { name: 'Chat controls' })
    expect(controls).toHaveClass('composer-toolbar', 'flex-nowrap', 'sm:flex-wrap', 'min-w-0')
    // The selectors mark their wrappers so the phone CSS can open their
    // panels from the row rather than from each trigger. (The context meter
    // carries the same marker but renders nothing without usage data.)
    const triggers = {
      model: within(controls).getByRole('button', { name: /GPT-4/ }),
      tools: within(controls).getByTitle('Choose which tools the model can use'),
      prompt: within(controls).getByTitle(/^Select custom prompts/),
    }
    for (const trigger of Object.values(triggers)) {
      expect(trigger).toBeInTheDocument()
      expect(trigger.closest('.composer-toolbar-anchor')).not.toBeNull()
    }
  })

  it('places the phone blocks after the short-window block so they win', () => {
    const shortBlock = css.indexOf('@media (max-height: 950px)')
    const floatQuery = '@media (max-width: 639px), (hover: none) and (pointer: coarse) and (max-height: 950px) {'
    const floatBlock = css.indexOf(floatQuery)
    const phoneBlock = css.search(/@media \(max-width: 639px\) \{\s*\[data-testid="composer-footer"\]/)
    expect(shortBlock).toBeGreaterThan(-1)
    expect(floatBlock).toBeGreaterThan(shortBlock)
    expect(phoneBlock).toBeGreaterThan(shortBlock)

    // The feedback button never takes composer width on a phone or on a
    // short touch screen (a phone or tablet in landscape): no gutter, and the
    // button floats above the composer instead of docking inside it.
    const float = css.slice(floatBlock, phoneBlock)
    expect(float).toMatch(/\.composer-stack\s*\{\s*padding-right:\s*0 !important;/)
    expect(float).toMatch(/\[data-testid="feedback-button"\]\s*\{\s*bottom:\s*calc\(var\(--atlas-composer-height/)
    // It still clears an open side-by-side canvas, like the short-window dock.
    expect(float).toMatch(/right:\s*calc\(var\(--atlas-canvas-width, 0px\) \+ 0\.75rem\) !important/)
    // It steps aside while the user types.
    expect(float).toMatch(/textarea:focus\) \[data-testid="feedback-button"\]\s*\{\s*display:\s*none;/)
    // Right-aligned content at the button's height steps clear of it.
    expect(float).toMatch(/\.followup-chips,\s*\.welcome-powered-by\s*\{\s*padding-right:\s*3\.75rem;/)
  })

  it('caps phone toolbar panels to the room above the composer without a tall floor', () => {
    // A floor taller than the room (the old 12rem) pushed the panel top, and
    // the tools search field with it, under the header once the keyboard was
    // up. The panels scroll inside, so the cap follows the visible room.
    const phoneBlock = css.search(/@media \(max-width: 639px\) \{\s*\[data-testid="composer-footer"\]/)
    const phone = css.slice(phoneBlock)
    const cap = phone.match(/\.composer-toolbar \.composer-toolbar-popover \{[^}]*max-height:\s*([^;]+);/)
    expect(cap).not.toBeNull()
    expect(cap[1]).toContain('calc(var(--app-viewport-height, 100vh) - 8.5rem)')
    expect(cap[1]).not.toMatch(/max\(12rem/)
  })

  it('keeps the transcript padding the sticky code-block toolbar offsets by', () => {
    // #1151 pulls the sticky code-block header up by the transcript's own
    // padding (p-2 under sm, p-4 from sm) so it sits flush with the top. The
    // phone block clears the floating feedback button with the scroll
    // sentinel instead of transcript padding, so the two stay in step.
    renderChat()
    expect(document.querySelector('.chat-messages')).toHaveClass('p-2', 'sm:p-4')
    expect(css).toMatch(/\.chat-messages \.code-block-header \{\s*top: -0\.5rem;/)
    const phoneBlock = css.search(/@media \(max-width: 639px\) \{\s*\[data-testid="composer-footer"\]/)
    expect(css.slice(phoneBlock)).not.toMatch(/\.chat-messages\s*\{[^}]*padding/)
  })

  it('does not opt into edge-to-edge (viewport-fit=cover)', () => {
    // viewport-fit=cover would draw fixed overlays (header menu, sidebar
    // drawer, RAG panel, toasts) under the notch and home indicator unless
    // each one pads for the safe-area insets. Until they do, the browser's
    // default keeps the whole page inside the safe area.
    expect(html).toMatch(/name="viewport"/)
    expect(html).not.toMatch(/viewport-fit=cover/)
  })
})
