/**
 * Tests for useSelections hook
 * Focus: loaded prompts should not be cleared when switching back to default prompt
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useSelections } from '../hooks/chat/useSelections'

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

  it('makePromptActive adds prompt to loaded prompts when missing', () => {
    const { result } = renderHook(() => useSelections())

    act(() => {
      result.current.makePromptActive('server_added_later')
    })

    expect(result.current.activePromptKey).toBe('server_added_later')
    expect(result.current.activePrompts).toEqual(['server_added_later'])
    expect(result.current.selectedPrompts.has('server_added_later')).toBe(true)
  })
})
