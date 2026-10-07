/**
 * The chat-bar model picker under the header compliance filter.
 *
 * The filter hides models outside the selected level, but the *selected*
 * model can still be one of them (a persisted choice, or no compliant model
 * existed to switch to). The button must say so rather than look normal, and
 * a list emptied by the filter must explain itself instead of rendering blank.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ModelSelector from '../components/ModelSelector'
import { useChat } from '../contexts/ChatContext'
import { isComplianceAccessible } from '../utils/complianceAccess'

const LEVELS = [
  { name: 'Public', allowed_with: ['Public'] },
  { name: 'Internal', allowed_with: ['Internal'] },
  { name: 'HIPAA', allowed_with: ['HIPAA', 'SOC2'] },
]

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext', () => ({
  useOptionalMarketplace: () => ({
    isComplianceAccessible: (user, resource) => isComplianceAccessible(LEVELS, user, resource),
  }),
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

const MODELS = [
  { name: 'public-model', compliance_level: 'Public' },
  { name: 'soc2-model', compliance_level: 'SOC2' },
  { name: 'soc2-hipaa-model', allowed_data_classifications: ['SOC2', 'HIPAA'] },
  { name: 'hipaa-model', compliance_level: 'HIPAA' },
]

function setup({ currentModel = 'public-model', complianceLevelFilter = null } = {}) {
  useChat.mockReturnValue({
    models: MODELS,
    llmGateways: [],
    user: 'test@test.com',
    currentModel,
    setCurrentModel: vi.fn(),
    features: { compliance_levels: true },
    complianceLevelFilter,
  })
  render(<ModelSelector />)
}

beforeEach(() => vi.clearAllMocks())

describe('ModelSelector - compliance filter', () => {
  it('flags a selected model outside the active level', () => {
    setup({ currentModel: 'public-model', complianceLevelFilter: 'HIPAA' })
    const button = screen.getByRole('button', { name: /Select chat model/ })
    expect(button).toHaveAttribute('aria-label', expect.stringContaining('outside the HIPAA compliance level'))
    expect(button.title).toMatch(/outside the HIPAA compliance level/)
  })

  it('does not flag a model whose classifications include the level', () => {
    setup({ currentModel: 'soc2-hipaa-model', complianceLevelFilter: 'HIPAA' })
    const button = screen.getByRole('button', { name: /Select chat model/ })
    expect(button.getAttribute('aria-label')).not.toMatch(/outside/)
  })

  it('flags a SOC2-only model under HIPAA despite allowed_with', () => {
    setup({ currentModel: 'soc2-model', complianceLevelFilter: 'HIPAA' })
    const button = screen.getByRole('button', { name: /Select chat model/ })
    expect(button).toHaveAttribute('aria-label', expect.stringContaining('outside the HIPAA compliance level'))
  })

  it('lists only the models approved for the level', () => {
    setup({ currentModel: 'hipaa-model', complianceLevelFilter: 'HIPAA' })
    fireEvent.click(screen.getByRole('button', { name: /Select chat model/ }))
    expect(screen.getByTitle('hipaa-model')).toBeInTheDocument()
    expect(screen.getByTitle('soc2-hipaa-model')).toBeInTheDocument()
    expect(screen.queryByTitle('soc2-model')).not.toBeInTheDocument()
    expect(screen.queryByTitle('public-model')).not.toBeInTheDocument()
  })

  it('explains an empty list instead of rendering nothing', () => {
    setup({ currentModel: 'public-model', complianceLevelFilter: 'Internal' })
    fireEvent.click(screen.getByRole('button', { name: /Select chat model/ }))
    expect(screen.getByText(/No models match the Internal compliance level/)).toBeInTheDocument()
  })
})
