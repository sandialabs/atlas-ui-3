/**
 * The built-in search tool's automatic state (issue #1102).
 *
 * Picking a data source implies the search tool: the server offers it for the
 * very same reason, so the panels and the model always agree. These are the
 * pure rules behind that; the components that use them are covered in
 * search-tool-auto-enable.test.jsx.
 */

import { describe, it, expect } from 'vitest'
import {
  ATLAS_SEARCH_TOOL,
  SEARCH_TOOL_KEY,
  searchToolAutoEnabled,
  searchToolOffered,
  sourcesInPlay
} from '../utils/searchToolAvailability'

const catalogue = (tools = ['canvas', ATLAS_SEARCH_TOOL]) => [
  { server: 'atlas', tools, tools_detailed: [] }
]

describe('searchToolOffered', () => {
  it('is offered when the built-in server advertises the search tool', () => {
    expect(searchToolOffered(catalogue())).toBe(true)
  })

  it('is not offered when the deployment leaves it out', () => {
    // The backend drops the search tool from the catalogue when the RAG
    // tools feature is off, so the catalogue is the authority.
    expect(searchToolOffered(catalogue(['canvas']))).toBe(false)
  })

  it('is not offered when there is no catalogue at all', () => {
    expect(searchToolOffered(undefined)).toBe(false)
    expect(searchToolOffered([])).toBe(false)
  })

  it('ignores another server that happens to have a search tool', () => {
    expect(searchToolOffered([{ server: 'other', tools: ['search'] }])).toBe(false)
  })
})

describe('sourcesInPlay', () => {
  it('counts hand-picked sources', () => {
    expect(sourcesInPlay({
      selectedDataSources: new Set(['srv:doc']),
      ragEnabled: false,
      reachableSourceCount: 0
    })).toBe(true)
  })

  it('counts the RAG toggle, which means "search my data"', () => {
    expect(sourcesInPlay({
      selectedDataSources: new Set(),
      ragEnabled: true,
      reachableSourceCount: 2
    })).toBe(true)
  })

  it('counts nothing without sources', () => {
    expect(sourcesInPlay({
      selectedDataSources: new Set(),
      ragEnabled: false,
      reachableSourceCount: 2
    })).toBe(false)
    expect(sourcesInPlay({
      selectedDataSources: new Set(),
      ragEnabled: true,
      reachableSourceCount: 0
    })).toBe(false)
  })
})

describe('searchToolAutoEnabled', () => {
  it('is on when sources are selected and the tool is offered', () => {
    expect(searchToolAutoEnabled({
      tools: catalogue(),
      selectedDataSources: new Set(['srv:doc']),
      ragEnabled: false,
      reachableSourceCount: 0
    })).toBe(true)
  })

  it('is off once every source is deselected', () => {
    expect(searchToolAutoEnabled({
      tools: catalogue(),
      selectedDataSources: new Set(),
      ragEnabled: false,
      reachableSourceCount: 3
    })).toBe(false)
  })

  it('is off when the deployment does not offer the tool', () => {
    expect(searchToolAutoEnabled({
      tools: catalogue(['canvas']),
      selectedDataSources: new Set(['srv:doc']),
      ragEnabled: false,
      reachableSourceCount: 0
    })).toBe(false)
  })

  it('keys the built-in tool by server and name, not by name alone', () => {
    // Another provider may have a tool called "search"; it is not the
    // built-in and must not be locked on.
    const tools = [
      { server: 'atlas', tools: [ATLAS_SEARCH_TOOL] },
      { server: 'other', tools: ['search'] }
    ]
    expect(SEARCH_TOOL_KEY).toBe('atlas_search')
    expect(searchToolAutoEnabled({
      tools,
      selectedDataSources: new Set(['srv:doc']),
      ragEnabled: false,
      reachableSourceCount: 0
    })).toBe(true)
  })
})
