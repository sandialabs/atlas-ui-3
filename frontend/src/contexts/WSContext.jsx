import { createContext, useContext, useEffect, useRef, useState } from 'react'

const WSContext = createContext()

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

  const connectWebSocket = () => {
    // In development, use the backend port 8000 directly
    // In production, use the same host as the frontend
    const isDev = window.location.hostname.includes('github.dev') || window.location.hostname === 'localhost'
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    
    let wsUrl
    if (isDev && window.location.hostname.includes('github.dev')) {
      // For GitHub Codespaces, replace the port in the hostname
      // From: bookish-giggle-695w65wxvv2j95-5173.app.github.dev
      // To:   bookish-giggle-695w65wxvv2j95-8000.app.github.dev
      const backendHost = window.location.hostname.replace('-5173.app.github.dev', '-8000.app.github.dev')
      wsUrl = `${protocol}//${backendHost}/ws`
    } else if (isDev && window.location.hostname === 'localhost') {
      // For localhost development
      wsUrl = `${protocol}//localhost:8000/ws`
    } else {
      // Production - use proxy
      wsUrl = `${protocol}//${window.location.host}/ws`
    }
    
    console.log('Attempting WebSocket connection to:', wsUrl)
    console.log('Current location:', window.location.host)
    
    try {
      wsRef.current = new WebSocket(wsUrl)

      wsRef.current.onopen = () => {
        console.log('WebSocket connected')
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
          console.log('Raw message that failed to parse:', event.data)
        }
      }

      wsRef.current.onclose = () => {
        console.log('WebSocket disconnected')
        setIsConnected(false)
        setConnectionStatus('Disconnected (Demo Mode)')
        // Don't attempt to reconnect in demo mode to avoid spam
      }

      wsRef.current.onerror = (error) => {
        console.log('WebSocket connection failed - running in demo mode')
        setIsConnected(false)
        setConnectionStatus('Demo Mode')
      }
    } catch (error) {
      console.log('WebSocket not available - running in demo mode')
      setIsConnected(false)
      setConnectionStatus('Demo Mode')
    }
  }

  const sendMessage = (message) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message))
    } else {
      console.error('WebSocket is not connected')
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

  // Separate effect for periodic health check to avoid infinite loops
  useEffect(() => {
    const checkBackendAndReconnect = async () => {
      // Only check if WebSocket is disconnected and not already attempting to connect
      if (wsRef.current && wsRef.current.readyState !== WebSocket.OPEN && wsRef.current.readyState !== WebSocket.CONNECTING) {
        try {
          const response = await fetch('/api/config', { timeout: 5000 })
          if (response.ok) {
            console.log('Backend is alive but WebSocket is disconnected, attempting reconnection...')
            reconnectWebSocket()
          }
        } catch (error) {
          // Backend is down, don't attempt reconnection
          console.log('Backend health check failed, not attempting WebSocket reconnection')
        }
      }
    }

    // Set up periodic health check every 30 seconds
    const healthCheckInterval = setInterval(checkBackendAndReconnect, 30000)
    
    return () => {
      clearInterval(healthCheckInterval)
    }
  }, [])

  const reconnectWebSocket = () => {
    console.log('Manually reconnecting WebSocket...')
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