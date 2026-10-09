/**
 * When the backend ends the OIDC session behind an open chat socket it sends
 * a `session_ended` frame and closes with 1008. The UI must surface a way
 * back to sign-in instead of treating the connection loss like a backend
 * outage, and WSContext must not grow the reconnect backoff while the
 * session is ended.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ChatArea from '../components/ChatArea'
import { WSProvider, useWS } from '../contexts/WSContext'
import { useChat } from '../contexts/ChatContext'
import { createWebSocketHandler } from '../handlers/chat/websocketHandlers'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext', () => ({
  useMarketplace: () => ({ isComplianceAccessible: () => true }),
  useOptionalMarketplace: () => null,
}))
// Mock the hook (used by ChatArea) while keeping the real provider so the
// session_ended plumbing itself is exercised below.
vi.mock('../contexts/WSContext', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, useWS: vi.fn() }
})
const realWSContext = await vi.importActual('../contexts/WSContext')

const SESSION_ENDED_REASON = 'OIDC session ended. Please sign in again.'

class FakeWebSocket {
  static instances = []
  static OPEN = 1
  static CONNECTING = 0
  constructor() {
    FakeWebSocket.instances.push(this)
    this.readyState = 1
    this.onopen = null
    this.onmessage = null
    this.onclose = null
    this.onerror = null
  }
  send() {}
  close() {}
}

describe('session_ended sign-in flow', () => {
  describe('ChatArea banner', () => {
    const sendChatMessage = vi.fn()

    beforeEach(() => {
      vi.clearAllMocks()
      useChat.mockReturnValue({
        messages: [],
        isWelcomeVisible: true,
        isThinking: false,
        sendChatMessage,
        currentModel: 'gpt-4',
        tools: [],
        prompts: [],
        selectedTools: new Set(),
        selectedPrompts: new Set(),
        toggleTool: vi.fn(),
        togglePrompt: vi.fn(),
        sessionFiles: { files: [], total_files: 0, categories: {} },
        agentModeEnabled: false,
        agentPendingQuestion: null,
        setAgentPendingQuestion: vi.fn(),
        stopAgent: vi.fn(),
        answerAgentQuestion: vi.fn(),
        followUpSuggestions: [],
        setFollowUpSuggestions: vi.fn(),
      })
    })

    const renderChat = () =>
      render(
        <BrowserRouter>
          <ChatArea />
        </BrowserRouter>
      )

    it('offers a way back to sign-in when the session ended', () => {
      useWS.mockReturnValue({
        isConnected: false,
        connectionStatus: `Unauthenticated: ${SESSION_ENDED_REASON}`,
        sessionEnded: true,
        sendMessage: vi.fn(),
      })
      renderChat()

      const banner = screen.getByTestId('session-ended-banner')
      expect(banner).toBeInTheDocument()
      expect(screen.queryByText(/Disconnected from server/)).not.toBeInTheDocument()

      const link = screen.getByTestId('session-signin-link')
      expect(link).toHaveAttribute('href')
      const url = new URL(link.getAttribute('href'), 'http://localhost')
      expect(url.pathname).toBe('/auth/oidc/login')
      expect(url.searchParams.get('next')).toBe(window.location.pathname + window.location.search)
    })

    it('keeps the ordinary disconnected banner when the session did not end', () => {
      useWS.mockReturnValue({
        isConnected: false,
        connectionStatus: 'Disconnected',
        sessionEnded: false,
        sendMessage: vi.fn(),
      })
      renderChat()

      expect(screen.getByTestId('ws-disconnected-banner')).toBeInTheDocument()
      expect(screen.getByText(/Disconnected from server/)).toBeInTheDocument()
      expect(screen.queryByTestId('session-signin-link')).not.toBeInTheDocument()
    })
  })

  describe('chat message handler', () => {
    it('stops the turn and tells the user to sign in again', () => {
      const deps = {
        addMessage: vi.fn(),
        mapMessages: vi.fn(),
        setIsThinking: vi.fn(),
        setIsAgentRunning: vi.fn(),
        setIsSynthesizing: vi.fn(),
        setCurrentAgentStep: vi.fn(),
        setAgentPendingQuestion: vi.fn(),
        streamToken: vi.fn(),
        streamEnd: vi.fn(),
      }
      const handler = createWebSocketHandler(deps)
      handler({ type: 'session_ended', reason: 'OIDC session ended. Please sign in again.' })

      expect(deps.setIsThinking).toHaveBeenCalledWith(false)
      expect(deps.setIsSynthesizing).toHaveBeenCalledWith(false)
      expect(deps.setIsAgentRunning).toHaveBeenCalledWith(false)
      expect(deps.streamEnd).toHaveBeenCalled()
      expect(deps.addMessage).toHaveBeenCalledTimes(1)
      const [[message]] = deps.addMessage.mock.calls
      expect(message.role).toBe('system')
      expect(message.content).toContain('Sign in again')
    })
  })

  describe('WSContext', () => {
    let probe
    const RealUseWS = realWSContext.useWS

    const Probe = () => {
      probe = RealUseWS()
      return null
    }

    const renderProvider = () =>
      render(
        <WSProvider>
          <Probe />
        </WSProvider>
      )

    beforeEach(() => {
      vi.stubGlobal('WebSocket', FakeWebSocket)
      FakeWebSocket.instances = []
    })

    afterEach(() => {
      vi.unstubAllGlobals()
    })

    it('marks the session ended on the session_ended frame and the 4401 close', async () => {
      renderProvider()

      const socket = FakeWebSocket.instances[0]
      act(() => {
        socket.onmessage({ data: JSON.stringify({ type: 'session_ended', reason: SESSION_ENDED_REASON }) })
        socket.onclose({ code: 4401, reason: SESSION_ENDED_REASON })
      })

      await waitFor(() => expect(probe.sessionEnded).toBe(true))
      expect(probe.connectionStatus).toBe(`Unauthenticated: ${SESSION_ENDED_REASON}`)
    })

    it('marks the session ended from a bare 4401 close', async () => {
      renderProvider()

      const socket = FakeWebSocket.instances[0]
      act(() => {
        socket.onclose({ code: 4401, reason: SESSION_ENDED_REASON })
      })

      await waitFor(() => expect(probe.sessionEnded).toBe(true))
    })

    it('does not mark the session ended for other 1008 closes', async () => {
      renderProvider()

      const socket = FakeWebSocket.instances[0]
      act(() => {
        socket.onclose({ code: 1008, reason: 'Invalid proxy secret' })
      })

      await waitFor(() => expect(probe.isConnected).toBe(false))
      expect(probe.sessionEnded).toBe(false)
      expect(probe.connectionStatus).toBe('Unauthenticated: Invalid proxy secret')
    })
  })
})
