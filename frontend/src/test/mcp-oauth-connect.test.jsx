/**
 * Tests for the MCP OAuth connection flow in the frontend.
 *
 * The flow is a full-page navigation to an Atlas route, not a fetch, because
 * the user has to see and interact with the provider's consent screen.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useServerAuthStatus } from '../hooks/useServerAuthStatus'

describe('useServerAuthStatus - OAuth', () => {
  let originalLocation

  beforeEach(() => {
    originalLocation = window.location
    delete window.location
    window.location = { href: '', search: '', pathname: '/', hash: '' }
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ servers: [] }) })
    )
  })

  afterEach(() => {
    window.location = originalLocation
    vi.restoreAllMocks()
  })

  it('navigates to the server-specific start route', () => {
    const { result } = renderHook(() => useServerAuthStatus())

    act(() => {
      result.current.startOAuth('remote-mcp')
    })

    expect(window.location.href).toBe('/api/mcp/auth/remote-mcp/oauth/start')
  })

  it('encodes a server name so it cannot escape the path', () => {
    const { result } = renderHook(() => useServerAuthStatus())

    act(() => {
      result.current.startOAuth('weird/../name')
    })

    expect(window.location.href).toBe('/api/mcp/auth/weird%2F..%2Fname/oauth/start')
  })

  it('exposes the start URL the backend advertises in status', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            servers: [
              {
                server_name: 'remote-mcp',
                auth_type: 'oauth',
                auth_required: true,
                authenticated: false,
                oauth_start_url: '/api/mcp/auth/remote-mcp/oauth/start',
              },
            ],
          }),
      })
    )

    const { result } = renderHook(() => useServerAuthStatus())
    await act(async () => {
      await result.current.fetchAuthStatus()
    })

    await waitFor(() => {
      expect(result.current.getServerAuth('remote-mcp')?.oauth_start_url).toBe(
        '/api/mcp/auth/remote-mcp/oauth/start'
      )
    })
  })
})
