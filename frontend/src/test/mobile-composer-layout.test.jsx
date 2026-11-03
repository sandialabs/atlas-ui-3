/**
 * Phone-width composer layout.
 *
 * Under Tailwind's sm breakpoint (640px) the composer switches to a mobile
 * layout instead of the desktop row squeezed down. jsdom has no layout engine,
 * so these tests pin the contract the CSS then sizes -- one stop control, a
 * flexible message box, a one-row toolbar, the mobile block in index.css
 * winning over the short-window rules, and the safe-area viewport -- rather
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

  it('shows one stop control under sm during an agent run', () => {
    renderChat()

    // The composer duplicate is hidden under sm and shown from sm up.
    const composerStop = screen.getByRole('button', { name: 'Stop agent' })
    expect(composerStop).toHaveClass('hidden', 'sm:flex')

    // The status row's labelled Stop is the phone's single stop control.
    const statusStop = within(screen.getByTestId('agent-run-status'))
      .getByRole('button', { name: 'Stop agent run' })
    expect(statusStop).not.toHaveClass('hidden')
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
    for (const name of [/GPT-4/, /tools/i, 'Select custom prompts']) {
      const trigger = within(controls).getAllByRole('button').find((b) =>
        typeof name === 'string' ? b.title === name : name.test(b.textContent))
      expect(trigger?.closest('.composer-toolbar-anchor')).not.toBeNull()
    }
  })

  it('places the phone block after the short-window block so it wins', () => {
    const shortBlock = css.indexOf('@media (max-height: 950px)')
    const phoneBlock = css.search(/@media \(max-width: 639px\) \{\s*\.app-shell/)
    expect(shortBlock).toBeGreaterThan(-1)
    expect(phoneBlock).toBeGreaterThan(shortBlock)

    const phone = css.slice(phoneBlock)
    // No feedback-button gutter on a phone; the button floats above the
    // composer instead.
    expect(phone).toMatch(/\.composer-stack\s*\{\s*padding-right:\s*0 !important;/)
    expect(phone).toMatch(/\[data-testid="feedback-button"\]\s*\{\s*bottom:\s*calc\(var\(--atlas-composer-height/)
    // The footer clears the home indicator.
    expect(phone).toMatch(/env\(safe-area-inset-bottom/)
  })

  it('opts into safe-area insets in the viewport meta tag', () => {
    expect(html).toMatch(/name="viewport"[^>]*viewport-fit=cover/)
  })
})
