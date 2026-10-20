/**
 * Issue #1091: a reconnecting socket names the server-side session it had, so
 * whichever replica receives the reconnect can resume it. The id comes from
 * the server's `connection_session` frame and lives only as long as the page.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'
import { WSProvider, useWS } from '../contexts/WSContext'

class FakeWebSocket {
  static instances = []
  static OPEN = 1
  static CONNECTING = 0
  constructor(url) {
    FakeWebSocket.instances.push(this)
    this.url = url
    this.readyState = 1
    this.onopen = null
    this.onmessage = null
    this.onclose = null
    this.onerror = null
  }
  send() {}
  close() {}
}

describe('WebSocket session resume', () => {
  let probe
  const Probe = () => {
    probe = useWS()
    return null
  }

  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    FakeWebSocket.instances = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const resumeParam = (socket) => new URL(socket.url).searchParams.get('resume_session')

  it('opts in with an empty id, then resumes the id the server assigned', () => {
    const received = []
    render(
      <WSProvider>
        <Probe />
      </WSProvider>
    )
    act(() => {
      probe.addMessageHandler((data) => received.push(data))
    })

    const first = FakeWebSocket.instances[0]
    expect(resumeParam(first)).toBe('')

    act(() => {
      first.onmessage({
        data: JSON.stringify({ type: 'connection_session', session_id: 'abc-123', resumed: false }),
      })
      first.onmessage({ data: JSON.stringify({ type: 'pong' }) })
    })
    // The session frame is transport bookkeeping, not a chat event.
    expect(received.map((m) => m.type)).toEqual(['pong'])

    act(() => {
      probe.reconnectWebSocket()
    })
    const second = FakeWebSocket.instances[1]
    expect(resumeParam(second)).toBe('abc-123')
  })
})
