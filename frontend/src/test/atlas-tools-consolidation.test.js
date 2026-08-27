import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useSelections } from '../hooks/chat/useSelections'
import {
  CANVAS_TOOL,
  SEARCH_TOOL,
  SLEEP_TOOL,
  isCanvasTool,
  migrateToolName,
  migrateToolNames,
} from '../constants/atlasTools'

// Issue #855: canvas, sleep and search became one built-in `atlas` server.
// Selections live in localStorage, so the old names have to keep resolving.
describe('built-in ATLAS tool names', () => {
  it('migrates every pre-consolidation name', () => {
    expect(migrateToolName('canvas_canvas')).toBe(CANVAS_TOOL)
    expect(migrateToolName('atlas_agent_sleep')).toBe(SLEEP_TOOL)
    expect(migrateToolName('atlas_rag_query')).toBe(SEARCH_TOOL)
    expect(migrateToolName('atlas_rag_discover_data_sources')).toBe(SEARCH_TOOL)
  })

  it('leaves MCP tool names alone', () => {
    expect(migrateToolName('pptx_generator_create')).toBe('pptx_generator_create')
    expect(migrateToolName(undefined)).toBe(undefined)
  })

  it('de-dupes names that migrate onto the same tool', () => {
    expect(migrateToolNames(['atlas_rag_query', 'atlas_rag_discover_data_sources']))
      .toEqual([SEARCH_TOOL])
  })

  it('preserves order and non-array input', () => {
    expect(migrateToolNames(['math_add', 'canvas_canvas'])).toEqual(['math_add', CANVAS_TOOL])
    expect(migrateToolNames(null)).toBe(null)
  })

  it('recognises the canvas tool under either name', () => {
    expect(isCanvasTool('canvas_canvas')).toBe(true)
    expect(isCanvasTool(CANVAS_TOOL)).toBe(true)
    expect(isCanvasTool('math_add')).toBe(false)
  })
})

describe('useSelections built-in tool migration', () => {
  const createLocalStorageMock = () => {
    let store = {}
    return {
      getItem: vi.fn(key => (key in store ? store[key] : null)),
      setItem: vi.fn((key, value) => { store[key] = String(value) }),
      removeItem: vi.fn(key => { delete store[key] }),
      clear: vi.fn(() => { store = {} }),
      _seed: (key, value) => { store[key] = JSON.stringify(value) },
    }
  }

  let localStorageMock

  beforeEach(() => {
    vi.clearAllMocks()
    localStorageMock = createLocalStorageMock()
    Object.defineProperty(window, 'localStorage', {
      value: localStorageMock,
      writable: true,
    })
  })

  it('defaults to the consolidated canvas tool', () => {
    const { result } = renderHook(() => useSelections())

    expect([...result.current.selectedTools]).toEqual([CANVAS_TOOL])
  })

  it('upgrades a selection persisted before the consolidation', () => {
    localStorageMock._seed('chatui-selected-tools', ['canvas_canvas', 'math_add'])

    const { result } = renderHook(() => useSelections())

    expect(result.current.selectedTools.has(CANVAS_TOOL)).toBe(true)
    expect(result.current.selectedTools.has('canvas_canvas')).toBe(false)
    expect(result.current.selectedTools.has('math_add')).toBe(true)
  })
})
