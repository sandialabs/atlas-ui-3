/**
 * Team prompt library selection (issue #1169): key encoding, the send-time
 * decision, and labels. The send path must never let a stale or revoked
 * selection fall back to a different prompt silently.
 */
import { describe, it, expect } from 'vitest'
import {
  libraryPromptKey,
  parseLibraryPromptKey,
  isLibraryPromptKey,
  libraryDraftKey,
  isNonMcpPromptKey,
  userPromptKey,
  personaKey,
} from '../hooks/chat/useSelections'
import { resolveLibrarySelection, libraryPromptLabel } from '../utils/libraryPromptSelection'

const entry = {
  id: 'p1',
  title: 'Reviewer',
  latest_version: { id: 'v3', version_number: 3 },
}
const loaded = (entries = [entry]) => ({ loaded: true, error: null, entries })

describe('library prompt keys', () => {
  it('round-trips latest and pinned versions', () => {
    expect(libraryPromptKey('p1')).toBe('libprompt:p1@latest')
    expect(parseLibraryPromptKey('libprompt:p1@latest')).toEqual({ promptId: 'p1', versionId: null })
    expect(parseLibraryPromptKey(libraryPromptKey('p1', 'v2'))).toEqual({ promptId: 'p1', versionId: 'v2' })
    expect(parseLibraryPromptKey('userprompt:p1')).toBeNull()
    expect(parseLibraryPromptKey('libprompt:@v2')).toBeNull()
  })

  it('never treats library or draft keys as MCP prompts', () => {
    for (const key of [libraryPromptKey('p1'), libraryDraftKey('p1'), userPromptKey('u'), personaKey('x')]) {
      expect(isNonMcpPromptKey(key)).toBe(true)
    }
    expect(isNonMcpPromptKey('server_prompt')).toBe(false)
    expect(isLibraryPromptKey('server_prompt')).toBe(false)
  })
})

describe('resolveLibrarySelection', () => {
  it('sends only a reference -- null version for latest', () => {
    expect(resolveLibrarySelection(libraryPromptKey('p1'), loaded())).toEqual({
      kind: 'library',
      ref: { prompt_id: 'p1', version_id: null },
    })
    expect(resolveLibrarySelection(libraryPromptKey('p1', 'v1'), loaded()).ref).toEqual({
      prompt_id: 'p1',
      version_id: 'v1',
    })
  })

  it('blocks a selection the library no longer lists instead of falling back', () => {
    const result = resolveLibrarySelection(libraryPromptKey('gone'), loaded())
    expect(result.kind).toBe('blocked')
    expect(result.message).toMatch(/no longer available/)
  })

  it('blocks an entry that has nothing published', () => {
    const draftOnly = { ...entry, latest_version: null }
    expect(resolveLibrarySelection(libraryPromptKey('p1'), loaded([draftOnly])).kind).toBe('blocked')
  })

  it('defers to the server when the list is only a first page', () => {
    const partial = { ...loaded(), complete: false }
    expect(resolveLibrarySelection(libraryPromptKey('beyond-first-page'), partial).kind).toBe('library')
  })

  it('defers to the server while the list is unavailable', () => {
    for (const library of [{ loaded: false, entries: [] }, { loaded: true, error: 'boom', entries: [] }, null]) {
      expect(resolveLibrarySelection(libraryPromptKey('p1'), library).kind).toBe('library')
    }
  })

  it('sends a draft under test inline only while it is open', () => {
    const library = { ...loaded(), testDraft: { promptId: 'p1', content: 'draft text' } }
    expect(resolveLibrarySelection(libraryDraftKey('p1'), library)).toEqual({ kind: 'draft', content: 'draft text' })
    expect(resolveLibrarySelection(libraryDraftKey('p2'), library).kind).toBe('blocked')
    expect(resolveLibrarySelection(libraryDraftKey('p1'), loaded()).kind).toBe('blocked')
  })

  it('ignores every other key', () => {
    for (const key of [null, 'server_prompt', userPromptKey('u'), personaKey('x')]) {
      expect(resolveLibrarySelection(key, loaded())).toEqual({ kind: 'none' })
    }
  })
})

describe('libraryPromptLabel', () => {
  it('names the entry and the pinned version', () => {
    expect(libraryPromptLabel(libraryPromptKey('p1'), [entry])).toBe('Reviewer')
    expect(libraryPromptLabel(libraryPromptKey('p1', 'v3'), [entry])).toBe('Reviewer (v3)')
    expect(libraryPromptLabel(libraryPromptKey('p1', 'v1'), [entry])).toBe('Reviewer (earlier version)')
    expect(libraryPromptLabel(libraryPromptKey('gone'), [entry])).toBe('Unavailable team prompt')
    expect(libraryPromptLabel(libraryDraftKey('p1'), [entry])).toBe('Testing draft')
  })
})

describe('saved workspaces and library selections', () => {
  const memoryStorage = () => {
    let store = {}
    return {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v) },
      removeItem: k => { delete store[k] },
      clear: () => { store = {} },
    }
  }

  it('keeps a library selection in the snapshot but never a draft under test', async () => {
    const { renderHook, act } = await import('@testing-library/react')
    const { useSelections } = await import('../hooks/chat/useSelections')
    Object.defineProperty(window, 'localStorage', { value: memoryStorage(), writable: true })
    const { result } = renderHook(() => useSelections())

    act(() => { result.current.makePromptActive(libraryPromptKey('p1', 'v2')) })
    expect(result.current.snapshotSelections().active_prompt_key).toBe('libprompt:p1@v2')
    expect(result.current.selectedPrompts.size).toBe(0)

    act(() => { result.current.makePromptActive(libraryDraftKey('p1')) })
    expect(result.current.snapshotSelections().active_prompt_key).toBeNull()

    // Applying a workspace restores the library key without loading it as an MCP prompt.
    act(() => { result.current.applyWorkspace({ active_prompt_key: 'libprompt:p9@latest', selected_prompts: [] }) })
    expect(result.current.activePromptKey).toBe('libprompt:p9@latest')
    expect(result.current.selectedPrompts.size).toBe(0)
  })
})
