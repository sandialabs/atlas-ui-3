/**
 * Tests for useSelections hook
 * Focus: loaded prompts should not be cleared when switching back to default prompt
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useSelections, personaSurvivesComplianceFilter } from '../hooks/chat/useSelections'

// Simple in-memory localStorage mock (per-test isolated)
const createLocalStorageMock = () => {
  let store = {}

  return {
    getItem: vi.fn(key => (key in store ? store[key] : null)),
    setItem: vi.fn((key, value) => {
      store[key] = String(value)
    }),
    removeItem: vi.fn(key => {
      delete store[key]
    }),
    clear: vi.fn(() => {
      store = {}
    }),
    // helper for tests
    _dump: () => ({ ...store }),
  }
}

describe('useSelections', () => {
  let localStorageMock

  beforeEach(() => {
    vi.clearAllMocks()
    localStorageMock = createLocalStorageMock()

    Object.defineProperty(window, 'localStorage', {
      value: localStorageMock,
      writable: true,
    })
  })

  it('keeps loaded prompts when clearing active prompt', () => {
    const { result } = renderHook(() => useSelections())

    act(() => {
      result.current.addPrompts(['server_one', 'server_two'])
    })

    act(() => {
      result.current.makePromptActive('server_one')
    })

    expect(result.current.activePromptKey).toBe('server_one')
    expect(result.current.activePrompts).toEqual(['server_one'])
    expect(result.current.selectedPrompts.has('server_one')).toBe(true)
    expect(result.current.selectedPrompts.has('server_two')).toBe(true)

    act(() => {
      result.current.clearActivePrompt()
    })

    // Default prompt active
    expect(result.current.activePromptKey).toBe(null)
    expect(result.current.activePrompts).toEqual([])

    // Loaded prompts remain available
    expect(result.current.selectedPrompts.has('server_one')).toBe(true)
    expect(result.current.selectedPrompts.has('server_two')).toBe(true)
  })

  it('snapshotSelections captures the full workspace payload', () => {
    const { result } = renderHook(() => useSelections())

    act(() => {
      result.current.addTools(['files_read'])
      result.current.addDataSources(['corpus-a'])
    })
    act(() => {
      result.current.makePromptActive('userprompt:abc')
      result.current.toggleRagEnabled()
    })

    const snapshot = result.current.snapshotSelections()
    expect(snapshot.active_prompt_key).toBe('userprompt:abc')
    expect(snapshot.selected_tools).toContain('files_read')
    expect(snapshot.selected_data_sources).toEqual(['corpus-a'])
    expect(snapshot.rag_enabled).toBe(true)
    // A user prompt is client-side only: it must not leak into MCP prompts.
    expect(snapshot.selected_prompts).not.toContain('userprompt:abc')
  })

  it('applyWorkspace replaces selections rather than merging them', () => {
    const { result } = renderHook(() => useSelections())

    act(() => {
      result.current.addTools(['stale_tool'])
      result.current.addDataSources(['stale-source'])
    })

    act(() => {
      result.current.applyWorkspace({
        active_prompt_key: 'userprompt:abc',
        selected_tools: ['files_read'],
        selected_prompts: [],
        selected_data_sources: ['corpus-a'],
        rag_enabled: true,
      })
    })

    // Leftovers from the previous context would silently widen tool/RAG access.
    expect(result.current.selectedTools.has('stale_tool')).toBe(false)
    expect(result.current.selectedTools.has('files_read')).toBe(true)
    expect(result.current.selectedDataSources.has('stale-source')).toBe(false)
    expect(result.current.selectedDataSources.has('corpus-a')).toBe(true)
    expect(result.current.activePromptKey).toBe('userprompt:abc')
    expect(result.current.ragEnabled).toBe(true)
  })

  it('applyWorkspace loads an MCP active prompt into selected prompts', () => {
    const { result } = renderHook(() => useSelections())

    act(() => {
      result.current.applyWorkspace({
        active_prompt_key: 'server_one',
        selected_tools: [],
        selected_prompts: [],
        selected_data_sources: [],
        rag_enabled: false,
      })
    })

    expect(result.current.activePromptKey).toBe('server_one')
    expect(result.current.selectedPrompts.has('server_one')).toBe(true)
  })

  it('applyWorkspace ignores a malformed config', () => {
    const { result } = renderHook(() => useSelections())

    act(() => {
      result.current.addTools(['keep_me'])
    })
    act(() => {
      result.current.applyWorkspace(null)
    })

    expect(result.current.selectedTools.has('keep_me')).toBe(true)
  })

  it('makePromptActive adds prompt to loaded prompts when missing', () => {
    const { result } = renderHook(() => useSelections())

    act(() => {
      result.current.makePromptActive('server_added_later')
    })

    expect(result.current.activePromptKey).toBe('server_added_later')
    expect(result.current.activePrompts).toEqual(['server_added_later'])
    expect(result.current.selectedPrompts.has('server_added_later')).toBe(true)
  })

  it('makePromptActive keeps a persona out of the MCP selected prompts', () => {
    const { result } = renderHook(() => useSelections())

    act(() => {
      result.current.makePromptActive('persona:code-reviewer')
    })

    expect(result.current.activePromptKey).toBe('persona:code-reviewer')
    // A persona is resolved server-side from its id; it is not an MCP prompt.
    expect(result.current.selectedPrompts.has('persona:code-reviewer')).toBe(false)
  })

  it('snapshotSelections captures a persona as the workspace active prompt', () => {
    const { result } = renderHook(() => useSelections())

    act(() => {
      result.current.makePromptActive('persona:code-reviewer')
    })

    const snapshot = result.current.snapshotSelections()
    expect(snapshot.active_prompt_key).toBe('persona:code-reviewer')
    expect(snapshot.selected_prompts).not.toContain('persona:code-reviewer')
  })

  it('applyWorkspace restores a persona without loading it as an MCP prompt', () => {
    const { result } = renderHook(() => useSelections())

    act(() => {
      result.current.applyWorkspace({
        active_prompt_key: 'persona:code-reviewer',
        selected_tools: [],
        selected_prompts: [],
        selected_data_sources: [],
        rag_enabled: false,
      })
    })

    expect(result.current.activePromptKey).toBe('persona:code-reviewer')
    expect(result.current.selectedPrompts.has('persona:code-reviewer')).toBe(false)
  })
})

describe('personaSurvivesComplianceFilter', () => {
  it('keeps everything when the filter is cleared', () => {
    expect(personaSurvivesComplianceFilter({ compliance_level: 'Internal' }, null)).toBe(true)
    expect(personaSurvivesComplianceFilter({}, null)).toBe(true)
  })

  it('keeps a persona whose level matches the new filter', () => {
    expect(personaSurvivesComplianceFilter({ compliance_level: 'Internal' }, 'Internal')).toBe(true)
  })

  it('drops a persona whose level the new filter excludes', () => {
    expect(personaSurvivesComplianceFilter({ compliance_level: 'Public' }, 'Internal')).toBe(false)
  })

  it('drops a level-less persona once a filter is active', () => {
    expect(personaSurvivesComplianceFilter({}, 'Internal')).toBe(false)
  })

  it('leaves a missing persona to the stale-key effect', () => {
    expect(personaSurvivesComplianceFilter(undefined, 'Internal')).toBe(true)
  })
})
