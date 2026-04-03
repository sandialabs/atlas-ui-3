/**
 * Tests that the chat send button and form submission are blocked when
 * the WebSocket is disconnected, and that sendChatMessage is not called.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ChatArea from '../components/ChatArea'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/WSContext')

const defaultChatContext = {
  messages: [],
  isWelcomeVisible: true,
  isThinking: false,
  isSynthesizing: false,
  sendChatMessage: vi.fn(),
  currentModel: 'gpt-4',
  models: [{ name: 'gpt-4' }],
  tools: [],
  prompts: [],
  selectedTools: new Set(),
  selectedPrompts: new Set(),
  toggleTool: vi.fn(),
  togglePrompt: vi.fn(),
  setToolChoiceRequired: vi.fn(),
  activePromptKey: null,
  makePromptActive: vi.fn(),
  clearActivePrompt: vi.fn(),
  removePrompts: vi.fn(),
  sessionFiles: { files: [], total_files: 0, categories: {} },
  agentModeEnabled: false,
  agentPendingQuestion: null,
  setAgentPendingQuestion: vi.fn(),
  stopAgent: vi.fn(),
  stopStreaming: vi.fn(),
  isStreaming: false,
  answerAgentQuestion: vi.fn(),
  fileExtraction: false,
  ragEnabled: false,
  toggleRagEnabled: vi.fn(),
  selectedDataSources: new Set(),
  clearDataSources: vi.fn(),
  features: {},
  appName: 'Atlas',
  user: 'test@example.com',
  followUpSuggestions: [],
  setFollowUpSuggestions: vi.fn(),
}

describe('ChatArea - WebSocket disconnected send prevention', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useChat.mockReturnValue(defaultChatContext)
  })

  it('disables the send button when WebSocket is disconnected', () => {
    useWS.mockReturnValue({ isConnected: false, sendMessage: vi.fn() })

    render(<BrowserRouter><ChatArea /></BrowserRouter>)

    const textarea = screen.getByPlaceholderText(/Type a message/i)
    fireEvent.change(textarea, { target: { value: 'hello' } })

    // Find the submit button (type="submit")
    const form = textarea.closest('form')
    const submitBtn = form ? form.querySelector('button[type="submit"]') : null
    expect(submitBtn).not.toBeNull()
    expect(submitBtn).toBeDisabled()
  })

  it('enables the send button when WebSocket is connected', () => {
    useWS.mockReturnValue({ isConnected: true, sendMessage: vi.fn() })

    render(<BrowserRouter><ChatArea /></BrowserRouter>)

    const textarea = screen.getByPlaceholderText(/Type a message/i)
    fireEvent.change(textarea, { target: { value: 'hello' } })

    const form = textarea.closest('form')
    const submitBtn = form ? form.querySelector('button[type="submit"]') : null
    expect(submitBtn).not.toBeNull()
    expect(submitBtn).not.toBeDisabled()
  })

  it('does not call sendChatMessage when Enter is pressed while disconnected', () => {
    useWS.mockReturnValue({ isConnected: false, sendMessage: vi.fn() })

    render(<BrowserRouter><ChatArea /></BrowserRouter>)

    const textarea = screen.getByPlaceholderText(/Type a message/i)
    fireEvent.change(textarea, { target: { value: 'hello' } })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })

    expect(defaultChatContext.sendChatMessage).not.toHaveBeenCalled()
  })

  it('calls sendChatMessage when Enter is pressed while connected', async () => {
    useWS.mockReturnValue({ isConnected: true, sendMessage: vi.fn() })

    render(<BrowserRouter><ChatArea /></BrowserRouter>)

    const textarea = screen.getByPlaceholderText(/Type a message/i)
    fireEvent.change(textarea, { target: { value: 'hello' } })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })

    await waitFor(() => {
      expect(defaultChatContext.sendChatMessage).toHaveBeenCalled()
    })
  })
})
