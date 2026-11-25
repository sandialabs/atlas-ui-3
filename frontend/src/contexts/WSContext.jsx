import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { calculateBackoffDelay } from '../hooks/usePollingWithBackoff'

const WSContext = createContext()

// Reconnect configuration
const INITIAL_HEALTH_CHECK_INTERVAL = 5000 // 5 seconds for first retry
const MAX_HEALTH_CHECK_INTERVAL = 30000 // 30 seconds max between attempts
const CLEAN_DROP_RECONNECT_DELAY = 500 // an established socket dropped: retry almost at once
const CONNECT_TIMEOUT_MS = 10000 // a socket still CONNECTING after this is abandoned
// A socket must stay open this long before it counts as a good connection. One
// that closes sooner (a flapping upgrade) keeps growing the backoff.
const STABLE_CONNECTION_MS = 10000

// Frames that change which conversation the server session is in, mapped to the
// frame the server answers them with. A refusal is an `error` frame.
const NAVIGATION_ACKS = {
  reset_session: 'session_reset',
  restore_conversation: 'conversation_restored',
}
// How long a navigation frame is waited on after it was written to the socket.
const NAVIGATION_ACK_TIMEOUT_MS = 10000
// How long an unsent or unconfirmed frame is kept. A restore carries the
// transcript, so it is not held for longer than a short outage.
const HELD_NAVIGATION_TTL_MS = 120000
// The TTL restarts when the frame is written, but never runs past this age.
const HELD_NAVIGATION_MAX_AGE_MS = 4 * HELD_NAVIGATION_TTL_MS

// What sendMessage returns for a navigation frame that was held, not sent. It
// is truthy but not `true`, so a caller testing `=== true` still sees "not
// delivered yet".
export const SEND_QUEUED = 'queued'

// eslint-disable-next-line react-refresh/only-export-components
export const useWS = () => {
  const context = useContext(WSContext)
  if (!context) {
    throw new Error('useWS must be used within a WSProvider')
  }
  return context
}

