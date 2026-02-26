import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { calculateBackoffDelay } from '../hooks/usePollingWithBackoff'

const WSContext = createContext()

// Health check backoff configuration
const INITIAL_HEALTH_CHECK_INTERVAL = 5000 // 5 seconds for first retry
const MAX_HEALTH_CHECK_INTERVAL = 300000 // 5 minutes max

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
  const wsRef = useRef(null)
  const messageHandlersRef = useRef([])
  const healthCheckFailuresRef = useRef(0)
  const healthCheckTimeoutRef = useRef(null)

  const connectWebSocket = () => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/ws`
    
    try {
      wsRef.current = new WebSocket(wsUrl)

      wsRef.current.onopen = () => {
        setIsConnected(true)
        setConnectionStatus('Connected')
      }

      wsRef.current.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          messageHandlersRef.current.forEach(handler => {
            try {
              handler(data)
            } catch (error) {
              console.error('Error in message handler:', error)
            }
          })
        } catch (error) {
          console.error('Error parsing WebSocket message:', error)
        }
      }

      wsRef.current.onclose = (event) => {
        setIsConnected(false)
        // Check if closed due to authentication failure (1008 = Policy Violation)
        if (event.code === 1008) {
          setConnectionStatus(`Unauthenticated: ${event.reason || 'Authentication required'}`)
        } else {
          setConnectionStatus('Disconnected')
        }
      }

      wsRef.current.onerror = () => {
        setIsConnected(false)
        setConnectionStatus('Connection Failed')
      }
    } catch {
      setIsConnected(false)
      setConnectionStatus('Connection Failed')
    }
  }

  const sendMessage = (message) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message))
    } else {
      console.error('WebSocket is not connected, readyState:', wsRef.current?.readyState)
    }
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
      if (wsRef.current) {
        wsRef.current.close()
      }
    }
  }, [])

  // Separate effect for health check with exponential backoff
  useEffect(() => {
    const scheduleHealthCheck = (delay) => {
      if (healthCheckTimeoutRef.current) clearTimeout(healthCheckTimeoutRef.current)
      healthCheckTimeoutRef.current = setTimeout(checkBackendAndReconnect, delay)
    }

    const checkBackendAndReconnect = async () => {
      // Only check if WebSocket is disconnected and not already attempting to connect
      if (wsRef.current && wsRef.current.readyState !== WebSocket.OPEN && wsRef.current.readyState !== WebSocket.CONNECTING) {
        try {
          const response = await fetch('/api/config', { signal: AbortSignal.timeout(5000) })
          if (response.ok) {
            healthCheckFailuresRef.current = 0
            reconnectWebSocket()
            // After successful reconnection attempt, check again at initial interval
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
        // WebSocket is connected, reset failures and check at normal interval
        healthCheckFailuresRef.current = 0
        scheduleHealthCheck(INITIAL_HEALTH_CHECK_INTERVAL)
      } else {
        // Still connecting, check again soon
        scheduleHealthCheck(INITIAL_HEALTH_CHECK_INTERVAL)
      }
    }

    // Start health check cycle
    scheduleHealthCheck(INITIAL_HEALTH_CHECK_INTERVAL)

    return () => {
      if (healthCheckTimeoutRef.current) clearTimeout(healthCheckTimeoutRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const reconnectWebSocket = () => {
    if (wsRef.current) {
      wsRef.current.close()
    }
    connectWebSocket()
  }

  const value = {
    isConnected,
    connectionStatus,
    sendMessage,
    addMessageHandler,
    reconnectWebSocket
  }

  return (
    <WSContext.Provider value={value}>
      {children}
    </WSContext.Provider>
  )
}
