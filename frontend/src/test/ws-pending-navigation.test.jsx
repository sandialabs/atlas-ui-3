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
    expect(sent).toBe('queued')
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

  it('keeps the frame, closes the socket and is not connected when the replay throws', () => {
    const first = FakeWebSocket.instances[0]
    const close = vi.spyOn(first, 'close')
    act(() => probe.sendMessage({ type: 'reset_session' }))
    const realSend = first.send.bind(first)
    first.send = () => {
      throw new Error('socket closing')
    }
    act(() => first.open())
    expect(probe.isConnected).toBe(false)
    expect(close).toHaveBeenCalled()

    first.send = realSend
    const second = reconnect()
    expect(second.sent).toEqual([{ type: 'reset_session' }])
  })

  it('sends a held navigation before a chat frame on an already OPEN socket', () => {
    const first = FakeWebSocket.instances[0]
    act(() => probe.sendMessage({ type: 'reset_session' }))
    const realSend = first.send.bind(first)
    first.send = () => {
      throw new Error('socket closing')
    }
    act(() => first.open())
    first.send = realSend

    let sent
    act(() => {
      sent = probe.sendMessage({ type: 'chat', content: 'hello' })
    })
    expect(sent).toBe(true)
    expect(first.sent.map((m) => m.type)).toEqual(['reset_session', 'chat'])
  })

  it('does not send a chat frame while the held navigation cannot be sent', () => {
    const first = FakeWebSocket.instances[0]
    act(() => probe.sendMessage({ type: 'reset_session' }))
    first.readyState = 1
    first.send = () => {
      throw new Error('socket closing')
    }

    let sent
    act(() => {
      sent = probe.sendMessage({ type: 'chat', content: 'hello' })
    })
    expect(sent).toBe(false)
  })

  it('drops a held navigation when the session ends', () => {
    const first = FakeWebSocket.instances[0]
    act(() => probe.sendMessage({ type: 'restore_conversation', conversation_id: 'c1', messages: [] }))
    act(() => first.onmessage({ data: JSON.stringify({ type: 'session_ended', reason: 'x' }) }))
    const second = reconnect()
    expect(second.sent).toEqual([])
  })

  it('reports failure for a frame that cannot be serialized, without queueing it', () => {
    const circular = { type: 'reset_session' }
    circular.self = circular
    let sent
    act(() => {
      sent = probe.sendMessage(circular)
    })
    expect(sent).toBe(false)
    const first = FakeWebSocket.instances[0]
    act(() => first.open())
    expect(first.sent).toEqual([])
  })

  it('drops a held frame when a newer navigation frame is sent successfully', () => {
    const first = FakeWebSocket.instances[0]
    act(() => probe.sendMessage({ type: 'reset_session' }))
    const realSend = first.send.bind(first)
    first.send = () => {
      throw new Error('socket closing')
    }
    act(() => first.open())
    first.send = realSend

    act(() => {
      probe.sendMessage({ type: 'restore_conversation', conversation_id: 'c2', messages: [] })
    })
    expect(first.sent.map((m) => m.type)).toEqual(['restore_conversation'])

    const second = reconnect()
    expect(second.sent).toEqual([])
  })

  it('queues a navigation frame when an OPEN socket throws on send', () => {
    const first = FakeWebSocket.instances[0]
    act(() => first.open())
    const realSend = first.send.bind(first)
    first.send = () => {
      throw new Error('socket closing')
    }

    let sent
    act(() => {
      sent = probe.sendMessage({ type: 'reset_session' })
    })
    expect(sent).toBe('queued')

    first.send = realSend
    first.readyState = 3
    const second = reconnect()
    expect(second.sent).toEqual([{ type: 'reset_session' }])
  })

  it('reports failure for a chat frame when an OPEN socket throws on send', () => {
    const first = FakeWebSocket.instances[0]
    act(() => first.open())
    first.send = () => {
      throw new Error('socket closing')
    }

    let sent
    act(() => {
      sent = probe.sendMessage({ type: 'chat', content: 'lost' })
    })
    expect(sent).toBe(false)
  })

  it('does not queue chat frames', () => {
    let sent
    act(() => {
      sent = probe.sendMessage({ type: 'chat', content: 'lost' })
    })
    expect(sent).toBe(false)
    const first = FakeWebSocket.instances[0]
    act(() => first.open())
    expect(first.sent).toEqual([])
  })
})
