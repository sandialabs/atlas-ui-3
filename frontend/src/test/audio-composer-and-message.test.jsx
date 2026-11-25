/**
 * Where the dictation and read-aloud buttons appear (issue #1251).
 *
 * The rule and the buttons themselves are covered in
 * audio-stt-tts.test.jsx; this file checks them in place: the microphone in
 * the real composer (the transcript lands in the draft, editable, and is not
 * sent) and the speaker on the real Message component (finished assistant
 * prose only).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ChatArea from '../components/ChatArea'
import Message from '../components/Message'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext', () => ({
  useMarketplace: () => ({ isComplianceAccessible: () => true }),
  useOptionalMarketplace: () => null,
}))
vi.mock('../contexts/WSContext')

const AVAILABLE = { enabled: true, show_button_by_default: true }
const audio = (stt = AVAILABLE, tts = AVAILABLE) => ({ stt, tts })

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('microphone in the composer', () => {
  const sendChatMessage = vi.fn()
  const baseContext = {
    messages: [],
    isWelcomeVisible: false,
    isSynthesizing: false,
    isStreaming: false,
    sendChatMessage,
    stopStreaming: vi.fn(),
    stopAgent: vi.fn(),
    currentModel: 'gpt-4',
    tools: [],
    prompts: [],
    selectedTools: new Set(),
    selectedPrompts: new Set(),
    toggleTool: vi.fn(),
    togglePrompt: vi.fn(),
    sessionFiles: { files: [], total_files: 0, categories: {} },
    agentModeAvailable: true,
    agentPendingQuestion: null,
    setAgentPendingQuestion: vi.fn(),
    answerAgentQuestion: vi.fn(),
    followUpSuggestions: [],
    setFollowUpSuggestions: vi.fn(),
    settings: {},
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useWS.mockReturnValue({ isConnected: true, connectionStatus: 'Connected', sendMessage: vi.fn() })
  })

  const renderChat = (overrides = {}) => {
    useChat.mockReturnValue({ ...baseContext, ...overrides })
    render(<BrowserRouter><ChatArea /></BrowserRouter>)
  }

  it('is absent while speech-to-text is not available, whatever the preference says', () => {
    renderChat({ audio: audio({ enabled: false, show_button_by_default: true }), settings: { showSttButton: true } })
    expect(screen.queryByTestId('dictation-start')).toBeNull()
    cleanup()
    // An older server that sends no audio block at all.
    renderChat({ settings: { showSttButton: true } })
    expect(screen.queryByTestId('dictation-start')).toBeNull()
  })

  it('follows the administrator default, and the user preference over it', () => {
    renderChat({ audio: audio() })
    expect(screen.getByTestId('dictation-start')).toBeInTheDocument()
    cleanup()

    renderChat({ audio: audio({ enabled: true, show_button_by_default: false }) })
    expect(screen.queryByTestId('dictation-start')).toBeNull()
    cleanup()

    renderChat({ audio: audio({ enabled: true, show_button_by_default: false }), settings: { showSttButton: true } })
    expect(screen.getByTestId('dictation-start')).toBeInTheDocument()
    cleanup()

    renderChat({ audio: audio(), settings: { showSttButton: false } })
    expect(screen.queryByTestId('dictation-start')).toBeNull()
  })

  it('puts the transcript into the draft for editing and does not send it', async () => {
    class Recorder {
      static isTypeSupported = () => true
      constructor(stream, options) { this.mimeType = options.mimeType; this.state = 'inactive' }
      start() { this.state = 'recording' }
      stop() {
        this.state = 'inactive'
        this.ondataavailable({ data: new Blob(['x'], { type: this.mimeType }) })
        this.onstop()
      }
    }
    window.MediaRecorder = Recorder
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }) },
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ text: 'dictated words' }) }))

    renderChat({ audio: audio() })
    const textarea = screen.getByPlaceholderText(/Type a message/i)
    fireEvent.change(textarea, { target: { value: 'Typed first' } })
    textarea.setSelectionRange(11, 11)

    fireEvent.click(screen.getByTestId('dictation-start'))
    fireEvent.click(await screen.findByTestId('dictation-stop'))

    await waitFor(() => expect(textarea.value).toBe('Typed first dictated words'))
    expect(sendChatMessage).not.toHaveBeenCalled()
    // Still an ordinary draft: the user can keep editing it.
    fireEvent.change(textarea, { target: { value: 'Typed first dictated words, edited' } })
    expect(textarea.value).toBe('Typed first dictated words, edited')
  })
})

describe('speaker on messages', () => {
  const context = (overrides = {}) => ({
    appName: 'Atlas',
    downloadFile: vi.fn(),
    isSynthesizing: false,
    settings: {},
    audio: audio(),
    ...overrides,
  })

  beforeEach(() => {
    vi.clearAllMocks()
    useChat.mockReturnValue(context())
  })

  const speaker = () => screen.queryByLabelText('Read message aloud')

  it('appears on a finished assistant reply', () => {
    render(<Message message={{ role: 'assistant', content: 'A finished reply.' }} />)
    expect(speaker()).toBeInTheDocument()
  })

  it.each([
    ['a user message', { role: 'user', content: 'my prompt' }],
    ['a reply still streaming', { role: 'assistant', content: 'partial', _streaming: true }],
    ['a replayed fragment', { role: 'assistant', content: 'fragment', _replayed: true }],
    ['an empty reply', { role: 'assistant', content: '   ' }],
    ['a tool row', { role: 'assistant', type: 'tool_call', content: 'ran a tool', tool_name: 'x_y' }],
  ])('does not appear on %s', (_label, message) => {
    render(<Message message={message} />)
    expect(speaker()).toBeNull()
  })

  it('is absent while text-to-speech is not available or hidden by preference', () => {
    const reply = { role: 'assistant', content: 'A finished reply.' }
    useChat.mockReturnValue(context({ audio: audio(AVAILABLE, { enabled: false, show_button_by_default: true }), settings: { showTtsButton: true } }))
    render(<Message message={reply} />)
    expect(speaker()).toBeNull()
    cleanup()

    useChat.mockReturnValue(context({ settings: { showTtsButton: false } }))
    render(<Message message={reply} />)
    expect(speaker()).toBeNull()
    cleanup()

    useChat.mockReturnValue(context({ audio: audio(AVAILABLE, { enabled: true, show_button_by_default: false }) }))
    render(<Message message={reply} />)
    expect(speaker()).toBeNull()
    cleanup()

    useChat.mockReturnValue(context({ audio: audio(AVAILABLE, { enabled: true, show_button_by_default: false }), settings: { showTtsButton: true } }))
    render(<Message message={reply} />)
    expect(speaker()).toBeInTheDocument()
  })
})