export const WSProvider = ({ children }) => {
  const [isConnected, setIsConnected] = useState(false)
  const [connectionStatus, setConnectionStatus] = useState('Disconnected')
  const [sessionEnded, setSessionEnded] = useState(false)
  const wsRef = useRef(null)
  const messageHandlersRef = useRef([])
  // Consecutive failed connection attempts (a socket that closed before it
  // opened, or a backend that did not answer). Reset only when a socket stays open.
  const healthCheckFailuresRef = useRef(0)
  const healthCheckTimeoutRef = useRef(null)
  const scheduleHealthCheckRef = useRef(null)
  const connectTimerRef = useRef(null)
  const sessionEndedRef = useRef(false)
  // The server-side session this page's socket drives. Sent back on every
  // reconnect so the server can resume it -- on whichever replica the load
  // balancer picks -- instead of starting over. Held for the page's lifetime
  // only: a reload starts a fresh UI, so it must start a fresh session too.
  const serverSessionIdRef = useRef('')
  // The one navigation frame (reset_session / restore_conversation) that has
  // not been acknowledged yet. It is replayed on every open until the server
  // answers it, so a New Chat or a conversation picked while disconnected is
  // not lost (issue #1217). One slot: the latest navigation supersedes any
  // earlier one. Shape: { frame, requestId, sentOn, ackTimer, expiryTimer }.
  const heldNavigationRef = useRef(null)
  const navigationCounterRef = useRef(0)
  // Set when a navigation was refused or went unconfirmed: the server session
  // may not be where the screen is, so chat frames are refused until the user
  // navigates again and the server confirms it.
  const navigationOutOfSyncRef = useRef(false)

  const dispatchToHandlers = (data) => {
    messageHandlersRef.current.forEach(handler => {
      try {
        handler(data)
      } catch (error) {
        console.error('Error in message handler:', error)
      }
    })
  }

  const clearHeldNavigation = () => {
    const held = heldNavigationRef.current
    if (!held) return
    clearTimeout(held.ackTimer)
    clearTimeout(held.expiryTimer)
    heldNavigationRef.current = null
  }

  // Gives up on the held frame and tells the UI, so the user is not left
  // believing the server followed a navigation it never confirmed.
  const failHeldNavigation = (held, message) => {
    if (heldNavigationRef.current !== held) return
    clearHeldNavigation()
    navigationOutOfSyncRef.current = true
    dispatchToHandlers({ type: 'navigation_failed', frame_type: held.frame.type, message })
  }

  const armExpiry = (held) => {
    clearTimeout(held.expiryTimer)
    const left = Math.min(HELD_NAVIGATION_TTL_MS, held.heldAt + HELD_NAVIGATION_MAX_AGE_MS - Date.now())
    held.expiryTimer = setTimeout(
      () => failHeldNavigation(held, 'The change was not delivered to the server in time.'),
      Math.max(0, left)
    )
  }

  const holdNavigation = (message) => {
    clearHeldNavigation()
    navigationOutOfSyncRef.current = false
    navigationCounterRef.current += 1
    const requestId = `nav-${Date.now().toString(36)}-${navigationCounterRef.current}`
    const held = {
      frame: { ...message, request_id: requestId },
      requestId,
      heldAt: Date.now(),
      sentOn: null,
      ackTimer: null,
      expiryTimer: null,
    }
    armExpiry(held)
    heldNavigationRef.current = held
    return held
  }

  // Writes the held frame to `ws` unless that socket already carries it.
  // Returns false when the write threw (the frame stays held).
  const sendHeldNavigation = (ws) => {
    const held = heldNavigationRef.current
    if (!held || held.sentOn === ws) return true
    try {
      ws.send(JSON.stringify(held.frame))
    } catch (error) {
      console.error('Could not send the held navigation frame:', error)
      return false
    }
    held.sentOn = ws
    armExpiry(held)
    clearTimeout(held.ackTimer)
    held.ackTimer = setTimeout(
      () => failHeldNavigation(held, 'The server did not confirm the change.'),
      NAVIGATION_ACK_TIMEOUT_MS
    )
    return true
  }

  // The socket that carried the held frame is gone: keep the frame, forget the
  // wait for its answer, and send it again on the next open.
  const releaseHeldNavigationFrom = (ws) => {
    const held = heldNavigationRef.current
    if (held && held.sentOn === ws) {
      held.sentOn = null
      clearTimeout(held.ackTimer)
    }
  }

  // Matches a server frame to the held navigation. A reply carries the
  // request_id the client sent; an older server omits it, so a success frame
  // is also matched by type (and conversation id for a restore).
  const settleHeldNavigation = (ws, data) => {
    const held = heldNavigationRef.current
    if (!held || held.sentOn !== ws || !data) return
    const replyId = data.request_id
    if (replyId !== undefined && replyId !== held.requestId) return
    if (data.type === 'error') {
      // Refused: the error frame still reaches the handlers, which show it.
      if (replyId === held.requestId) {
        clearHeldNavigation()
        navigationOutOfSyncRef.current = true
      }
      return
    }
    if (data.type !== NAVIGATION_ACKS[held.frame.type]) return
    if (
      replyId === undefined &&
      held.frame.type === 'restore_conversation' &&
      data.conversation_id !== held.frame.conversation_id
    ) {
      return
    }
    clearHeldNavigation()
    navigationOutOfSyncRef.current = false
  }

  // A socket that cannot be written to is not worth keeping: drop it so the
  // reconnect path replaces it.
  const abandonSocket = (ws) => {
    setIsConnected(false)
    try {
      ws.close()
    } catch {
      // Already closing.
    }
  }

  const detachSocket = (ws) => {
    ws.onopen = null
    ws.onmessage = null
    ws.onclose = null
    ws.onerror = null
    releaseHeldNavigationFrom(ws)
  }

  const connectWebSocket = () => {
    // The server sends a `session_ended` frame and closes with 4401 when the
    // OIDC login behind the socket was refused by the IdP. Backing off as if
    // the backend were down would hide the actual problem: the user needs to
    // sign in again, so surface a sign-in prompt instead.
    const markSessionEnded = (reason) => {
      if (sessionEndedRef.current) return
      sessionEndedRef.current = true
      // Never replay a held navigation into whatever identity holds the socket
      // after the user signs in again, and do not resume the server session of
      // the identity that left.
      clearHeldNavigation()
      navigationOutOfSyncRef.current = false
      serverSessionIdRef.current = ''
      setSessionEnded(true)
      setConnectionStatus(`Unauthenticated: ${reason || 'Your session ended. Sign in again to continue.'}`)
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const resume = encodeURIComponent(serverSessionIdRef.current)
    const wsUrl = `${protocol}//${window.location.host}/ws?resume_session=${resume}`

    try {
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws
      let stable = false
      let stableTimer = null
      let closed = false

      const handleClose = (event) => {
        if (closed) return
        closed = true
        clearTimeout(connectTimerRef.current)
        clearTimeout(stableTimer)
        releaseHeldNavigationFrom(ws)
        setIsConnected(false)
        // 4401 (application range) means the OIDC login behind the socket was
        // ended; other 1008 closes are proxy-secret or origin problems.
        if (event.code === 4401) {
          markSessionEnded(event.reason)
        } else if (event.code === 1008) {
          clearHeldNavigation()
          setConnectionStatus(`Unauthenticated: ${event.reason || 'Authentication required'}`)
        } else {
          setConnectionStatus('Disconnected')
        }
        if (sessionEndedRef.current) return
        if (!stable) {
          // Refused, never completed, or dropped right after opening: back off,
          // whatever /api/config says.
          healthCheckFailuresRef.current += 1
          scheduleHealthCheckRef.current?.(
            calculateBackoffDelay(
              healthCheckFailuresRef.current,
              INITIAL_HEALTH_CHECK_INTERVAL,
              MAX_HEALTH_CHECK_INTERVAL
            )
          )
        } else if (event.code !== 1008) {
          // An established socket dropped: do not wait for the poll.
          scheduleHealthCheckRef.current?.(CLEAN_DROP_RECONNECT_DELAY)
        }
      }

      clearTimeout(connectTimerRef.current)
      connectTimerRef.current = setTimeout(() => {
        if (wsRef.current === ws && ws.readyState === WebSocket.CONNECTING) {
          try {
            ws.close()
          } catch {
            // Already closing.
          }
          handleClose({ code: 1006, reason: 'Connection timed out' })
        }
      }, CONNECT_TIMEOUT_MS)

      // Every handler checks it still belongs to the current socket: a replaced
      // socket's late events must not rewrite the new socket's state.
      ws.onopen = () => {
        if (wsRef.current !== ws) return
        clearTimeout(connectTimerRef.current)
        // A held navigation goes out first. If it cannot, do not offer this
        // connection for chat frames (they would land in the un-reset
        // session); drop it so the next reconnect retries.
        if (!sendHeldNavigation(ws)) {
          setConnectionStatus('Connection Failed')
          abandonSocket(ws)
          return
        }
        // Only a connection that lasts clears the failure count.
        stableTimer = setTimeout(() => {
          stable = true
          healthCheckFailuresRef.current = 0
        }, STABLE_CONNECTION_MS)
        setIsConnected(true)
        setConnectionStatus('Connected')
      }

      ws.onmessage = (event) => {
        if (wsRef.current !== ws) return
        try {
          const data = JSON.parse(event.data)
          settleHeldNavigation(ws, data)
          if (data && data.type === 'session_ended') {
            markSessionEnded(data.reason)
          }
          if (data && data.type === 'connection_session' && typeof data.session_id === 'string') {
            serverSessionIdRef.current = data.session_id
            return
          }
          dispatchToHandlers(data)
        } catch (error) {
          console.error('Error parsing WebSocket message:', error)
        }
      }

      ws.onclose = (event) => {
        if (wsRef.current !== ws) return
        handleClose(event)
      }

      ws.onerror = () => {
        if (wsRef.current !== ws) return
        setIsConnected(false)
        setConnectionStatus('Connection Failed')
      }
    } catch {
      setIsConnected(false)
      setConnectionStatus('Connection Failed')
    }
  }

  // Returns true if the message was handed to an open socket, SEND_QUEUED if it
  // is a navigation frame held until the server confirms it, false otherwise.
  // Callers use the return value to avoid leaving the UI in a "waiting" state
  // (e.g. an eternal "Thinking..." spinner) when the socket is disconnected.
  const sendMessage = (message) => {
    const navigation = !!message && Object.hasOwn(NAVIGATION_ACKS, message.type)
    if (navigation && sessionEndedRef.current) {
      // The user has to sign in again; a held frame would be replayed into
      // whichever identity holds the socket afterwards.
      console.warn('Session ended; not sending', message.type)
      return false
    }
    // Serialize first, so an unserializable frame is a plain failure and never
    // occupies the slot.
    let payload
    try {
      payload = JSON.stringify(message)
    } catch (error) {
      console.error('Cannot serialize WebSocket message:', error)
      return false
    }
    const ws = wsRef.current
    const open = !!ws && ws.readyState === WebSocket.OPEN
    if (navigation) {
      holdNavigation(message)
      if (!open) {
        console.warn('WebSocket is not connected; holding', message.type, 'for the next open')
        return SEND_QUEUED
      }
      if (sendHeldNavigation(ws)) return true
      abandonSocket(ws)
      return SEND_QUEUED
    }
    if (!open) {
      console.error('WebSocket is not connected, readyState:', ws?.readyState)
      return false
    }
    // A chat turn runs in whichever conversation the server session is in, so
    // it waits for the navigation that moves the session, and is refused when
    // that navigation failed (the screen and the session would disagree).
    if (message?.type === 'chat' && (heldNavigationRef.current || navigationOutOfSyncRef.current)) {
      if (heldNavigationRef.current && !sendHeldNavigation(ws)) abandonSocket(ws)
      console.warn('Chat frame refused: the conversation switch is not confirmed')
      return false
    }
    // A held navigation reaches the server ahead of any other frame, including
    // in the window where the socket reports OPEN but onopen has not run.
    if (!sendHeldNavigation(ws)) {
      abandonSocket(ws)
      return false
    }
    try {
      ws.send(payload)
      return true
    } catch (error) {
      // OPEN but unwritable (a closing socket can throw): report it like a
      // closed socket and replace it.
      console.error('WebSocket send failed:', error)
      abandonSocket(ws)
      return false
    }
  }

  // 'pending' while a navigation awaits the server, 'failed' after one was
  // refused or unconfirmed, null otherwise. Chat frames are refused in both.
  const navigationState = () => {
    if (heldNavigationRef.current) return 'pending'
    return navigationOutOfSyncRef.current ? 'failed' : null
  }

  const addMessageHandler = (handler) => {
    messageHandlersRef.current.push(handler)
    return () => {
      messageHandlersRef.current = messageHandlersRef.current.filter(h => h !== handler)
    }
  }

  useEffect(() => {
    connectWebSocket()

    return () => {
      clearTimeout(connectTimerRef.current)
      clearHeldNavigation()
      const ws = wsRef.current
      if (ws) {
        detachSocket(ws)
        ws.close()
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Separate effect for the reconnect check with exponential backoff
  useEffect(() => {
    const scheduleHealthCheck = (delay) => {
      if (healthCheckTimeoutRef.current) clearTimeout(healthCheckTimeoutRef.current)
      healthCheckTimeoutRef.current = setTimeout(checkBackendAndReconnect, delay)
    }
    scheduleHealthCheckRef.current = scheduleHealthCheck

    const checkBackendAndReconnect = async () => {
      // The OIDC session was ended: poll steadily for a fresh sign-in instead
      // of growing the backoff as if the backend were unavailable.
      if (sessionEndedRef.current) {
        try {
          const response = await fetch('/api/config', { signal: AbortSignal.timeout(5000) })
          if (response.ok) {
            sessionEndedRef.current = false
            setSessionEnded(false)
            reconnectWebSocket()
          }
        } catch {
          // Backend unreachable; keep waiting and polling.
        }
        scheduleHealthCheck(INITIAL_HEALTH_CHECK_INTERVAL)
        return
      }
      // Only check if WebSocket is disconnected and not already attempting to connect
      if (wsRef.current && wsRef.current.readyState !== WebSocket.OPEN && wsRef.current.readyState !== WebSocket.CONNECTING) {
        try {
          const response = await fetch('/api/config', { signal: AbortSignal.timeout(5000) })
          if (response.ok) {
            // A healthy /api/config does not clear the failure count: the
            // upgrade may still be refused. Only a socket that opens does.
            reconnectWebSocket()
            // The new socket's own close schedules the backoff; this is the
            // watchdog if it neither opens nor closes.
            scheduleHealthCheck(INITIAL_HEALTH_CHECK_INTERVAL)
            return
          }
        } catch {
          // Backend is down, don't attempt reconnection
        }
        // Failure path: increase backoff
        healthCheckFailuresRef.current += 1
        const delay = calculateBackoffDelay(
          healthCheckFailuresRef.current,
          INITIAL_HEALTH_CHECK_INTERVAL,
          MAX_HEALTH_CHECK_INTERVAL
        )
        scheduleHealthCheck(delay)
      } else if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        // WebSocket is connected: check again at the normal interval
        scheduleHealthCheck(INITIAL_HEALTH_CHECK_INTERVAL)
      } else {
        // Still connecting, check again soon
        scheduleHealthCheck(INITIAL_HEALTH_CHECK_INTERVAL)
      }
    }

    // The network or the tab came back: try now instead of waiting out a long
    // backoff. `online` also forgets the failures, they were the outage.
    const retryNow = (event) => {
      if (event?.type === 'visibilitychange' && document.visibilityState !== 'visible') return
      const ws = wsRef.current
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return
      if (event?.type === 'online') healthCheckFailuresRef.current = 0
      scheduleHealthCheck(0)
    }
    window.addEventListener('online', retryNow)
    window.addEventListener('focus', retryNow)
    window.addEventListener('pageshow', retryNow)
    document.addEventListener('visibilitychange', retryNow)

    // Start health check cycle
    scheduleHealthCheck(INITIAL_HEALTH_CHECK_INTERVAL)

    return () => {
      if (healthCheckTimeoutRef.current) clearTimeout(healthCheckTimeoutRef.current)
      scheduleHealthCheckRef.current = null
      window.removeEventListener('online', retryNow)
      window.removeEventListener('focus', retryNow)
      window.removeEventListener('pageshow', retryNow)
      document.removeEventListener('visibilitychange', retryNow)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const reconnectWebSocket = () => {
    const old = wsRef.current
    if (old) {
      // Its late events must not touch the socket that replaces it.
      detachSocket(old)
      try {
        old.close()
      } catch {
        // Already closing.
      }
    }
    setIsConnected(false)
    connectWebSocket()
  }

  const value = {
    isConnected,
    connectionStatus,
    sessionEnded,
    sendMessage,
    navigationState,
    addMessageHandler,
    reconnectWebSocket
  }

  return (
    <WSContext.Provider value={value}>
      {children}
    </WSContext.Provider>
  )
}
