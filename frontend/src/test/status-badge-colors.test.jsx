import { readFileSync } from 'node:fs'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import Message from '../components/Message'
import DataSourcesSelector from '../components/DataSourcesSelector'
import { useChat } from '../contexts/ChatContext'
import { useMarketplace } from '../contexts/MarketplaceContext'

vi.mock('../contexts/ChatContext', () => ({ useChat: vi.fn() }))
vi.mock('../contexts/MarketplaceContext', () => ({ useMarketplace: vi.fn() }))

const css = readFileSync('src/index.css', 'utf8')
const badgeClass = color => `atlas-status-${color}`

describe('theme-independent classification and log badges', () => {
  it.each([
    ['SOC2', 'red'], ['Internal', 'yellow'], ['Public', 'green'], ['Other', 'neutral'],
  ])('renders %s classification without bridged utilities', (level, color) => {
    useChat.mockReturnValue({
      ragSources: [{ id: 'docs', label: 'docs', serverName: 'rag', complianceLevel: level }],
      selectedDataSources: new Set(), features: { compliance_levels: true },
    })
    useMarketplace.mockReturnValue({ complianceLevels: [], isComplianceAccessible: () => true })
    render(<DataSourcesSelector />)
    expect(screen.getByText(level)).toHaveClass(badgeClass(color))
  })
  it.each([
    ['error', 'red'], ['critical', 'red'], ['emergency', 'red'],
    ['warning', 'yellow'], ['warn', 'yellow'], ['alert', 'orange'],
    ['info', 'blue'], ['notice', 'blue'], ['debug', 'neutral'], ['unknown', 'blue'],
  ])('renders %s log level with a fixed pair', (level, color) => {
    useChat.mockReturnValue({ appName: 'Atlas', settings: {} })
    render(<Message message={{ role: 'system', type: 'tool_log', log_level: level, content: 'Log body' }} />)
    expect(screen.getByText(level)).toHaveClass(badgeClass(color))
  })
  it.each(['red', 'yellow', 'green', 'neutral', 'orange', 'blue'])('%s uses literal, non-theme color pairs', color => {
    const rule = css.match(new RegExp(`\\.${badgeClass(color)} \\{([^}]+)\\}`))[1]
    expect(rule).toMatch(/background-color: #[0-9a-f]{6}; color: #[0-9a-f]{6};/)
    expect(rule).not.toContain('var(')
  })
})
