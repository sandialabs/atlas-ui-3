/**
 * Typing in the composer must not re-render previously rendered messages (#866).
 *
 * `Message` is wrapped in `memo`, but ChatArea used to hand it a freshly
 * allocated `onCorrect` arrow on every render. That defeated the memo, so every
 * keystroke re-rendered and re-reconciled the whole transcript -- the mechanism
 * behind the previous response visibly shifting while the user types.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ChatArea from '../components/ChatArea'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'
import { buildCorrectionContext } from '../utils/captureCorrection'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/WSContext')
vi.mock('../utils/captureCorrection', async (importOriginal) => ({
  ...(await importOriginal()),
  buildCorrectionContext: vi.fn(() => null),
}))

// Corrections on: this is the path that used to allocate a fresh onCorrect
// closure per render. With the feature off the prop is a constant null and the
// regression is invisible.
vi.mock('../hooks/useCaptureConsent', () => ({
  useCaptureConsent: () => ({ fetchConsent: vi.fn(), userEnabled: true }),
}))

// Count renders of the memoized Message component.
const messageRenders = { count: 0 }
vi.mock('../components/Message', async () => {
  const { memo } = await import('react')
  const Impl = ({ message, onCorrect }) => {
    messageRenders.count += 1
    return (
      <div data-testid="message">
        {message.content}
        {onCorrect && (
          <button data-testid={`correct-${message.content}`} onClick={() => onCorrect(message)}>
            correct
          </button>
        )}
      </div>
    )
  }
  return { default: memo(Impl) }
})

const messages = [
  { role: 'user', content: 'first question' },
  { role: 'assistant', content: 'first answer' },
  { role: 'user', content: 'second question' },
  { role: 'assistant', content: 'a previous response worth reading' },
]

describe('ChatArea - typing does not re-render the transcript', () => {
  const chatContext = {
    messages,
    isWelcomeVisible: false,
    isThinking: false,
    sendChatMessage: vi.fn(),
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
    features: { finetune_capture: true },
  }

  beforeEach(() => {
    vi.clearAllMocks()
    messageRenders.count = 0
    useChat.mockReturnValue(chatContext)
    useWS.mockReturnValue({ isConnected: true, sendMessage: vi.fn() })
  })

  it('does not re-render messages while the composer grows to several lines', () => {
    render(
      <BrowserRouter>
        <ChatArea />
      </BrowserRouter>
    )
    expect(screen.getAllByTestId('message')).toHaveLength(messages.length)

    const textarea = screen.getByPlaceholderText(/Type a message/i)
    const rendersAfterMount = messageRenders.count

    let typed = ''
    for (const ch of 'line one\nline two\nline three') {
      typed += ch
      fireEvent.change(textarea, { target: { value: typed } })
    }

    expect(textarea.value).toBe(typed)
    expect(messageRenders.count).toBe(rendersAfterMount)
  })

  it('memoized onCorrect handlers still target their own message', () => {
    // The handlers are built by index in a useMemo; an off-by-one there would
    // silently open the correction modal against the wrong turn.
    const captured = []
    buildCorrectionContext.mockImplementation((_msgs, messageIndex) => {
      captured.push(messageIndex)
      return null
    })

    render(
      <BrowserRouter>
        <ChatArea />
      </BrowserRouter>
    )

    fireEvent.click(screen.getByTestId('correct-first answer'))
    fireEvent.click(screen.getByTestId('correct-a previous response worth reading'))

    expect(captured).toEqual([1, 3])
  })
})
