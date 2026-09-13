/**
 * Regression cover for issue #908: the transcript .json / .txt exports open in
 * a new browser tab by default instead of forcing a file download.
 *
 * These pin the header wiring for that behavior: the transcript dropdown offers
 * "Open as JSON" / "Open as Text" (not the old download labels) in both the
 * desktop dropdown and the mobile menu, and each item calls its context
 * opener. The open-in-new-tab mechanics themselves are unit tested in
 * chatExport.test.js (openBlobInNewTab).
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
    openChat: vi.fn(),
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

describe('header transcript actions (#908)', () => {
  it('offers open-as items instead of the old download labels', () => {
    renderHeader()
    fireEvent.click(screen.getByTitle('Open Chat Transcript'))
    expect(screen.getByText('Open as JSON')).toBeTruthy()
    expect(screen.getByText('Open as Text')).toBeTruthy()
    expect(screen.queryByText('Download as JSON')).toBeNull()
    expect(screen.queryByText('Download as Text')).toBeNull()
    // The print option stays in the dropdown untouched.
    expect(screen.getByText('Print / Save as PDF')).toBeTruthy()
  })

  it('routes the desktop dropdown items to the context openers', () => {
    const openChat = vi.fn()
    const openChatAsText = vi.fn()
    setChat({ openChat, openChatAsText })
    renderHeader()

    fireEvent.click(screen.getByTitle('Open Chat Transcript'))
    fireEvent.click(screen.getByText('Open as JSON'))
    expect(openChat).toHaveBeenCalledTimes(1)
    expect(openChatAsText).not.toHaveBeenCalled()

    fireEvent.click(screen.getByTitle('Open Chat Transcript'))
    fireEvent.click(screen.getByText('Open as Text'))
    expect(openChatAsText).toHaveBeenCalledTimes(1)
    expect(openChat).toHaveBeenCalledTimes(1)
  })

  it('routes the mobile menu items to the context openers', () => {
    const openChat = vi.fn()
    const openChatAsText = vi.fn()
    setChat({ openChat, openChatAsText })
    renderHeader()

    // jsdom reports header width 0, so the hamburger (compact) path is live.
    fireEvent.click(screen.getByTitle('Menu'))
    fireEvent.click(screen.getByText('Open as JSON'))
    expect(openChat).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByTitle('Menu'))
    fireEvent.click(screen.getByText('Open as Text'))
    expect(openChatAsText).toHaveBeenCalledTimes(1)
  })

  it('marks the items as opening in a new browser tab', () => {
    renderHeader()
    fireEvent.click(screen.getByTitle('Open Chat Transcript'))
    const jsonItem = screen.getByText('Open as JSON')
    expect(jsonItem.title).toBe('Opens in a new browser tab')
    expect(screen.getByText('Open as Text').title).toBe('Opens in a new browser tab')
  })
})