/**
 * Issue #1223: navigation frames (reset_session / restore_conversation) sent
 * while the socket is down are held, replayed on the next open, and kept until
 * the server acknowledges them. Also covers the reconnect hardening around the
 * same socket lifecycle (stale handlers, backoff, early reconnect triggers).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'
import { WSProvider, useWS, SEND_QUEUED } from '../contexts/WSContext'

// A socket that behaves like the browser's: starts CONNECTING, close() moves it
// through CLOSING and the close event arrives separately (finishClose).
class FakeWebSocket {
  static instances = []
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  constructor(url) {
    FakeWebSocket.instances.push(this)
    this.url = url
    this.readyState = FakeWebSocket.CONNECTING
    this.sent = []
    this.throwOnSend = false
    this.onopen = null
    this.onmessage = null
    this.onclose = null
    this.onerror = null
  }
  send(payload) {
    if (this.readyState === FakeWebSocket.CONNECTING) throw new Error('InvalidStateError')
    if (this.throwOnSend) throw new Error('send failed')
    this.sent.push(JSON.parse(payload))
  }
  close() {
    if (this.readyState >= FakeWebSocket.CLOSING) return
    this.readyState = FakeWebSocket.CLOSING
  }
  open() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }
  finishClose(code = 1006, reason = '') {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({ code, reason })
  }
  receive(frame) {
    this.onmessage?.({ data: JSON.stringify(frame) })
  }
}

const last = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
const types = (socket) => socket.sent.map((m) => m.type)

describe('WSContext navigation frames', () => {
  let probe
  let received
  const Probe = () => {
    probe = useWS()
    return null
  }

  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true })))
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    FakeWebSocket.instances = []
    received = []
    render(
      <WSProvider>
        <Probe />
      </WSProvider>
    )
    act(() => {
      probe.addMessageHandler((data) => received.push(data))
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  // Drop the current socket the way the network does, then reconnect.
  const dropAndReconnect = () => {
    act(() => last().finishClose(1006))
    act(() => probe.reconnectWebSocket())
    const next = last()
    act(() => next.open())
    return next
  }

  const openFirst = () => {
    act(() => last().open())
    return last()
  }

  describe('holding and replay', () => {
    it('holds a reset_session sent on a closed socket and replays it on the next open', () => {
      const first = openFirst()
      act(() => first.finishClose(1006))
      let sent
      act(() => {
        sent = probe.sendMessage({ type: 'reset_session' })
      })
      expect(sent).toBe(SEND_QUEUED)
      expect(first.sent).toEqual([])

      act(() => probe.reconnectWebSocket())
      const second = last()
      act(() => second.open())
      expect(second.sent).toHaveLength(1)
      expect(second.sent[0]).toMatchObject({ type: 'reset_session' })
      expect(second.sent[0].request_id).toEqual(expect.any(String))
    })

    it('holds a frame sent while CONNECTING and refuses chat until the server confirms it', () => {
      // Socket is CONNECTING: send() would throw.
      const first = last()
      let sent
      act(() => {
        sent = probe.sendMessage({ type: 'reset_session' })
      })
      expect(sent).toBe(SEND_QUEUED)
      expect(first.sent).toEqual([])
      act(() => first.open())
      expect(probe.navigationState()).toBe('pending')
      act(() => {
        sent = probe.sendMessage({ type: 'chat', content: 'hi' })
      })
      expect(sent).toBe(false)
      expect(types(first)).toEqual(['reset_session'])

      act(() => first.receive({ type: 'session_reset', request_id: first.sent[0].request_id }))
      expect(probe.navigationState()).toBe(null)
      act(() => {
        sent = probe.sendMessage({ type: 'chat', content: 'hi' })
      })
      expect(sent).toBe(true)
      expect(types(first)).toEqual(['reset_session', 'chat'])
    })

    it('does not queue ordinary frames', () => {
      let sent
      act(() => {
        sent = probe.sendMessage({ type: 'chat', content: 'hi' })
      })
      expect(sent).toBe(false)
      const first = openFirst()
      expect(first.sent).toEqual([])
    })

    it('keeps only the latest navigation frame', () => {
      act(() => {
        probe.sendMessage({ type: 'restore_conversation', conversation_id: 'c1', messages: [] })
        probe.sendMessage({ type: 'reset_session' })
      })
      const first = openFirst()
      expect(types(first)).toEqual(['reset_session'])
    })

    it('sends the held frame, not the chat, in the window before onopen runs', () => {
      const first = last()
      act(() => {
        probe.sendMessage({ type: 'reset_session' })
      })
      // The socket reports OPEN, but its open handler has not run yet.
      first.readyState = FakeWebSocket.OPEN
      let sent
      act(() => {
        sent = probe.sendMessage({ type: 'chat', content: 'hi' })
      })
      expect(sent).toBe(false)
      expect(types(first)).toEqual(['reset_session'])
      act(() => first.onopen())
      expect(types(first)).toEqual(['reset_session'])
    })

    it('refuses chat after a refused navigation until a new one is confirmed', () => {
      act(() => {
        probe.sendMessage({ type: 'restore_conversation', conversation_id: 'c1', messages: [] })
      })
      const first = openFirst()
      act(() => first.receive({ type: 'error', message: 'refused', request_id: first.sent[0].request_id }))
      expect(probe.navigationState()).toBe('failed')
      let sent
      act(() => {
        sent = probe.sendMessage({ type: 'chat', content: 'hi' })
      })
      expect(sent).toBe(false)

      act(() => {
        probe.sendMessage({ type: 'reset_session' })
      })
      act(() => first.receive({ type: 'session_reset', request_id: first.sent[first.sent.length - 1].request_id }))
      act(() => {
        sent = probe.sendMessage({ type: 'chat', content: 'hi' })
      })
      expect(sent).toBe(true)
    })

    it('refuses chat after an unconfirmed navigation timed out', () => {
      act(() => {
        probe.sendMessage({ type: 'reset_session' })
      })
      openFirst()
      act(() => {
        vi.advanceTimersByTime(15000)
      })
      expect(probe.navigationState()).toBe('failed')
      let sent
      act(() => {
        sent = probe.sendMessage({ type: 'chat', content: 'hi' })
      })
      expect(sent).toBe(false)
    })

    it('refuses a chat frame while the held navigation cannot be sent', () => {
      const first = last()
      act(() => {
        probe.sendMessage({ type: 'reset_session' })
      })
      first.readyState = FakeWebSocket.OPEN
      first.throwOnSend = true
      let sent
      act(() => {
        sent = probe.sendMessage({ type: 'chat', content: 'hi' })
      })
      expect(sent).toBe(false)
      expect(first.readyState).toBeGreaterThanOrEqual(FakeWebSocket.CLOSING)
    })

    it('holds a navigation frame when an OPEN socket throws, and replays it on the next socket', () => {
      const first = openFirst()
      first.throwOnSend = true
      let sent
      act(() => {
        sent = probe.sendMessage({ type: 'reset_session' })
      })
      expect(sent).toBe(SEND_QUEUED)
      act(() => first.finishClose(1006))
      act(() => probe.reconnectWebSocket())
      act(() => last().open())
      expect(types(last())).toEqual(['reset_session'])
    })

    it('does not report connected when the replay on open throws, and retries on the next socket', () => {
      act(() => {
        probe.sendMessage({ type: 'reset_session' })
      })
      const first = last()
      first.throwOnSend = true
      act(() => first.open())
      expect(probe.isConnected).toBe(false)
      expect(first.readyState).toBeGreaterThanOrEqual(FakeWebSocket.CLOSING)

      act(() => first.finishClose(1006))
      act(() => probe.reconnectWebSocket())
      const second = last()
      act(() => second.open())
      expect(probe.isConnected).toBe(true)
      expect(types(second)).toEqual(['reset_session'])
    })
  })

  describe('acknowledgement', () => {
    const ackFor = (socket) => {
      const frame = socket.sent[socket.sent.length - 1]
      return frame.request_id
    }

    it('keeps the frame until acknowledged and replays it on a reconnect before the ack', () => {
      act(() => {
        probe.sendMessage({ type: 'reset_session' })
      })
      const first = openFirst()
      expect(types(first)).toEqual(['reset_session'])

      // The socket dies before the ack arrives: the frame is replayed.
      const second = dropAndReconnect()
      expect(types(second)).toEqual(['reset_session'])
      expect(second.sent[0].request_id).toBe(first.sent[0].request_id)
    })

    it('clears the frame on session_reset and does not replay it again', () => {
      act(() => {
        probe.sendMessage({ type: 'reset_session' })
      })
      const first = openFirst()
      act(() => first.receive({ type: 'session_reset', request_id: ackFor(first) }))
      const second = dropAndReconnect()
      expect(second.sent).toEqual([])
    })

    it('clears a restore_conversation on conversation_restored for the same conversation', () => {
      act(() => {
        probe.sendMessage({ type: 'restore_conversation', conversation_id: 'c1', messages: [{ role: 'user', content: 'x' }] })
      })
      const first = openFirst()
      act(() => first.receive({ type: 'conversation_restored', conversation_id: 'c1', request_id: ackFor(first) }))
      expect(dropAndReconnect().sent).toEqual([])
    })

    it('accepts an ack that carries no request id (older server) by type and conversation', () => {
      act(() => {
        probe.sendMessage({ type: 'restore_conversation', conversation_id: 'c1', messages: [] })
      })
      const first = openFirst()
      act(() => first.receive({ type: 'conversation_restored', conversation_id: 'other' }))
      expect(types(dropAndReconnect())).toEqual(['restore_conversation'])
      act(() => last().receive({ type: 'conversation_restored', conversation_id: 'c1' }))
      expect(dropAndReconnect().sent).toEqual([])
    })

    it('does not treat an ack for another request as delivery', () => {
      act(() => {
        probe.sendMessage({ type: 'reset_session' })
      })
      const first = openFirst()
      act(() => first.receive({ type: 'session_reset', request_id: 'someone-else' }))
      expect(types(dropAndReconnect())).toEqual(['reset_session'])
    })

    it('treats an error reply to the request as a refusal: surfaced, not replayed', () => {
      act(() => {
        probe.sendMessage({ type: 'restore_conversation', conversation_id: 'c1', messages: [] })
      })
      const first = openFirst()
      const error = { type: 'error', message: 'Conversation not found', request_id: ackFor(first) }
      act(() => first.receive(error))
      expect(received).toContainEqual(error)
      expect(dropAndReconnect().sent).toEqual([])
    })

    it('leaves the frame held when an unrelated error arrives', () => {
      act(() => {
        probe.sendMessage({ type: 'reset_session' })
      })
      const first = openFirst()
      act(() => first.receive({ type: 'error', message: 'chat failed' }))
      expect(types(dropAndReconnect())).toEqual(['reset_session'])
    })

    it('surfaces a failure and drops the frame when no ack arrives in time', () => {
      act(() => {
        probe.sendMessage({ type: 'reset_session' })
      })
      const first = openFirst()
      act(() => {
        vi.advanceTimersByTime(15000)
      })
      const failure = received.find((m) => m.type === 'navigation_failed')
      expect(failure).toBeTruthy()
      expect(failure.frame_type).toBe('reset_session')
      expect(dropAndReconnect().sent).toEqual([])
      expect(first.sent).toHaveLength(1)
    })

    it('treats a reply that arrives after the timeout as confirmation', () => {
      act(() => {
        probe.sendMessage({ type: 'reset_session' })
      })
      const first = openFirst()
      const requestId = first.sent[0].request_id
      act(() => {
        vi.advanceTimersByTime(15000)
      })
      expect(probe.navigationState()).toBe('failed')
      act(() => first.receive({ type: 'session_reset', request_id: requestId }))
      expect(probe.navigationState()).toBe(null)
      let sent
      act(() => {
        sent = probe.sendMessage({ type: 'chat', content: 'hi' })
      })
      expect(sent).toBe(true)
    })

    it('lifts the failed state on a late reply without a request id (older server)', () => {
      act(() => {
        probe.sendMessage({ type: 'restore_conversation', conversation_id: 'c1', messages: [] })
      })
      const first = openFirst()
      act(() => {
        vi.advanceTimersByTime(15000)
      })
      expect(probe.navigationState()).toBe('failed')
      // Another conversation's reply does not count.
      act(() => first.receive({ type: 'conversation_restored', conversation_id: 'other' }))
      expect(probe.navigationState()).toBe('failed')
      act(() => first.receive({ type: 'conversation_restored', conversation_id: 'c1' }))
      expect(probe.navigationState()).toBe(null)
    })

    it('lifts the failed state on a late session_reset without a request id', () => {
      act(() => {
        probe.sendMessage({ type: 'reset_session' })
      })
      const first = openFirst()
      act(() => {
        vi.advanceTimersByTime(15000)
      })
      act(() => first.receive({ type: 'session_reset' }))
      expect(probe.navigationState()).toBe(null)
    })

    it('does not time out once acknowledged', () => {
      act(() => {
        probe.sendMessage({ type: 'reset_session' })
      })
      const first = openFirst()
      act(() => first.receive({ type: 'session_reset', request_id: ackFor(first) }))
      act(() => {
        vi.advanceTimersByTime(60000)
      })
      expect(received.find((m) => m.type === 'navigation_failed')).toBeUndefined()
    })
  })

  describe('expiry', () => {
    it('drops a held transcript that waited too long and says so', () => {
      const first = openFirst()
      act(() => first.finishClose(1006))
      act(() => {
        probe.sendMessage({
          type: 'restore_conversation',
          conversation_id: 'c1',
          messages: [{ role: 'user', content: 'secret transcript' }],
        })
      })
      act(() => {
        vi.advanceTimersByTime(10 * 60 * 1000)
      })
      expect(received.find((m) => m.type === 'navigation_failed')).toBeTruthy()
      act(() => probe.reconnectWebSocket())
      act(() => last().open())
      expect(last().sent).toEqual([])
    })
  })

  describe('expiry timing', () => {
    it('restarts the expiry when the frame is finally written', () => {
      const first = openFirst()
      act(() => first.finishClose(1006))
      act(() => {
        probe.sendMessage({ type: 'restore_conversation', conversation_id: 'c1', messages: [] })
      })
      act(() => {
        vi.advanceTimersByTime(119000)
      })
      act(() => probe.reconnectWebSocket())
      act(() => last().open())
      expect(types(last())).toEqual(['restore_conversation'])
      act(() => {
        vi.advanceTimersByTime(5000)
      })
      expect(received.find((m) => m.type === 'navigation_failed')).toBeUndefined()
    })

    it('does not claim the connection came back when it did not', () => {
      const first = openFirst()
      act(() => first.finishClose(1006))
      act(() => {
        probe.sendMessage({ type: 'reset_session' })
      })
      act(() => {
        vi.advanceTimersByTime(130000)
      })
      const failure = received.find((m) => m.type === 'navigation_failed')
      expect(failure.message).not.toMatch(/came back/i)
    })
  })

  describe('session end', () => {
    it('rejects navigation after the session ended, and replays nothing after sign-in', () => {
      const first = openFirst()
      act(() => {
        first.receive({ type: 'session_ended', reason: 'ended' })
        first.finishClose(4401, 'ended')
      })
      let sent
      act(() => {
        sent = probe.sendMessage({ type: 'restore_conversation', conversation_id: 'c1', messages: [] })
      })
      expect(sent).toBe(false)

      act(() => probe.reconnectWebSocket())
      act(() => last().open())
      expect(last().sent).toEqual([])
    })

    it('clears a held frame on the session_ended frame', () => {
      const first = openFirst()
      act(() => first.finishClose(1006))
      act(() => {
        probe.sendMessage({ type: 'restore_conversation', conversation_id: 'c1', messages: [] })
      })
      act(() => probe.reconnectWebSocket())
      const second = last()
      act(() => second.receive({ type: 'session_ended', reason: 'ended' }))
      act(() => second.finishClose(4401, 'ended'))
      act(() => probe.reconnectWebSocket())
      act(() => last().open())
      expect(last().sent).toEqual([])
    })

    it('clears a held frame on a 1008 close', () => {
      const first = openFirst()
      act(() => first.finishClose(1006))
      act(() => {
        probe.sendMessage({ type: 'restore_conversation', conversation_id: 'c1', messages: [] })
      })
      act(() => probe.reconnectWebSocket())
      act(() => last().finishClose(1008, 'Authentication required'))
      act(() => probe.reconnectWebSocket())
      act(() => last().open())
      expect(last().sent).toEqual([])
    })

    it('keeps chat blocked, with a notice, after a 1008 close discarded a held frame', () => {
      const first = openFirst()
      act(() => first.finishClose(1006))
      act(() => {
        probe.sendMessage({ type: 'restore_conversation', conversation_id: 'c1', messages: [] })
      })
      act(() => probe.reconnectWebSocket())
      act(() => last().finishClose(1008, 'Authentication required'))
      expect(received.find((m) => m.type === 'navigation_failed')).toBeTruthy()
      expect(probe.navigationState()).toBe('failed')
      act(() => probe.reconnectWebSocket())
      act(() => last().open())
      let sent
      act(() => {
        sent = probe.sendMessage({ type: 'chat', content: 'hi' })
      })
      expect(sent).toBe(false)
    })

    it('does not resume the server session the user left', () => {
      const first = openFirst()
      act(() => {
        first.receive({ type: 'connection_session', session_id: 'abc-123', resumed: false })
        first.receive({ type: 'session_ended', reason: 'ended' })
        first.finishClose(4401, 'ended')
      })
      act(() => probe.reconnectWebSocket())
      expect(new URL(last().url).searchParams.get('resume_session')).toBe('')
    })
  })

  describe('failing sends', () => {
    it('reports failure for an OPEN socket that throws, and abandons it', () => {
      const first = openFirst()
      first.throwOnSend = true
      let sent
      act(() => {
        sent = probe.sendMessage({ type: 'chat', content: 'hi' })
      })
      expect(sent).toBe(false)
      expect(first.readyState).toBeGreaterThanOrEqual(FakeWebSocket.CLOSING)
    })

    it('reports failure for an unserializable frame without holding it', () => {
      const circular = { type: 'reset_session' }
      circular.self = circular
      let sent
      act(() => {
        sent = probe.sendMessage(circular)
      })
      expect(sent).toBe(false)
      expect(openFirst().sent).toEqual([])
    })
  })

  describe('socket lifecycle', () => {
    it('ignores handlers of a replaced socket', () => {
      const old = openFirst()
      act(() => probe.reconnectWebSocket())
      const fresh = last()
      act(() => fresh.open())
      expect(probe.isConnected).toBe(true)
      act(() => old.finishClose(1006))
      act(() => old.receive({ type: 'session_ended', reason: 'stale' }))
      expect(probe.isConnected).toBe(true)
      expect(probe.sessionEnded).toBe(false)
    })

    it('reconnects soon after a clean drop instead of waiting for the poll', async () => {
      const first = openFirst()
      act(() => {
        vi.advanceTimersByTime(11000)
      })
      const before = FakeWebSocket.instances.length
      act(() => first.finishClose(1006))
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000)
      })
      expect(FakeWebSocket.instances.length).toBe(before + 1)
    })

    it('backs off when the upgrade keeps being refused although HTTP is healthy', async () => {
      // Every socket is refused before it opens, as a 403 on the upgrade is.
      const refuse = () => {
        const socket = last()
        if (socket.readyState === FakeWebSocket.CONNECTING) act(() => socket.finishClose(1006))
      }
      refuse()
      for (let i = 0; i < 120; i += 1) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1000)
        })
        refuse()
      }
      // 120 s with a flat 5 s poll would be about 24 attempts.
      expect(FakeWebSocket.instances.length).toBeLessThan(12)
    })

    it('resets the backoff once a socket has stayed open', async () => {
      const refuse = () => {
        const socket = last()
        if (socket.readyState === FakeWebSocket.CONNECTING) act(() => socket.finishClose(1006))
      }
      refuse()
      for (let i = 0; i < 40; i += 1) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1000)
        })
        refuse()
      }
      act(() => probe.reconnectWebSocket())
      const during = FakeWebSocket.instances.length
      act(() => last().open())
      act(() => {
        vi.advanceTimersByTime(11000)
      })
      act(() => last().finishClose(1006))
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000)
      })
      expect(FakeWebSocket.instances.length).toBe(during + 1)
    })

    it('keeps backing off when sockets open and close at once', async () => {
      const flap = () => {
        const socket = last()
        if (socket.readyState === FakeWebSocket.CONNECTING) {
          act(() => socket.open())
          act(() => socket.finishClose(1006))
        }
      }
      flap()
      for (let i = 0; i < 120; i += 1) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1000)
        })
        flap()
      }
      expect(FakeWebSocket.instances.length).toBeLessThan(14)
    })

    it('reconnects at once when the browser comes back online', async () => {
      const first = openFirst()
      act(() => first.finishClose(1006))
      // Let the first retry fail so a long backoff is pending.
      for (let i = 0; i < 6; i += 1) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1000)
        })
        if (last().readyState === FakeWebSocket.CONNECTING) act(() => last().finishClose(1006))
      }
      const before = FakeWebSocket.instances.length
      await act(async () => {
        window.dispatchEvent(new Event('online'))
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(FakeWebSocket.instances.length).toBe(before + 1)
    })

    it('closes a socket that never finishes connecting', () => {
      const first = last()
      act(() => {
        vi.advanceTimersByTime(20000)
      })
      expect(first.readyState).toBeGreaterThanOrEqual(FakeWebSocket.CLOSING)
      expect(probe.connectionStatus).not.toBe('Connected')
    })
  })
})
