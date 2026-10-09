/**
 * Compliance classification banner rendering (issue #1045).
 *
 * The shell banner is driven by the open conversation's recorded
 * classification, or by the selected level for a new conversation. It renders
 * only for levels that configure a banner and is never dismissible.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, cleanup } from '@testing-library/react'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn(),
}))

import ComplianceBanner from '../components/ComplianceBanner'
import { useChat } from '../contexts/ChatContext'

const LEVELS = [
  { name: 'UUR', aliases: [], allowed_with: ['UUR'], banner: { label: 'UUR', background_color: '#007A33', text_color: '#FFFFFF' } },
  {
    name: 'CUI',
    aliases: [],
    allowed_with: ['CUI'],
    banner: {
      label: 'CONTROLLED UNCLASSIFIED INFORMATION',
      background_color: '#502B85',
      text_color: '#FFFFFF',
      pattern: { type: 'edge_stripes', color: '#9871B9', width: 8, angle: 45 },
    },
  },
  { name: 'Internal', aliases: [], allowed_with: ['Internal'] },
]

const setChat = (over = {}) => {
  useChat.mockReturnValue({
    complianceEnabled: true,
    complianceLevels: LEVELS,
    activeComplianceFilter: null,
    activeConversationId: null,
    messages: [],
    activeConversationClassification: null,
    ...over,
  })
}

const banner = () => screen.queryByTestId('compliance-banner')

beforeEach(() => vi.clearAllMocks())
afterEach(cleanup)

describe('ComplianceBanner', () => {
  it('renders the selected level for a new conversation', () => {
    setChat({ activeComplianceFilter: 'UUR' })
    render(<ComplianceBanner />)

    expect(banner()).toBeTruthy()
    expect(screen.getByText('UUR')).toBeTruthy()
    expect(banner().dataset.level).toBe('UUR')
    expect(banner().dataset.unknown).toBe('false')
  })

  it('renders nothing when no level configures a banner', () => {
    setChat({
      complianceLevels: [{ name: 'Internal', aliases: [], allowed_with: ['Internal'] }],
      activeComplianceFilter: 'Internal',
    })
    render(<ComplianceBanner />)

    expect(banner()).toBeNull()
  })

  it('renders nothing when compliance levels are disabled', () => {
    setChat({ complianceEnabled: false, activeComplianceFilter: 'UUR' })
    render(<ComplianceBanner />)

    expect(banner()).toBeNull()
  })

  it('uses the recorded classification for an open conversation, not the selector', () => {
    setChat({
      activeComplianceFilter: 'UUR',
      activeConversationId: 'conv-1',
      messages: [{ role: 'user', content: 'hi' }],
      activeConversationClassification: { state: 'classified', level: 'CUI' },
    })
    render(<ComplianceBanner />)

    expect(screen.getByText('CONTROLLED UNCLASSIFIED INFORMATION')).toBeTruthy()
    expect(banner().dataset.level).toBe('CUI')
  })

  it('shows a neutral unavailable marking for an untrustworthy record', () => {
    setChat({
      activeComplianceFilter: 'UUR',
      activeConversationId: 'conv-1',
      messages: [{ role: 'user', content: 'hi' }],
      activeConversationClassification: { state: 'legacy', level: null },
    })
    render(<ComplianceBanner />)

    expect(screen.getByText('CLASSIFICATION UNAVAILABLE')).toBeTruthy()
    expect(banner().dataset.unknown).toBe('true')
    // Never silently shows the less restrictive selector level.
    expect(screen.queryByText('UUR')).toBeNull()
  })

  it('draws two bounded bands for edge stripes', () => {
    setChat({ activeComplianceFilter: 'CUI' })
    const { container } = render(<ComplianceBanner />)

    const bands = container.querySelectorAll('[aria-hidden="true"]')
    expect(bands).toHaveLength(2)
    for (const band of bands) {
      expect(band.style.backgroundImage).toContain('repeating-linear-gradient')
      expect(parseFloat(band.style.height)).toBeLessThanOrEqual(8)
    }
  })

  it('colours a solid banner and keeps the label uppercase', () => {
    setChat({ activeComplianceFilter: 'UUR' })
    render(<ComplianceBanner />)

    expect(banner().style.backgroundColor).toBe('rgb(0, 122, 51)')
    const label = screen.getByText('UUR')
    expect(label.className).toContain('uppercase')
    expect(label.className).toContain('text-[10px]')
    expect(label.className).toContain('sm:text-xs')
  })

  it('is not dismissible', () => {
    setChat({ activeComplianceFilter: 'UUR' })
    render(<ComplianceBanner />)

    expect(screen.queryByRole('button')).toBeNull()
  })
})
