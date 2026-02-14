/**
 * Tests for RAG activation gating (GH #335)
 *
 * Verifies that selecting data sources alone does NOT trigger RAG.
 * RAG should only be invoked when explicitly activated via the search
 * button (ragEnabled toggle) or the /search command (forceRag flag).
 */

import { describe, it, expect } from 'vitest'

/**
 * Pure-logic extraction of the data-source gating from ChatContext.sendChatMessage.
 * This mirrors the logic in ChatContext.jsx without needing to render the full
 * React context tree.
 */
function computeDataSourcesToSend({ forceRag, ragEnabled, selectedDataSources, allRagSourceIds }) {
  const ragActivated = forceRag || ragEnabled
  const hasSelectedSources = selectedDataSources.size > 0
  return ragActivated
    ? (hasSelectedSources ? [...selectedDataSources] : allRagSourceIds)
    : []
}

describe('RAG activation gating', () => {
  const allSources = ['server:source1', 'server:source2', 'server:source3']

  it('does NOT send data sources when sources are selected but RAG is not activated', () => {
    const result = computeDataSourcesToSend({
      forceRag: false,
      ragEnabled: false,
      selectedDataSources: new Set(['server:source1', 'server:source2']),
      allRagSourceIds: allSources,
    })
    expect(result).toEqual([])
  })

  it('sends selected data sources when ragEnabled toggle is on', () => {
    const result = computeDataSourcesToSend({
      forceRag: false,
      ragEnabled: true,
      selectedDataSources: new Set(['server:source1']),
      allRagSourceIds: allSources,
    })
    expect(result).toEqual(['server:source1'])
  })

  it('sends selected data sources when forceRag (/search) is true', () => {
    const result = computeDataSourcesToSend({
      forceRag: true,
      ragEnabled: false,
      selectedDataSources: new Set(['server:source2']),
      allRagSourceIds: allSources,
    })
    expect(result).toEqual(['server:source2'])
  })

  it('falls back to all sources when forceRag is true and none are selected', () => {
    const result = computeDataSourcesToSend({
      forceRag: true,
      ragEnabled: false,
      selectedDataSources: new Set(),
      allRagSourceIds: allSources,
    })
    expect(result).toEqual(allSources)
  })

  it('falls back to all sources when ragEnabled is true and none are selected', () => {
    const result = computeDataSourcesToSend({
      forceRag: false,
      ragEnabled: true,
      selectedDataSources: new Set(),
      allRagSourceIds: allSources,
    })
    expect(result).toEqual(allSources)
  })

  it('returns empty when nothing is selected and RAG is not activated', () => {
    const result = computeDataSourcesToSend({
      forceRag: false,
      ragEnabled: false,
      selectedDataSources: new Set(),
      allRagSourceIds: allSources,
    })
    expect(result).toEqual([])
  })

  it('sends selected sources when both ragEnabled and forceRag are true', () => {
    const result = computeDataSourcesToSend({
      forceRag: true,
      ragEnabled: true,
      selectedDataSources: new Set(['server:source1', 'server:source3']),
      allRagSourceIds: allSources,
    })
    expect(result).toEqual(['server:source1', 'server:source3'])
  })
})
