/**
 * Issue #1055: data classification badges must not crowd model names out of
 * the model pickers. The name gets its own line at full width; the badge sits
 * below it, compact ("UUR +2"), with the full list on hover/focus and always
 * in the row's accessible name.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import ModelSelector from '../components/ModelSelector'
import ClassificationBadge from '../components/ClassificationBadge'
import { useChat } from '../contexts/ChatContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext', () => ({
  useOptionalMarketplace: () => ({ isComplianceAccessible: () => true }),
}))
vi.mock('../hooks/useLLMAuthStatus', () => ({
  useLLMAuthStatus: () => ({
    fetchAuthStatus: vi.fn(),
    getModelAuth: () => null,
    uploadToken: vi.fn(),
    loading: false,
    error: null,
  }),
}))

const LONG_NAME = 'us.anthropic.very-long-provider-prefixed-model-name-v2:0'
const MANY = ['UUR', 'CUI', 'CUI_no_ECI_no_ITAR_no_PII']

const GATEWAY = { name: 'enterprise', display_name: 'Enterprise LiteLLM', supports_tools: true }

function setup({ models, gateways = [] }) {
  useChat.mockReturnValue({
    models,
    llmGateways: gateways,
    user: 'test@test.com',
    currentModel: null,
    setCurrentModel: vi.fn(),
    features: { compliance_levels: true },
    complianceLevelFilter: null,
  })
  render(<ModelSelector />)
  fireEvent.click(screen.getByRole('button', { name: /select chat model/i }))
}

describe('ClassificationBadge', () => {
  it('shows the first classification and a count of the rest', () => {
    render(<ClassificationBadge resource={{ allowed_data_classifications: MANY }} />)
    const badge = screen.getByTestId('classification-badge')
    expect(within(badge).getByText('UUR')).toBeInTheDocument()
    expect(within(badge).getByText('+2')).toBeInTheDocument()
  })

  it('keeps the full list available to hover, focus and screen readers', () => {
    render(<ClassificationBadge resource={{ allowed_data_classifications: MANY }} />)
    const badge = screen.getByTestId('classification-badge')
    const full = MANY.join(', ')
    expect(within(badge).getByTitle(`Allowed data classifications: ${full}`)).toBeInTheDocument()
    expect(within(badge).getByText(`Allowed data classifications: ${full}`)).toHaveClass('sr-only')
    // Swapped in by the row's `group` on hover or keyboard focus.
    const expanded = within(badge).getByText(full)
    expect(expanded).toHaveClass('hidden', 'group-hover:inline', 'group-focus-visible:inline')
    expect(within(badge).getByText('UUR').parentElement)
      .toHaveClass('group-hover:hidden', 'group-focus-visible:hidden')
  })

  it('shows no count for a single classification', () => {
    render(<ClassificationBadge resource={{ allowed_data_classifications: ['CUI'] }} />)
    expect(screen.queryByText(/^\+\d/)).toBeNull()
  })

  it('renders nothing for an undeclared resource', () => {
    const { container } = render(<ClassificationBadge resource={{ name: 'x' }} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('model picker rows', () => {
  afterEach(() => vi.restoreAllMocks())

  it('gives the model name its own unclipped line above the badge', () => {
    setup({ models: [{ name: LONG_NAME, supports_tools: true, allowed_data_classifications: MANY }] })
    const row = screen.getByRole('button', { name: n => n.startsWith(`${LONG_NAME} `) })
    expect(row).toHaveClass('group')
    const name = within(row).getByText(LONG_NAME)
    // Wraps instead of truncating, and does not share its line with the badge.
    expect(name).not.toHaveClass('truncate')
    expect(name).toHaveClass('[overflow-wrap:anywhere]')
    const badge = within(row).getByTestId('classification-badge')
    expect(name.parentElement.contains(badge)).toBe(false)
    expect(row).toHaveAccessibleName(
      `${LONG_NAME} No image input Supports tool calling Allowed data classifications: ${MANY.join(', ')}`)
  })

  it('keeps a model awaiting an API key focusable but unselectable', () => {
    useChat.mockClear()
    setup({ models: [{ name: 'keyed', api_key_source: 'user', allowed_data_classifications: MANY }] })
    const row = screen.getByRole('button', { name: /^keyed / })
    expect(row).not.toBeDisabled()
    expect(row).toHaveAttribute('aria-disabled', 'true')
    row.focus()
    expect(row).toHaveFocus()
    fireEvent.click(row)
    expect(useChat.mock.results.at(-1).value.setCurrentModel).not.toHaveBeenCalled()
  })

  it('keeps the menu inside a narrow viewport', () => {
    setup({ models: [{ name: 'm', allowed_data_classifications: MANY }] })
    const row = screen.getByRole('button', { name: /^m / })
    expect(row.closest('.overflow-y-auto')).toHaveClass('max-w-[calc(100vw-2rem)]')
  })

  describe('enterprise gateway rows', () => {
    beforeEach(() => {
      localStorage.clear()
      localStorage.setItem('chatui-gateway-last-team:test@test.com', JSON.stringify({ enterprise: 't1' }))
      global.fetch = vi.fn(async (url) => {
        const parsed = new URL(url, 'http://localhost')
        if (parsed.pathname.endsWith('/teams')) {
          return { ok: true, status: 200, json: async () => ({ teams: [{ team_id: 't1', label: 'Team One' }] }) }
        }
        return { ok: true, status: 200, json: async () => ({ models: [
          { name: `enterprise::t1::${LONG_NAME}`, model_id: LONG_NAME, label: LONG_NAME, allowed_data_classifications: MANY },
        ] }) }
      })
    })

    it('use the same compact layout as the flat list', async () => {
      setup({ models: [], gateways: [GATEWAY] })
      const name = await screen.findByText(LONG_NAME)
      const row = name.closest('button')
      expect(row).toHaveClass('group')
      expect(name).not.toHaveClass('truncate')
      const badge = within(row).getByTestId('classification-badge')
      expect(name.parentElement.contains(badge)).toBe(false)
      expect(within(badge).getByText('+2')).toBeInTheDocument()
      expect(row).toHaveAccessibleName(expect.stringContaining(`Allowed data classifications: ${MANY.join(', ')}`))
    })
  })
})
