/**
 * The header wires Agent Mode's explanation to the toggle.
 *
 * Header-level rather than component-only: the explanation must be reachable
 * from the actual toggle, and "Reduce helper text" must remove it there too.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn(),
}))
vi.mock('../contexts/WSContext', () => ({
  useWS: vi.fn(),
}))
vi.mock('../contexts/MarketplaceContext', () => ({
  useMarketplace: vi.fn(),
}))
vi.mock('react-router-dom', () => ({
  useNavigate: vi.fn(),
}))

import Header from '../components/Header'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'
import { useMarketplace } from '../contexts/MarketplaceContext'
import { useNavigate } from 'react-router-dom'

const setChat = (over = {}) => {
  useChat.mockReturnValue({
    user: 'test@test.com',
    agentModeAvailable: true,
    agentModeEnabled: true,
    setAgentModeEnabled: vi.fn(),
    saveMode: 'none',
    setSaveMode: vi.fn(),
    openChatAsText: vi.fn(),
    messages: [],
    clearChat: vi.fn(),
    features: { tools: true },
    settings: {},
    complianceLevelFilter: null,
    setComplianceLevelFilter: vi.fn(),
    selectedDataSources: new Set(),
    ...over,
  })
}

const renderHeader = () => render(
  <Header
    onToggleSidebar={vi.fn()}
    onToggleRag={vi.fn()}
    onToggleFiles={vi.fn()}
    onToggleCanvas={vi.fn()}
    onCloseCanvas={vi.fn()}
    onToggleSettings={vi.fn()}
  />
)

beforeEach(() => {
  vi.clearAllMocks()
  useWS.mockReturnValue({ isConnected: true, connectionStatus: 'connected' })
  useMarketplace.mockReturnValue({ complianceLevels: [] })
  useNavigate.mockReturnValue(vi.fn())
  setChat()
})

afterEach(cleanup)

describe('header Agent Mode explanation', () => {
  it('renders the help button next to the toggle by default', () => {
    renderHeader()
    expect(screen.getByRole('button', { name: 'What Agent Mode does' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Agent Mode: on' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('opens the plain-language explanation', () => {
    renderHeader()
    fireEvent.click(screen.getByRole('button', { name: 'What Agent Mode does' }))
    expect(screen.getByRole('dialog', { name: 'What Agent Mode does' }))
      .toHaveTextContent(/multiple steps/i)
  })

  it('drops the help button when helper text is reduced', () => {
    setChat({ settings: { reduceHelpText: true } })
    renderHeader()
    expect(screen.queryByRole('button', { name: 'What Agent Mode does' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Agent Mode: on' })).toBeInTheDocument()
  })
})
