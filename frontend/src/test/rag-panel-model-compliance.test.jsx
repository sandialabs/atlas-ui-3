/**
 * The RAG picker must not offer a source the server will exclude at query time.
 *
 * Each source carries two classification sets (issue #1032):
 *   - its per-corpus classifications (the RAG backend's discovery response),
 *     which the header compliance filter uses to decide whether a row is
 *     listed at all;
 *   - its RAG server's classifications (rag-sources.json), which the server
 *     checks against the active compliance level on every turn.
 * A corpus can pass the header filter while its server is not approved for
 * that level. The panel renders such rows disabled rather than hidden (a
 * hidden row would stay selected with no way to deselect it). `allowed_with`
 * in the level definitions no longer widens either check, and the selected
 * model no longer bounds which sources are offered.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import RagPanel from '../components/RagPanel'
import { useChat } from '../contexts/ChatContext'
import { useMarketplace } from '../contexts/MarketplaceContext'
import { isComplianceAccessible } from '../utils/complianceAccess'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext')

const COMPLIANCE_LEVELS = [
  { name: 'Public', allowed_with: ['Public'] },
  // allowed_with still lists Public, but it no longer grants access.
  { name: 'Internal', allowed_with: ['Internal', 'Public'] }
]

const RAG_SOURCES = [
  {
    id: 'public-docs',
    label: 'public-docs',
    serverName: 'atlas_rag',
    complianceLevel: 'Public',
    serverClassifications: ['Public', 'Internal']
  },
  {
    id: 'shared-docs',
    label: 'shared-docs',
    serverName: 'atlas_rag',
    allowedDataClassifications: ['Public', 'Internal'],
    serverClassifications: ['Public', 'Internal']
  },
  {
    // The corpus says Internal, but its server is only approved for Public.
    id: 'internal-docs',
    label: 'internal-docs',
    serverName: 'legacy_rag',
    complianceLevel: 'Internal',
    serverClassifications: ['Public']
  }
]

function setup({
  complianceLevelFilter = null,
  complianceLevels = COMPLIANCE_LEVELS,
  selectedDataSources = new Set(),
  ragSources = RAG_SOURCES,
  toggleDataSource = vi.fn(),
  addDataSources = vi.fn(),
  currentModel = 'public-model'
} = {}) {
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
      { name: 'internal-model', compliance_level: 'Internal' }
    ],
    currentModel
  })

  useMarketplace.mockReturnValue({
    complianceLevels,
    // The real shared rule, bound to this test's level definitions.
    isComplianceAccessible: (userLevel, classifications) =>
      isComplianceAccessible(complianceLevels, userLevel, classifications)
  })

  render(<RagPanel isOpen={true} onClose={vi.fn()} />)
}

const BOUNDARY_HINT = /Not approved for the selected compliance level/

describe('RagPanel - header compliance filter vs. server classifications', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders a listed source whose server is not approved for the level disabled, not hidden', () => {
    const toggleDataSource = vi.fn()
    setup({ complianceLevelFilter: 'Internal', toggleDataSource })

    expect(screen.getByText('shared-docs')).toBeInTheDocument()
    // internal-docs passes the header filter (its corpus is Internal) but its
    // server is not approved for Internal: it stays rendered (so a selected
    // row remains reachable) but is disabled and explains why.
    expect(screen.getByText('internal-docs')).toBeInTheDocument()
    expect(screen.getByTitle(BOUNDARY_HINT)).toBeInTheDocument()
    expect(screen.getByText(BOUNDARY_HINT)).toBeInTheDocument()

    fireEvent.click(screen.getByText('internal-docs'))
    expect(toggleDataSource).not.toHaveBeenCalled()
  })

  it('does not disable anything when no compliance level is selected', () => {
    // The selected model no longer bounds the picker: with a Public model and
    // no header filter, every source is listed and enabled.
    setup({ currentModel: 'public-model' })

    expect(screen.getByText('public-docs')).toBeInTheDocument()
    expect(screen.getByText('shared-docs')).toBeInTheDocument()
    expect(screen.getByText('internal-docs')).toBeInTheDocument()
    expect(screen.queryByTitle(BOUNDARY_HINT)).not.toBeInTheDocument()
  })

  it('lists and enables every source approved for the level at both granularities', () => {
    setup({ complianceLevelFilter: 'Public' })

    expect(screen.getByText('public-docs')).toBeInTheDocument()
    expect(screen.getByText('shared-docs')).toBeInTheDocument()
    expect(screen.queryByText('internal-docs')).not.toBeInTheDocument()
    expect(screen.queryByTitle(BOUNDARY_HINT)).not.toBeInTheDocument()
  })

  it('filters by the corpus classifications, not by allowed_with', () => {
    // Internal's allowed_with lists Public, but a Public-only corpus is no
    // longer listed under Internal; one classified for both still is.
    setup({ complianceLevelFilter: 'Internal' })

    expect(screen.queryByText('public-docs')).not.toBeInTheDocument()
    expect(screen.getByText('shared-docs')).toBeInTheDocument()
  })

  it('hides untagged sources while a header filter is active', () => {
    const untagged = { id: 'untagged-docs', label: 'untagged-docs', serverName: 'atlas_rag', serverClassifications: ['Internal'] }
    setup({ complianceLevelFilter: 'Internal', ragSources: [...RAG_SOURCES, untagged] })

    expect(screen.queryByText('untagged-docs')).not.toBeInTheDocument()
  })

  it('disables a source whose server declares no classifications under an active filter', () => {
    // An undeclared server is approved for no classified session.
    const undeclaredServer = {
      id: 'orphan-docs',
      label: 'orphan-docs',
      serverName: 'orphan_rag',
      complianceLevel: 'Internal',
      serverClassifications: null
    }
    setup({ complianceLevelFilter: 'Internal', ragSources: [undeclaredServer] })

    expect(screen.getByText('orphan-docs')).toBeInTheDocument()
    expect(screen.getByTitle(BOUNDARY_HINT)).toBeInTheDocument()
  })

  it('renders all sources when compliance levels are not loaded', () => {
    // An empty config (fetch pending or failed) must not blank the panel.
    setup({ complianceLevels: [] })

    expect(screen.getByText('public-docs')).toBeInTheDocument()
    expect(screen.getByText('internal-docs')).toBeInTheDocument()
    expect(screen.queryByTitle(BOUNDARY_HINT)).not.toBeInTheDocument()
  })

  it('enableAll skips sources whose server is not approved for the level', () => {
    const addDataSources = vi.fn()
    setup({ complianceLevelFilter: 'Internal', addDataSources })

    fireEvent.click(screen.getByRole('button', { name: /Enable All/i }))
    expect(addDataSources).toHaveBeenCalledWith(['atlas_rag:shared-docs'])
  })

  it('marks a selected out-of-boundary source as excluded', () => {
    setup({
      complianceLevelFilter: 'Internal',
      selectedDataSources: new Set(['legacy_rag:internal-docs'])
    })

    expect(screen.getByText(/selected but will not be searched/)).toBeInTheDocument()
    expect(screen.getByTitle(/Click to deselect/)).toBeInTheDocument()
  })

  it('lets a selected out-of-boundary source be deselected', () => {
    // The server's denial message tells the user to "Deselect it". Disabling
    // the row must not take that away: an out-of-boundary row that is already
    // selected stays clickable, and clicking it deselects.
    const toggleDataSource = vi.fn()
    setup({
      complianceLevelFilter: 'Internal',
      selectedDataSources: new Set(['legacy_rag:internal-docs']),
      toggleDataSource
    })

    fireEvent.click(screen.getByText('internal-docs'))
    expect(toggleDataSource).toHaveBeenCalledWith('legacy_rag:internal-docs')
  })
})
