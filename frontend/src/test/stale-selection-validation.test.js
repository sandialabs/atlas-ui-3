/**
 * Tests for stale selection validation logic (issue #269)
 * Validates that tools, prompts, and marketplace servers that no longer
 * exist in backend config are detected and removed from persisted selections.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useSelections } from '../hooks/chat/useSelections'

// In-memory localStorage mock (per-test isolated)
const createLocalStorageMock = () => {
  let store = {}
  return {
    getItem: vi.fn(key => (key in store ? store[key] : null)),
    setItem: vi.fn((key, value) => { store[key] = String(value) }),
    removeItem: vi.fn(key => { delete store[key] }),
    clear: vi.fn(() => { store = {} }),
    _dump: () => ({ ...store }),
  }
}

/**
 * Mirrors the stale tool detection logic from ChatContext.jsx.
 * Given a config tools array and a set of selected tool keys,
 * returns the keys that are stale (no longer in config).
 */
function findStaleTools(configTools, selectedToolKeys) {
  const validToolKeys = new Set(
    configTools.flatMap(server =>
      server.tools.map(tool => `${server.server}_${tool}`)
    )
  )
  return [...selectedToolKeys].filter(key => !validToolKeys.has(key))
}

/**
 * Mirrors the stale prompt detection logic from ChatContext.jsx.
 */
function findStalePrompts(configPrompts, selectedPromptKeys) {
  const validPromptKeys = new Set(
    configPrompts.flatMap(server =>
      server.prompts.map(p => `${server.server}_${p.name}`)
    )
  )
  return [...selectedPromptKeys].filter(key => !validPromptKeys.has(key))
}

/**
 * Mirrors the stale marketplace server detection logic from MarketplaceContext.jsx.
 */
function findStaleServers(configTools, configPrompts, selectedServers) {
  const toolServers = configTools.map(t => t.server)
  const promptServers = configPrompts.map(p => p.server)
  const allServers = new Set([...toolServers, ...promptServers])
  return [...selectedServers].filter(s => !allServers.has(s))
}

describe('stale tool detection', () => {
  it('detects tools from removed servers', () => {
    const configTools = [
      { server: 'canvas', tools: ['canvas'] },
    ]
    const selected = new Set(['canvas_canvas', 'calculator_add', 'calculator_multiply'])

    const stale = findStaleTools(configTools, selected)
    expect(stale).toEqual(['calculator_add', 'calculator_multiply'])
  })

  it('detects individual removed tools within an existing server', () => {
    const configTools = [
      { server: 'math', tools: ['add'] }, // 'multiply' was removed
    ]
    const selected = new Set(['math_add', 'math_multiply'])

    const stale = findStaleTools(configTools, selected)
    expect(stale).toEqual(['math_multiply'])
  })

  it('returns empty array when all selected tools are valid', () => {
    const configTools = [
      { server: 'canvas', tools: ['canvas'] },
      { server: 'math', tools: ['add', 'multiply'] },
    ]
    const selected = new Set(['canvas_canvas', 'math_add'])

    const stale = findStaleTools(configTools, selected)
    expect(stale).toEqual([])
  })

  it('returns empty array when no tools are selected', () => {
    const configTools = [
      { server: 'canvas', tools: ['canvas'] },
    ]
    const selected = new Set()

    const stale = findStaleTools(configTools, selected)
    expect(stale).toEqual([])
  })

  it('detects all tools as stale when config has no tool servers', () => {
    const configTools = []
    const selected = new Set(['math_add', 'canvas_canvas'])

    // Note: in ChatContext, the early return prevents this case,
    // but the logic itself would correctly flag them as stale
    const stale = findStaleTools(configTools, selected)
    expect(stale).toEqual(['math_add', 'canvas_canvas'])
  })

  it('handles tool names with underscores correctly', () => {
    const configTools = [
      { server: 'ui-demo', tools: ['create_form', 'submit_form'] },
    ]
    const selected = new Set(['ui-demo_create_form', 'ui-demo_delete_form'])

    const stale = findStaleTools(configTools, selected)
    expect(stale).toEqual(['ui-demo_delete_form'])
  })
})

