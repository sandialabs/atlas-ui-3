/**
 * useAdminConfigActions (issue #839 review follow-up).
 *
 * The hook shapes three different request bodies per endpoint and owns the
 * notification auto-dismiss timers that both the full admin dashboard and the
 * combined panel's Admin tab rely on.
 */
import { renderHook, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAdminConfigActions } from '../hooks/useAdminConfigActions'

const okResponse = (body = { message: 'done' }) => ({
  ok: true,
  status: 200,
  json: () => Promise.resolve(body),
})

describe('useAdminConfigActions', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    global.fetch = vi.fn(() => Promise.resolve(okResponse()))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  const saveVia = async (endpoint, content) => {
    const hook = renderHook(() => useAdminConfigActions())
    act(() => {
      hook.result.current.openModal('t', content, endpoint)
    })
    await act(async () => {
      await hook.result.current.saveConfig(content)
    })
    return hook
  }

  it('sends banners as a split message list', async () => {
    await saveVia('banners', 'first\n\n  second  \nthird')

    const [url, init] = global.fetch.mock.calls[0]
    expect(url).toBe('/admin/banners')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ messages: ['first', 'second', 'third'] })
  })

  it('sends help-config as a PUT of the whole document', async () => {
    await saveVia('help-config', '# Help\n\nbody')

    const [url, init] = global.fetch.mock.calls[0]
    expect(url).toBe('/admin/help-config')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({ content: '# Help\n\nbody' })
  })

  it('sends any other endpoint as content plus an inferred file type', async () => {
    await saveVia('mcp.json', '{}')
    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({ content: '{}', file_type: 'json' })

    global.fetch.mockClear()
    await saveVia('llmconfig.yml', 'a: 1')
    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({ content: 'a: 1', file_type: 'yaml' })

    global.fetch.mockClear()
    await saveVia('notes', 'plain')
    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({ content: 'plain', file_type: 'text' })
  })

  it('reports a failed save as an error notification', async () => {
    global.fetch = vi.fn(() => Promise.resolve({
      ok: false, status: 500, json: () => Promise.resolve({ detail: 'boom' })
    }))
    const hook = await saveVia('notes', 'plain')

    expect(hook.result.current.notifications).toHaveLength(1)
    expect(hook.result.current.notifications[0]).toMatchObject({ type: 'error' })
    expect(hook.result.current.notifications[0].message).toContain('boom')
  })

  it('auto-dismisses notifications, later for errors than for successes', () => {
    const { result } = renderHook(() => useAdminConfigActions())

    act(() => {
      result.current.addNotification('ok', 'success')
      result.current.addNotification('bad', 'error')
    })
    expect(result.current.notifications).toHaveLength(2)

    act(() => { vi.advanceTimersByTime(5000) })
    expect(result.current.notifications).toHaveLength(1)
    expect(result.current.notifications[0].message).toBe('bad')

    act(() => { vi.advanceTimersByTime(3000) })
    expect(result.current.notifications).toHaveLength(0)
  })

  it('clears pending dismiss timers on unmount', () => {
    const clearSpy = vi.spyOn(global, 'clearTimeout')
    const { result, unmount } = renderHook(() => useAdminConfigActions())

    act(() => { result.current.addNotification('still up', 'info') })
    clearSpy.mockClear()
    unmount()

    // The pending auto-dismiss timer is cancelled rather than left to fire
    // against a hook that is gone.
    expect(clearSpy).toHaveBeenCalled()
  })

  it('cancels the timer when a notification is dismissed by hand', () => {
    const { result } = renderHook(() => useAdminConfigActions())

    act(() => { result.current.addNotification('bye', 'info') })
    const { id } = result.current.notifications[0]
    act(() => { result.current.removeNotification(id) })

    expect(result.current.notifications).toHaveLength(0)
    // No stale timer left behind to fire later.
    act(() => { vi.advanceTimersByTime(10000) })
    expect(result.current.notifications).toHaveLength(0)
  })

  it('does not accept a non-ok system status response', async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) }))
    const { result } = renderHook(() => useAdminConfigActions())

    await act(async () => { await result.current.loadSystemStatus() })

    expect(result.current.systemStatus).toEqual({})
  })
})
