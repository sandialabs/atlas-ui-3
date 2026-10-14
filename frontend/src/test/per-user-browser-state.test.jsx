/**
 * Browser-stored state is per signed-in user (issue #1050): locally saved
 * conversations, the selected model, and the compliance filter of one
 * account never carry over to the next account on the same browser.
 * (The splash acceptance is covered in splash-screen.test.jsx.)
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { usePersistentState } from '../hooks/chat/usePersistentState'
import { useSelections } from '../hooks/chat/useSelections'
import { useChatConfig, resolveStartingModel } from '../hooks/chat/useChatConfig'
import * as db from '../utils/localConversationDB'
import { isKnownUser, userStorageKey } from '../utils/userStorage'

const ALICE = 'alice@example.com'
const BOB = 'bob@example.com'

// ---------------------------------------------------------------------------
// A minimal in-memory IndexedDB: enough of the API localConversationDB uses.
// ---------------------------------------------------------------------------
function createFakeIndexedDB() {
  const databases = new Map()

  const request = () => ({ onsuccess: null, onerror: null, result: undefined, error: null })

  function makeTransaction(stores, name) {
    const data = stores.get(name)
    let pending = 0
    let done = false
    const tx = { oncomplete: null, onerror: null, onabort: null, error: null }
    const finishSoon = () => {
      setTimeout(() => {
        if (done || pending > 0) return
        done = true
        tx.oncomplete?.()
      }, 0)
    }
    const issue = (fn) => {
      if (done) throw new Error('TransactionInactiveError')
      const req = request()
      pending += 1
      queueMicrotask(() => {
        if (done) return
        req.result = fn()
        req.onsuccess?.()
        pending -= 1
        if (pending === 0) finishSoon()
      })
      return req
    }
    const clone = (v) => (v === undefined ? undefined : structuredClone(v))
    tx.objectStore = () => ({
      get: (key) => issue(() => clone(data.get(key))),
      getAll: () => issue(() => [...data.values()].map(clone)),
      put: (value) => issue(() => { data.set(value.id, clone(value)); return value.id }),
      delete: (key) => issue(() => { data.delete(key) }),
      count: () => issue(() => data.size),
      clear: () => issue(() => { data.clear() }),
    })
    tx.abort = () => {
      if (done) return
      done = true
      tx.onabort?.()
    }
    finishSoon()
    return tx
  }

  return {
    open(name) {
      const req = request()
      setTimeout(() => {
        let stores = databases.get(name)
        const fresh = !stores
        if (fresh) {
          stores = new Map()
          databases.set(name, stores)
        }
        const conn = {
          objectStoreNames: { contains: (n) => stores.has(n) },
          createObjectStore: (n) => {
            stores.set(n, new Map())
            return { createIndex: () => {} }
          },
          transaction: (n) => makeTransaction(stores, n),
          close: () => {},
        }
        req.result = conn
        if (fresh) req.onupgradeneeded?.({ target: { result: conn } })
        req.onsuccess?.()
      }, 0)
      return req
    },
    // Test hook: seed a record directly, bypassing saveConversation.
    _seed(name, store, record) {
      if (!databases.has(name)) databases.set(name, new Map([[store, new Map()]]))
      databases.get(name).get(store).set(record.id, structuredClone(record))
    },
  }
}

const conv = (id, title, content = 'hello') => ({
  id,
  title,
  messages: [{ role: 'user', content }],
})

describe('userStorage', () => {
  it('treats loading/failed placeholders as no user', () => {
    expect(isKnownUser('Unknown')).toBe(false)
    expect(isKnownUser('Unauthenticated')).toBe(false)
    expect(isKnownUser('')).toBe(false)
    expect(isKnownUser(undefined)).toBe(false)
    expect(isKnownUser(ALICE)).toBe(true)
  })

  it('scopes a key to the user', () => {
    expect(userStorageKey('k', ALICE)).toBe(`k:${ALICE}`)
    expect(userStorageKey('k', BOB)).not.toBe(userStorageKey('k', ALICE))
  })
})

describe('localConversationDB per user', () => {
  let fake

  beforeEach(() => {
    fake = createFakeIndexedDB()
    vi.stubGlobal('indexedDB', fake)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('stamps the owner on save', async () => {
    const saved = await db.saveConversation(conv('c1', 'Alice chat'), ALICE)
    expect(saved.user_email).toBe(ALICE)
    expect((await db.getConversation('c1', ALICE)).user_email).toBe(ALICE)
  })

  it('refuses to save without a user', async () => {
    await expect(db.saveConversation(conv('c1', 'x'), '')).rejects.toThrow()
  })

  it('lists, opens, searches and exports only the signed-in user\'s records', async () => {
    await db.saveConversation(conv('a1', 'Alice secret', 'alpha'), ALICE)
    await db.saveConversation(conv('b1', 'Bob notes', 'alpha'), BOB)

    expect((await db.listConversations(BOB)).map(c => c.id)).toEqual(['b1'])
    expect(await db.getConversation('a1', BOB)).toBeNull()
    expect((await db.searchConversations(BOB, 'alpha')).map(c => c.id)).toEqual(['b1'])
    expect((await db.searchConversations(BOB, 'secret'))).toEqual([])
    expect((await db.exportAllConversations(BOB)).map(c => c.id)).toEqual(['b1'])
    expect((await db.listConversations(ALICE)).map(c => c.id)).toEqual(['a1'])
  })

  it('never lets one user delete another\'s records', async () => {
    await db.saveConversation(conv('a1', 'Alice 1'), ALICE)
    await db.saveConversation(conv('a2', 'Alice 2'), ALICE)
    await db.saveConversation(conv('b1', 'Bob 1'), BOB)

    expect(await db.deleteConversation('a1', BOB)).toBe(false)
    expect(await db.deleteAllConversations(BOB)).toBe(1)

    expect(await db.listConversations(BOB)).toEqual([])
    expect((await db.listConversations(ALICE)).map(c => c.id).sort()).toEqual(['a1', 'a2'])
    expect(await db.deleteConversation('a1', ALICE)).toBe(true)
    expect((await db.listConversations(ALICE)).map(c => c.id)).toEqual(['a2'])
  })

  it('will not overwrite another user\'s record with the same id', async () => {
    await db.saveConversation(conv('same', 'Alice'), ALICE)
    await expect(db.saveConversation(conv('same', 'Bob'), BOB)).rejects.toThrow()
    expect((await db.getConversation('same', ALICE)).title).toBe('Alice')
  })

  it('hides records saved before ownership was stored, without deleting them', async () => {
    // Create the database, then seed an unowned (legacy) record.
    await db.listConversations(ALICE)
    fake._seed('atlas-chat-local', 'conversations', { ...conv('legacy', 'Old chat'), updated_at: '2026-01-01' })

    expect(await db.listConversations(ALICE)).toEqual([])
    expect(await db.getConversation('legacy', ALICE)).toBeNull()
    expect(await db.deleteAllConversations(ALICE)).toBe(0)
    await expect(db.saveConversation(conv('legacy', 'claim'), ALICE)).rejects.toThrow()
  })

  it('returns nothing when no user is known', async () => {
    await db.saveConversation(conv('a1', 'Alice'), ALICE)
    expect(await db.listConversations(null)).toEqual([])
    expect(await db.getConversation('a1', null)).toBeNull()
    expect(await db.deleteAllConversations(null)).toBe(0)
    expect((await db.listConversations(ALICE)).length).toBe(1)
  })
})

describe('usePersistentState with a changing key', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => localStorage.clear())

  it('keeps a null key in memory only', () => {
    const { result } = renderHook(() => usePersistentState(null, 'init'))
    act(() => result.current[1]('changed'))
    expect(result.current[0]).toBe('changed')
    expect(localStorage.length).toBe(0)
  })

  it('re-reads the value when the key changes instead of carrying it over', () => {
    localStorage.setItem('k:alice', JSON.stringify('alice-value'))
    const { result, rerender } = renderHook(({ k }) => usePersistentState(k, null), {
      initialProps: { k: 'k:alice' },
    })
    expect(result.current[0]).toBe('alice-value')

    rerender({ k: 'k:bob' })
    expect(result.current[0]).toBeNull()
    expect(localStorage.getItem('k:bob')).toBeNull()

    act(() => result.current[1]('bob-value'))
    expect(localStorage.getItem('k:bob')).toBe(JSON.stringify('bob-value'))
    expect(localStorage.getItem('k:alice')).toBe(JSON.stringify('alice-value'))
  })
})

describe('compliance filter per user', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => localStorage.clear())

  it('does not carry one user\'s filter over to the next', () => {
    const alice = renderHook(() => useSelections(ALICE))
    act(() => alice.result.current.setComplianceLevelFilter('SECRET'))
    alice.unmount()

    const bob = renderHook(() => useSelections(BOB))
    expect(bob.result.current.complianceLevelFilter).toBeNull()
    bob.unmount()

    const aliceAgain = renderHook(() => useSelections(ALICE))
    expect(aliceAgain.result.current.complianceLevelFilter).toBe('SECRET')
  })

  it('ignores the pre-#1050 browser-wide filter', () => {
    localStorage.setItem('chatui-compliance-level-filter', JSON.stringify('SECRET'))
    const { result } = renderHook(() => useSelections(BOB))
    expect(result.current.complianceLevelFilter).toBeNull()
  })
})

describe('selected model per user', () => {
  const GATEWAYS = [{ name: 'corp' }]
  const config = (user, extra = {}) => ({
    app_name: 'Atlas',
    user,
    models: [{ name: 'gpt-4o' }, { name: 'claude-3' }],
    llm_gateways: GATEWAYS,
    features: {},
    ...extra,
  })

  let teamsResponse
  function mockFetch(cfg) {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (url.startsWith('/api/config')) return { ok: true, json: async () => cfg }
      if (url.startsWith('/api/llm/gateways/corp/teams')) return teamsResponse()
      return { ok: false, json: async () => ({}) }
    }))
  }

  beforeEach(() => {
    localStorage.clear()
    teamsResponse = () => ({ ok: true, json: async () => ({ teams: [] }) })
  })

  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('resolveStartingModel keeps an offered saved model and falls back otherwise', () => {
    const cfg = config(ALICE)
    expect(resolveStartingModel('claude-3', cfg)).toBe('claude-3')
    expect(resolveStartingModel('corp::t1::m', cfg)).toBe('corp::t1::m')
    expect(resolveStartingModel('gone', cfg)).toBe('gpt-4o')
    expect(resolveStartingModel('', cfg)).toBe('gpt-4o')
    expect(resolveStartingModel('other::t1::m', cfg)).toBe('gpt-4o')
  })

  it('starts each user on their own saved model', async () => {
    localStorage.setItem(`chatui-current-model:${ALICE}`, 'claude-3')
    mockFetch(config(BOB))
    const bob = renderHook(() => useChatConfig())
    await waitFor(() => expect(bob.result.current.currentModel).toBe('gpt-4o'))
    bob.unmount()

    mockFetch(config(ALICE))
    const alice = renderHook(() => useChatConfig())
    await waitFor(() => expect(alice.result.current.currentModel).toBe('claude-3'))
  })

  it('remembers a pick under the signed-in user only', async () => {
    mockFetch(config(ALICE))
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(result.current.currentModel).toBe('gpt-4o'))
    act(() => result.current.setCurrentModel('claude-3'))
    expect(localStorage.getItem(`chatui-current-model:${ALICE}`)).toBe('claude-3')
    expect(localStorage.getItem(`chatui-current-model:${BOB}`)).toBeNull()
    expect(localStorage.getItem('chatui-current-model')).toBeNull()
  })

  it('ignores the pre-#1050 browser-wide model', async () => {
    localStorage.setItem('chatui-current-model', 'claude-3')
    mockFetch(config(BOB))
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(result.current.currentModel).toBe('gpt-4o'))
  })

  it('drops a saved gateway model whose team the user is not in', async () => {
    localStorage.setItem(`chatui-current-model:${BOB}`, 'corp::team-a::gpt-x')
    teamsResponse = () => ({ ok: true, json: async () => ({ teams: [{ team_id: 'team-b', label: 'B' }] }) })
    mockFetch(config(BOB))
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(localStorage.getItem(`chatui-current-model:${BOB}`)).toBe('gpt-4o'))
    expect(result.current.currentModel).toBe('gpt-4o')
  })

  it('keeps a saved gateway model while the user is in its team', async () => {
    localStorage.setItem(`chatui-current-model:${BOB}`, 'corp::team-a::gpt-x')
    teamsResponse = () => ({ ok: true, json: async () => ({ teams: [{ team_id: 'team-a', label: 'A' }] }) })
    mockFetch(config(BOB))
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/llm/gateways/corp/teams'))
    await new Promise(r => setTimeout(r, 0))
    expect(result.current.currentModel).toBe('corp::team-a::gpt-x')
  })

  it('keeps a saved gateway model when membership cannot be checked', async () => {
    localStorage.setItem(`chatui-current-model:${BOB}`, 'corp::team-a::gpt-x')
    teamsResponse = () => ({ ok: false, status: 502, json: async () => ({}) })
    mockFetch(config(BOB))
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/llm/gateways/corp/teams'))
    await new Promise(r => setTimeout(r, 0))
    expect(result.current.currentModel).toBe('corp::team-a::gpt-x')
  })
})