describe('stale prompt detection', () => {
  it('detects prompts from removed servers', () => {
    const configPrompts = [
      { server: 'helper', prompts: [{ name: 'greeting' }] },
    ]
    const selected = new Set(['helper_greeting', 'removed_server_farewell'])

    const stale = findStalePrompts(configPrompts, selected)
    expect(stale).toEqual(['removed_server_farewell'])
  })

  it('detects individual removed prompts within an existing server', () => {
    const configPrompts = [
      { server: 'helper', prompts: [{ name: 'greeting' }] }, // 'farewell' removed
    ]
    const selected = new Set(['helper_greeting', 'helper_farewell'])

    const stale = findStalePrompts(configPrompts, selected)
    expect(stale).toEqual(['helper_farewell'])
  })

  it('returns empty array when all prompts valid', () => {
    const configPrompts = [
      { server: 'helper', prompts: [{ name: 'greeting' }, { name: 'farewell' }] },
    ]
    const selected = new Set(['helper_greeting'])

    const stale = findStalePrompts(configPrompts, selected)
    expect(stale).toEqual([])
  })
})

describe('stale marketplace server detection', () => {
  it('detects servers no longer in config', () => {
    const configTools = [{ server: 'canvas', tools: ['canvas'] }]
    const configPrompts = []
    const selected = new Set(['canvas', 'removed_server'])

    const stale = findStaleServers(configTools, configPrompts, selected)
    expect(stale).toEqual(['removed_server'])
  })

  it('keeps servers that appear in either tools or prompts', () => {
    const configTools = [{ server: 'tool_server', tools: ['t1'] }]
    const configPrompts = [{ server: 'prompt_server', prompts: [{ name: 'p1' }] }]
    const selected = new Set(['tool_server', 'prompt_server'])

    const stale = findStaleServers(configTools, configPrompts, selected)
    expect(stale).toEqual([])
  })

  it('detects all servers as stale when config is empty', () => {
    const stale = findStaleServers([], [], new Set(['old_server_1', 'old_server_2']))
    expect(stale).toEqual(['old_server_1', 'old_server_2'])
  })
})

describe('useSelections removeTools/removePrompts', () => {
  let localStorageMock

  beforeEach(() => {
    vi.clearAllMocks()
    localStorageMock = createLocalStorageMock()
    Object.defineProperty(window, 'localStorage', {
      value: localStorageMock,
      writable: true,
    })
  })

  it('removeTools removes only specified keys', () => {
    const { result } = renderHook(() => useSelections())

    act(() => {
      result.current.addTools(['canvas_canvas', 'math_add', 'math_multiply'])
    })

    expect(result.current.selectedTools.has('math_add')).toBe(true)
    expect(result.current.selectedTools.has('math_multiply')).toBe(true)

    act(() => {
      result.current.removeTools(['math_add', 'math_multiply'])
    })

    expect(result.current.selectedTools.has('canvas_canvas')).toBe(true)
    expect(result.current.selectedTools.has('math_add')).toBe(false)
    expect(result.current.selectedTools.has('math_multiply')).toBe(false)
  })

  it('removePrompts removes only specified keys', () => {
    const { result } = renderHook(() => useSelections())

    act(() => {
      result.current.addPrompts(['server_greeting', 'server_farewell'])
    })

    act(() => {
      result.current.removePrompts(['server_farewell'])
    })

    expect(result.current.selectedPrompts.has('server_greeting')).toBe(true)
    expect(result.current.selectedPrompts.has('server_farewell')).toBe(false)
  })

  it('clearActivePrompt resets active prompt key', () => {
    const { result } = renderHook(() => useSelections())

    act(() => {
      result.current.makePromptActive('server_greeting')
    })
    expect(result.current.activePromptKey).toBe('server_greeting')

    act(() => {
      result.current.clearActivePrompt()
    })
    expect(result.current.activePromptKey).toBe(null)
    expect(result.current.activePrompts).toEqual([])
  })

  it('removeTools with empty array is a no-op', () => {
    const { result } = renderHook(() => useSelections())

    act(() => {
      result.current.addTools(['canvas_canvas'])
    })

    act(() => {
      result.current.removeTools([])
    })

    expect(result.current.selectedTools.has('canvas_canvas')).toBe(true)
  })
})
