/**
 * New Chat routing in the header.
 *
 * The desktop cluster, the compact header button, and Ctrl+Alt+N all go
 * through one handler so they clear the transcript the same way and close the
 * canvas. These tests pin that shared path (the cluster component tests cover
 * the menu wiring itself).
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
    agentModeAvailable: false,
    agentModeEnabled: false,
    setAgentModeEnabled: vi.fn(),
    saveMode: 'none',
    setSaveMode: vi.fn(),
    openChatAsText: vi.fn(),
    messages: [],
    clearChat: vi.fn(),
    features: {},
    complianceLevelFilter: null,
    setComplianceLevelFilter: vi.fn(),
    selectedDataSources: new Set(),
    ...over,
  })
}

const renderHeader = () => {
  const handlers = {
    onToggleSidebar: vi.fn(),
    onToggleRag: vi.fn(),
    onToggleFiles: vi.fn(),
    onToggleCanvas: vi.fn(),
    onCloseCanvas: vi.fn(),
    onToggleSettings: vi.fn(),
  }
  render(<Header {...handlers} />)
  return handlers
}

beforeEach(() => {
  vi.clearAllMocks()
  useWS.mockReturnValue({ isConnected: true, connectionStatus: 'connected' })
  useMarketplace.mockReturnValue({ complianceLevels: [] })
  useNavigate.mockReturnValue(vi.fn())
  setChat()
})

afterEach(cleanup)

describe('header New Chat', () => {
  it('clears the transcript and closes the canvas from the header button', () => {
    const clearChat = vi.fn()
    setChat({ clearChat })
    const handlers = renderHeader()

    fireEvent.click(screen.getAllByRole('button', { name: 'New Chat' })[0])

    expect(clearChat).toHaveBeenCalledTimes(1)
    expect(handlers.onCloseCanvas).toHaveBeenCalledTimes(1)
  })

  it('uses the same path for Ctrl+Alt+N', () => {
    const clearChat = vi.fn()
    setChat({ clearChat })
    const handlers = renderHeader()

    fireEvent.keyDown(document, { key: 'n', ctrlKey: true, altKey: true })

    expect(clearChat).toHaveBeenCalledTimes(1)
    expect(handlers.onCloseCanvas).toHaveBeenCalledTimes(1)
  })
})

// The header button opens the combined panel -- general settings, prompts and
// admin as well as tools -- so it must not share the tool picker's bare wrench.
describe('header Tools and Settings button', () => {
  it('uses the gear-and-wrench glyph and opens the combined panel', () => {
    const handlers = renderHeader()
    const [button] = screen.getAllByRole('button', { name: 'Tools and Settings' })
    expect(button.querySelector('.lucide-settings')).not.toBeNull()
    expect(button.querySelector('.lucide-wrench')).not.toBeNull()
    expect(button).toHaveAttribute('aria-haspopup', 'dialog')
    expect(button.getAttribute('title')).toMatch(/general settings/)
    fireEvent.click(button)
    expect(handlers.onToggleSettings).toHaveBeenCalledTimes(1)
  })
})
