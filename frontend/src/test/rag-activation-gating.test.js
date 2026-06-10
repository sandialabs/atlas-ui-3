/**
 * Tests for RAG activation gating (GH #335, #396)
 *
 * Verifies that RAG is activated when:
 *   1. The ragEnabled toggle is on
 *   2. One or more data sources are selected
 */

import { describe, it, expect } from 'vitest'

/**
 * Pure-logic extraction of the data-source gating from ChatContext.sendChatMessage.
 * This mirrors the logic in ChatContext.jsx without needing to render the full
 * React context tree.
 */
function computeDataSourcesToSend({ ragEnabled, selectedDataSources, allRagSourceIds }) {
  const hasSelectedSources = selectedDataSources.size > 0
  const ragActivated = ragEnabled || hasSelectedSources
  return ragActivated
    ? (hasSelectedSources ? [...selectedDataSources] : allRagSourceIds)
    : []
}

describe('RAG activation gating', () => {
  const allSources = ['server:source1', 'server:source2', 'server:source3']

  it('sends selected data sources when sources are selected (even without ragEnabled toggle)', () => {
    const result = computeDataSourcesToSend({
      ragEnabled: false,
      selectedDataSources: new Set(['server:source1', 'server:source2']),
      allRagSourceIds: allSources,
    })
    expect(result).toEqual(['server:source1', 'server:source2'])
  })

  it('sends selected data sources when ragEnabled toggle is on', () => {
    const result = computeDataSourcesToSend({
      ragEnabled: true,
      selectedDataSources: new Set(['server:source1']),
      allRagSourceIds: allSources,
    })
    expect(result).toEqual(['server:source1'])
  })

  it('falls back to all sources when ragEnabled is true and none are selected', () => {
    const result = computeDataSourcesToSend({
      ragEnabled: true,
      selectedDataSources: new Set(),
      allRagSourceIds: allSources,
    })
    expect(result).toEqual(allSources)
  })

  it('returns empty when nothing is selected and RAG is not activated', () => {
    const result = computeDataSourcesToSend({
      ragEnabled: false,
      selectedDataSources: new Set(),
      allRagSourceIds: allSources,
    })
    expect(result).toEqual([])
  })
})
