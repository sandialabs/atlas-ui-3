/**
 * The RAG picker must not offer a source the server will exclude at query time.
 *
 * Query-time enforcement compares each selected source against the *selected
 * model's* configured compliance level. The header filter is a separate,
 * user-chosen display filter, so a source can satisfy the header filter and
 * still sit outside the model's boundary. The panel therefore applies the
 * model's level as well.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
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
  ragSources = RAG_SOURCES
}) {
  useChat.mockReturnValue({
    ragSources,
    selectedDataSources: new Set(),
    toggleDataSource: vi.fn(),
    addDataSources: vi.fn(),
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

describe('RagPanel - selected model compliance boundary', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('hides sources outside the selected model compliance level', () => {
    setup({ currentModel: 'public-model' })

    expect(screen.getByText('public-docs')).toBeInTheDocument()
    expect(screen.queryByText('internal-docs')).not.toBeInTheDocument()
  })

  it('shows every source the selected model is cleared for', () => {
    setup({ currentModel: 'internal-model' })

    expect(screen.getByText('public-docs')).toBeInTheDocument()
    expect(screen.getByText('internal-docs')).toBeInTheDocument()
  })

  it('does not filter when the selected model carries no compliance level', () => {
    // Matches the server: with no trusted level resolved, enforcement is off.
    setup({ currentModel: 'unlabelled-model' })

    expect(screen.getByText('public-docs')).toBeInTheDocument()
    expect(screen.getByText('internal-docs')).toBeInTheDocument()
  })

  it('applies the header filter and the model level together', () => {
    // Header filter narrows to Internal; the Public model excludes it anyway.
    setup({ currentModel: 'public-model', complianceLevelFilter: 'Internal' })

    expect(screen.queryByText('internal-docs')).not.toBeInTheDocument()
    expect(screen.queryByText('public-docs')).not.toBeInTheDocument()
  })

  it('does not hide a source that carries no compliance level', () => {
    // The server gate returns early for untagged sources, so the picker must
    // not hide them either.
    const untagged = {
      id: 'untagged-docs',
      label: 'untagged-docs',
      serverName: 'atlas_rag'
    }
    setup({ currentModel: 'public-model', ragSources: [untagged] })

    expect(screen.getByText('untagged-docs')).toBeInTheDocument()
  })

  it('renders all sources when compliance levels are not loaded', () => {
    // An empty config (fetch pending or failed) must not blank the panel:
    // the server's ComplianceLevelManager is permissive in that state.
    setup({ currentModel: 'public-model', complianceLevels: [] })

    expect(screen.getByText('public-docs')).toBeInTheDocument()
    expect(screen.getByText('internal-docs')).toBeInTheDocument()
  })
})
