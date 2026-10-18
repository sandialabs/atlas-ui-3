/**
 * Regression cover for the transcript export/open controls.
 *
 * Plain text is the primary export, followed by Print / Save as PDF. The JSON
 * export was removed from this UI; the context still has its JSON exporter for
 * other callers, but the header must not offer it. These pin both the desktop
 * dropdown and the compact menu, and keep the menu semantics intact.
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
    saveMode: 'server',
    setSaveMode: vi.fn(),
    openChatAsText: vi.fn(),
    // Non-empty: the transcript controls are disabled while the chat is empty.
    messages: [{ role: 'user', content: 'hello', timestamp: '2026-09-13T00:00:00Z' }],
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
  const view = render(<Header {...handlers} />)
  return { ...view, handlers }
}

beforeEach(() => {
  vi.clearAllMocks()
  useWS.mockReturnValue({ isConnected: true, connectionStatus: 'connected' })
  useMarketplace.mockReturnValue({ complianceLevels: [] })
  useNavigate.mockReturnValue(vi.fn())
  setChat()
})

afterEach(cleanup)

describe('header transcript actions', () => {
  it('offers plain text first, then Print / Save as PDF, and no JSON export', () => {
    renderHeader()
    fireEvent.click(screen.getByTitle('Open Chat Transcript'))

    const items = screen.getAllByRole('menuitem')
    expect(items.map(item => item.textContent.trim())).toEqual([
      'Open as plain text',
      'Print / Save as PDF',
    ])
    expect(screen.queryByText(/Open as JSON/i)).toBeNull()
    expect(screen.queryByText(/Download as JSON/i)).toBeNull()
  })

  it('routes the desktop plain-text item to the text opener', () => {
    const openChatAsText = vi.fn()
    setChat({ openChatAsText })
    renderHeader()

    fireEvent.click(screen.getByTitle('Open Chat Transcript'))
    fireEvent.click(screen.getByText('Open as plain text'))
    expect(openChatAsText).toHaveBeenCalledTimes(1)
  })

  it('routes the compact menu plain-text item to the text opener', () => {
    const openChatAsText = vi.fn()
    setChat({ openChatAsText })
    renderHeader()

    // jsdom reports header width 0, so the hamburger (compact) path is live.
    fireEvent.click(screen.getByTitle('Menu'))
    fireEvent.click(screen.getByText('Open as plain text'))
    expect(openChatAsText).toHaveBeenCalledTimes(1)
  })

  it('marks the plain-text item as opening in a new browser tab', () => {
    renderHeader()
    fireEvent.click(screen.getByTitle('Open Chat Transcript'))
    expect(screen.getByText('Open as plain text').title)
      .toBe('Opens a plain-text transcript in a new browser tab')
  })

  it('exposes the dropdown to assistive tech as a menu', () => {
    renderHeader()
    const toggle = screen.getByTitle('Open Chat Transcript')
    expect(toggle.getAttribute('aria-haspopup')).toBe('menu')
    expect(toggle.getAttribute('aria-expanded')).toBe('false')

    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    const textItem = screen.getByText('Open as plain text')
    expect(textItem.closest('[role="menu"]')).toBeTruthy()
    expect(textItem.getAttribute('role')).toBe('menuitem')
    expect(screen.getByText('Print / Save as PDF').getAttribute('role')).toBe('menuitem')
  })
})
