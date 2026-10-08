/**
 * The header's Sources toggle must describe the drawer it controls
 * (issue #1038 review): it is an expanded/collapsed dialog trigger that
 * points at the drawer with aria-controls, and the icon-only (narrow)
 * variant needs an explicit accessible name because it has no text.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'

vi.mock('../contexts/ChatContext', () => ({ useChat: vi.fn() }))
vi.mock('../contexts/WSContext', () => ({ useWS: vi.fn() }))
vi.mock('../contexts/MarketplaceContext', () => ({ useMarketplace: vi.fn() }))
vi.mock('react-router-dom', () => ({ useNavigate: vi.fn() }))
vi.mock('./WorkspaceSelector', () => ({ default: () => null }))
vi.mock('../hooks/useElementWidth', () => ({
  useElementWidth: vi.fn(() => [vi.fn(), mockedWidth])
}))
vi.mock('./ui/toastContext', () => ({
  useToast: () => ({ showToast: vi.fn(), success: vi.fn(), error: vi.fn() })
}))

import Header from '../components/Header'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'
import { useMarketplace } from '../contexts/MarketplaceContext'
import { useNavigate } from 'react-router-dom'

let mockedWidth = 1440

const setContext = ({ chatOver = {}, width = 1440 } = {}) => {
  mockedWidth = width
  useChat.mockReturnValue({
    user: 'test@test.com',
    agentModeAvailable: false,
    agentModeEnabled: false,
    setAgentModeEnabled: vi.fn(),
    saveMode: 'server',
    setSaveMode: vi.fn(),
    openChat: vi.fn(),
    openChatAsText: vi.fn(),
    messages: [],
    clearChat: vi.fn(),
    features: { rag: true },
    selectedDataSources: new Set(),
    selectedTools: new Set(),
    ...chatOver,
  })
  useWS.mockReturnValue({ isConnected: true, connectionStatus: 'connected' })
  useMarketplace.mockReturnValue({ complianceLevels: [] })
  useNavigate.mockReturnValue(vi.fn())
}

const renderHeader = (props = {}) => render(
  <Header
    ragPanelOpen={false}
    onToggleSidebar={vi.fn()}
    onToggleRag={vi.fn()}
    onToggleFiles={vi.fn()}
    onToggleCanvas={vi.fn()}
    onCloseCanvas={vi.fn()}
    onToggleSettings={vi.fn()}
    {...props}
  />
)

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(cleanup)

describe('header Sources toggle as a dialog trigger', () => {
  it('points at the drawer and reports it closed while collapsed', () => {
    setContext()
    renderHeader({ ragPanelOpen: false })

    const toggle = screen.getByRole('button', { name: 'Sources' })
    expect(toggle.getAttribute('aria-controls')).toBe('rag-drawer')
    expect(toggle.getAttribute('aria-haspopup')).toBe('dialog')
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
  })

  it('reports expanded while the drawer is open', () => {
    setContext()
    renderHeader({ ragPanelOpen: true })

    expect(screen.getByRole('button', { name: 'Sources' }).getAttribute('aria-expanded')).toBe('true')
  })

  it('keeps the source count in its accessible name while expanded', () => {
    // The visible "N sources" label IS the accessible name; an aria-label
    // that overrode it would hide the count from assistive tech.
    setContext({ chatOver: { selectedDataSources: new Set(['a:1', 'b:2']) } })
    renderHeader({ ragPanelOpen: false })

    expect(screen.getByRole('button', { name: '2 sources' })).toBeTruthy()
  })

  it('gives the icon-only variant an explicit accessible name', () => {
    // Below ACTION_LABELS_MIN_WIDTH the toggle drops its text label; the
    // title attribute alone is a weak accessible name.
    setContext({ width: 720 })
    renderHeader({ ragPanelOpen: false })

    expect(screen.getByRole('button', { name: 'Toggle Data Sources drawer' })).toBeTruthy()
  })

  it('toggles via onToggleRag when clicked', () => {
    setContext()
    const onToggleRag = vi.fn()
    renderHeader({ onToggleRag })

    fireEvent.click(screen.getByRole('button', { name: 'Sources' }))
    expect(onToggleRag).toHaveBeenCalledTimes(1)
  })
})
