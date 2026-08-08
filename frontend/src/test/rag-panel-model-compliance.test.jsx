/**
 * The RAG picker must not offer a source the server will exclude at query time.
 *
 * Query-time enforcement compares each selected source against the *selected
 * model's* configured compliance level. The header filter is a separate,
 * user-chosen display filter, so a source can satisfy the header filter and
 * still sit outside the model's boundary. The panel therefore renders
 * out-of-boundary sources disabled rather than hidden (a hidden row would
 * stay selected with no way to deselect it), and it mirrors the server's
 * permissive treatment of untagged sources and missing compliance config so
 * the picker never disables something the gate would allow.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import RagPanel from '../components/RagPanel'
import { useChat } from '../contexts/ChatContext'
import { useMarketplace } from '../contexts/MarketplaceContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext')

const COMPLIANCE_LEVELS = [
  { name: 'Public', allowed_with: ['Public'] },
  { name: 'Internal', allowed_with: ['Internal', 'Public'] }
]

const RAG_SOURCES = [
  {
    id: 'public-docs',
    label: 'public-docs',
    serverName: 'atlas_rag',
    complianceLevel: 'Public',
    serverComplianceLevel: 'Public'
  },
  {
    id: 'internal-docs',
    label: 'internal-docs',
    serverName: 'atlas_rag',
    complianceLevel: 'Internal',
    serverComplianceLevel: 'Internal'
  }
]

function setup({
  currentModel,
  complianceLevelFilter = null,
  complianceLevels = COMPLIANCE_LEVELS,
  selectedDataSources = new Set(),
  ragSources = RAG_SOURCES,
  toggleDataSource = vi.fn(),
  addDataSources = vi.fn()
}) {
  useChat.mockReturnValue({
    ragSources,
    selectedDataSources,
    toggleDataSource,
    addDataSources,
    clearDataSources: vi.fn(),
    features: { compliance_levels: true },
    complianceLevelFilter,
    models: [
      { name: 'public-model', compliance_level: 'Public' },
      { name: 'internal-model', compliance_level: 'Internal' },
      { name: 'unlabelled-model' }
    ],
    currentModel
  })

  useMarketplace.mockReturnValue({
    complianceLevels,
    isComplianceAccessible: (userLevel, resourceLevel) => {
      if (!userLevel) return true
      if (!resourceLevel) return false
      const level = complianceLevels.find(l => l.name === userLevel)
      return !!level && level.allowed_with.includes(resourceLevel)
    }
  })

  render(<RagPanel isOpen={true} onClose={vi.fn()} />)
}

const BOUNDARY_HINT = /Outside the selected model's compliance boundary/

describe('RagPanel - selected model compliance boundary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders out-of-boundary sources disabled rather than hiding them', () => {
    const toggleDataSource = vi.fn()
    setup({ currentModel: 'public-model', toggleDataSource })

    expect(screen.getByText('public-docs')).toBeInTheDocument()
    // The out-of-boundary row stays rendered (so a selected row remains
    // reachable) but is disabled and explains why.
    expect(screen.getByText('internal-docs')).toBeInTheDocument()
    expect(screen.getByTitle(BOUNDARY_HINT)).toBeInTheDocument()
    expect(screen.getByText(BOUNDARY_HINT)).toBeInTheDocument()

    fireEvent.click(screen.getByText('internal-docs'))
    expect(toggleDataSource).not.toHaveBeenCalled()
  })

  it('shows every source the selected model is cleared for', () => {
    setup({ currentModel: 'internal-model' })

    expect(screen.getByText('public-docs')).toBeInTheDocument()
    expect(screen.getByText('internal-docs')).toBeInTheDocument()
    expect(screen.queryByTitle(BOUNDARY_HINT)).not.toBeInTheDocument()
  })

  it('does not filter when the selected model carries no compliance level', () => {
    // Matches the server: with no trusted level resolved, enforcement is off.
    setup({ currentModel: 'unlabelled-model' })

    expect(screen.getByText('public-docs')).toBeInTheDocument()
    expect(screen.getByText('internal-docs')).toBeInTheDocument()
    expect(screen.queryByTitle(BOUNDARY_HINT)).not.toBeInTheDocument()
  })

  it('applies the header filter and the model level together', () => {
    // Header filter narrows to Internal, hiding public-docs; internal-docs
    // passes the header filter but sits outside the Public model's boundary,
    // so it renders disabled.
    setup({ currentModel: 'public-model', complianceLevelFilter: 'Internal' })

    expect(screen.queryByText('public-docs')).not.toBeInTheDocument()
    expect(screen.getByText('internal-docs')).toBeInTheDocument()
    expect(screen.getByTitle(BOUNDARY_HINT)).toBeInTheDocument()
  })

  it('does not disable a source that carries no compliance level', () => {
    // The server gate returns early for untagged sources, so the picker must
    // not disable them either.
    const untagged = {
      id: 'untagged-docs',
      label: 'untagged-docs',
      serverName: 'atlas_rag'
    }
    setup({ currentModel: 'public-model', ragSources: [untagged] })

    expect(screen.getByText('untagged-docs')).toBeInTheDocument()
    expect(screen.queryByTitle(BOUNDARY_HINT)).not.toBeInTheDocument()
  })

  it('renders all sources when compliance levels are not loaded', () => {
    // An empty config (fetch pending or failed) must not blank the panel:
    // the server's ComplianceLevelManager is permissive in that state.
    setup({ currentModel: 'public-model', complianceLevels: [] })

    expect(screen.getByText('public-docs')).toBeInTheDocument()
    expect(screen.getByText('internal-docs')).toBeInTheDocument()
    expect(screen.queryByTitle(BOUNDARY_HINT)).not.toBeInTheDocument()
  })

  it('enableAll skips out-of-boundary sources', () => {
    const addDataSources = vi.fn()
    setup({ currentModel: 'public-model', addDataSources })

    fireEvent.click(screen.getByRole('button', { name: /Enable All/i }))
    expect(addDataSources).toHaveBeenCalledWith(['atlas_rag:public-docs'])
  })

  it('marks a selected out-of-boundary source as excluded', () => {
    setup({
      currentModel: 'public-model',
      selectedDataSources: new Set(['atlas_rag:internal-docs'])
    })

    expect(screen.getByText(/selected but will not be searched/)).toBeInTheDocument()
  })
})
