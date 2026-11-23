/**
 * Issue #1217: New Chat (reset_session) or a conversation restore sent while
 * the socket is down must reach the server once it reconnects, ahead of
 * anything else, instead of being dropped.
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
    this.readyState = 0
    this.sent = []
    this.onopen = null
    this.onmessage = null
    this.onclose = null
    this.onerror = null
  }
  send(payload) {
    this.sent.push(JSON.parse(payload))
  }
  close() {}
  open() {
    this.readyState = 1
    this.onopen?.()
  }
}

describe('navigation frames sent while disconnected', () => {
  let probe
  const Probe = () => {
    probe = useWS()
    return null
  }

  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.spyOn(console, 'error').mockImplementation(() => {})
    FakeWebSocket.instances = []
    render(
      <WSProvider>
        <Probe />
      </WSProvider>
    )
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  const reconnect = () => {
    act(() => probe.reconnectWebSocket())
    const next = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    act(() => next.open())
    return next
  }

  it('delivers a reset_session sent while disconnected on the next open', () => {
    const first = FakeWebSocket.instances[0]
    act(() => first.open())
    first.readyState = 3

    let sent
    act(() => {
      sent = probe.sendMessage({ type: 'reset_session' })
    })
    expect(sent).toBe(false)
    expect(first.sent).toEqual([])

    const second = reconnect()
    expect(second.sent).toEqual([{ type: 'reset_session' }])
  })

  it('delivers only once, and before a chat frame sent after the open', () => {
    const first = FakeWebSocket.instances[0]
    act(() => probe.sendMessage({ type: 'reset_session' }))
    expect(first.sent).toEqual([])
    act(() => first.open())
    expect(first.sent).toEqual([{ type: 'reset_session' }])

    act(() => {
      probe.sendMessage({ type: 'chat', content: 'hello' })
    })
    expect(first.sent.map((m) => m.type)).toEqual(['reset_session', 'chat'])

    const second = reconnect()
    expect(second.sent).toEqual([])
  })

  it('keeps only the latest navigation frame', () => {
    act(() => {
      probe.sendMessage({ type: 'restore_conversation', conversation_id: 'c1', messages: [] })
      probe.sendMessage({ type: 'reset_session' })
    })
    const first = FakeWebSocket.instances[0]
    act(() => first.open())
    expect(first.sent).toEqual([{ type: 'reset_session' }])
  })

  it('also keeps a restore_conversation', () => {
    const frame = { type: 'restore_conversation', conversation_id: 'c1', messages: [] }
    act(() => {
      probe.sendMessage(frame)
    })
    const first = FakeWebSocket.instances[0]
    act(() => first.open())
    expect(first.sent).toEqual([frame])
  })

  it('does not queue chat frames', () => {
    act(() => {
      probe.sendMessage({ type: 'chat', content: 'lost' })
    })
    const first = FakeWebSocket.instances[0]
    act(() => first.open())
    expect(first.sent).toEqual([])
  })
})
