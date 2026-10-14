/**
 * Header compliance selector in required-level mode
 * (FEATURE_COMPLIANCE_LEVEL_REQUIRED).
 *
 * With the mode on there is no "All Levels" (no filter) choice: the selector
 * lists only defined levels. Without it the "All Levels" option stays. Both
 * copies of the selector (the header bar and the overflow menu) are checked.
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

const LEVELS = [
  { name: 'Public', description: '', aliases: [], allowed_with: ['Public'] },
  { name: 'Internal', description: '', aliases: [], allowed_with: ['Internal', 'Public'] },
]

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
    messages: [],
    clearChat: vi.fn(),
    features: { compliance_levels: true },
    complianceLevelFilter: 'Internal',
    setComplianceLevelFilter: vi.fn(),
    complianceRequired: false,
    activeConversationClassification: null,
    conversationAccepted: true,
    selectedDataSources: new Set(),
    ...over,
  })
}

const openSelectors = () => {
  render(
    <Header
      onToggleSidebar={vi.fn()}
      onToggleRag={vi.fn()}
      onToggleFiles={vi.fn()}
      onToggleCanvas={vi.fn()}
      onCloseCanvas={vi.fn()}
      onToggleSettings={vi.fn()}
    />
  )
  fireEvent.click(screen.getByTitle('Menu'))
  const selects = screen.getAllByLabelText('Compliance level')
  expect(selects).toHaveLength(2)
  return selects
}

const optionLabels = select => Array.from(select.options).map(o => o.textContent)

beforeEach(() => {
  vi.clearAllMocks()
  useWS.mockReturnValue({ isConnected: true, connectionStatus: 'connected' })
  useMarketplace.mockReturnValue({ complianceLevels: LEVELS })
  useNavigate.mockReturnValue(vi.fn())
})

afterEach(cleanup)

describe('header compliance selector', () => {
  it('offers "All Levels" when a level is not required', () => {
    setChat()
    for (const select of openSelectors()) {
      expect(optionLabels(select)).toEqual(['All Levels', 'Public', 'Internal'])
    }
  })

  it('offers only defined levels when a level is required', () => {
    setChat({ complianceRequired: true })
    for (const select of openSelectors()) {
      expect(optionLabels(select)).toEqual(['Public', 'Internal'])
      expect(select.value).toBe('Internal')
    }
  })

  it('shows a disabled placeholder, not "All Levels", before a level is set', () => {
    setChat({ complianceRequired: true, complianceLevelFilter: null })
    for (const select of openSelectors()) {
      expect(optionLabels(select)).toEqual(['Select a level', 'Public', 'Internal'])
      expect(select.options[0].disabled).toBe(true)
    }
  })

  it('switches between defined levels', () => {
    const setComplianceLevelFilter = vi.fn()
    setChat({ complianceRequired: true, setComplianceLevelFilter })
    for (const select of openSelectors()) {
      fireEvent.change(select, { target: { value: 'Public' } })
    }
    expect(setComplianceLevelFilter.mock.calls).toEqual([['Public'], ['Public']])
  })
})

describe('header compliance selector lock (issue #1044)', () => {
  it('stays enabled and changeable for a new, unbound conversation', () => {
    const setComplianceLevelFilter = vi.fn()
    setChat({ activeConversationClassification: null, setComplianceLevelFilter })
    for (const select of openSelectors()) {
      expect(select.disabled).toBe(false)
      expect(select.dataset.locked).toBe('false')
      fireEvent.change(select, { target: { value: 'Public' } })
    }
    expect(setComplianceLevelFilter.mock.calls).toEqual([['Public'], ['Public']])
  })

  it('locks a begun conversation to its recorded level, not the global value', () => {
    setChat({
      complianceLevelFilter: 'Public',
      activeConversationClassification: { state: 'classified', level: 'Internal' },
      conversationAccepted: true,
    })
    for (const select of openSelectors()) {
      expect(select.disabled).toBe(true)
      expect(select.dataset.locked).toBe('true')
      expect(select.value).toBe('Internal')
      expect(select.title).toMatch(/classified Internal/)
      expect(select.title).toMatch(/Start a new conversation/)
      // The recorded level is displayed even though the global filter differs.
      expect(optionLabels(select)).toEqual(['Public', 'Internal'])
    }
  })

  it('stays unlocked after a first turn the server has not accepted', () => {
    setChat({
      complianceLevelFilter: 'Public',
      activeConversationClassification: { state: 'classified', level: 'Internal' },
      conversationAccepted: false,
    })
    for (const select of openSelectors()) {
      expect(select.disabled).toBe(false)
      expect(select.dataset.locked).toBe('false')
      expect(select.value).toBe('Public')
    }
  })

  it('locks an unclassified conversation behind a placeholder option', () => {
    setChat({
      complianceLevelFilter: 'Internal',
      activeConversationClassification: { state: 'unclassified', level: null },
    })
    for (const select of openSelectors()) {
      expect(select.disabled).toBe(true)
      expect(select.value).toBe('')
      expect(optionLabels(select)[0]).toBe('Unclassified')
      expect(select.title).toMatch(/Start a new conversation/)
    }
  })

  it('shows a recorded level the deployment no longer defines', () => {
    setChat({
      complianceLevelFilter: null,
      activeConversationClassification: { state: 'classified', level: 'CUI' },
    })
    for (const select of openSelectors()) {
      expect(select.disabled).toBe(true)
      expect(select.value).toBe('CUI')
      expect(optionLabels(select)).toContain('CUI')
    }
  })
})
