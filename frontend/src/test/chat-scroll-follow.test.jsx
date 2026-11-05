/** Component regressions for transcript follow and user scroll intent (#1166). */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import ChatArea from '../components/ChatArea'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/WSContext')
vi.mock('../components/Message', () => ({
  default: ({ message }) => <article>{message.content}</article>,
}))
vi.mock('../components/WelcomeScreen', () => ({ default: () => null }))
vi.mock('../components/EnabledToolsIndicator', () => ({ default: () => null }))
vi.mock('../components/EnabledDataSourcesIndicator', () => ({ default: () => null }))
vi.mock('../components/ModelSelector', () => ({ default: () => null }))
vi.mock('../components/ToolSelector', () => ({ default: () => null }))
vi.mock('../components/WorkspaceSelector', () => ({ default: () => null }))
vi.mock('../components/PromptSelector', () => ({ default: () => null }))
vi.mock('../components/AgentBusyIndicator', () => ({ default: () => null }))
vi.mock('../components/AgentRunStatus', () => ({ default: () => null }))
vi.mock('../components/ContextUsageMeter', () => ({ default: () => null }))
vi.mock('../components/CorrectTurnModal', () => ({ default: () => null }))
vi.mock('../hooks/useCaptureConsent', () => ({
  useCaptureConsent: () => ({ fetchConsent: () => {}, userEnabled: false }),
}))

const userMessage = { role: 'user', content: 'Question' }
const stream = (content, extra = {}) => ({ role: 'assistant', content, _streaming: true, ...extra })
const tool = { role: 'assistant', type: 'tool_call', content: 'Calling tool', status: 'calling' }

// JSDOM has no layout or scrolling. Model a 400px viewport, a growing
// transcript, clamped scrollTop, and both browser scroll APIs. The real
// MutationObserver is deliberately left intact.
let height
let top
let scrollOptions
let frames
let nextFrame
let descriptors
const viewportHeight = 400
const bottom = () => height - viewportHeight
const isTranscript = el => el.classList?.contains('chat-messages')

function installGeometry() {
  const proto = HTMLElement.prototype
  const native = Element.prototype
  descriptors = new Map()
  const define = (name, value) => {
    descriptors.set(name, Object.getOwnPropertyDescriptor(proto, name))
    Object.defineProperty(proto, name, { configurable: true, ...value })
  }
  define('scrollHeight', {
    get() { return isTranscript(this) ? height : Object.getOwnPropertyDescriptor(native, 'scrollHeight').get.call(this) },
  })
  define('clientHeight', {
    get() { return isTranscript(this) ? viewportHeight : Object.getOwnPropertyDescriptor(native, 'clientHeight').get.call(this) },
  })
  define('scrollTop', {
    get() { return isTranscript(this) ? top : Object.getOwnPropertyDescriptor(native, 'scrollTop').get.call(this) },
    set(value) {
      if (isTranscript(this)) top = Math.max(0, Math.min(bottom(), Number(value)))
      else Object.getOwnPropertyDescriptor(native, 'scrollTop').set.call(this, value)
    },
  })
  define('scrollTo', {
    value(options, y) {
      if (!isTranscript(this)) return
      scrollOptions.push(options)
      this.scrollTop = typeof options === 'object' ? options.top : y
    },
    writable: true,
  })
  define('scrollIntoView', {
    value(options) {
      const transcript = this.closest('.chat-messages')
      if (!transcript) return
      scrollOptions.push(options)
      transcript.scrollTop = transcript.scrollHeight
    },
    writable: true,
  })
}

async function mutations() {
  // React commits and JSDOM's native MutationObserver deliver in microtasks.
  await act(async () => { await Promise.resolve() })
}

async function flushFrames() {
  await mutations()
  // Run frames in batches, as a browser does. New frames run on the next tick.
  for (let count = 0; frames.size && count < 20; count++) {
    const batch = [...frames.values()]
    frames.clear()
    await act(async () => { batch.forEach(callback => callback(performance.now())) })
    await mutations()
  }
  expect(frames.size).toBe(0)
}

async function settle() {
  await flushFrames()
  // Exercise any old delayed 80/250ms callbacks too; they must not yank a reader.
  await act(async () => { vi.advanceTimersByTime(300) })
  await flushFrames()
}

function userScroll(transcript, position) {
  fireEvent.wheel(transcript, { deltaY: position - transcript.scrollTop })
  transcript.scrollTop = position
  fireEvent.scroll(transcript)
}

function setup(overrides = {}) {
  const context = {
    messages: [userMessage],
    isWelcomeVisible: false,
    isThinking: false,
    isStreaming: false,
    isSynthesizing: false,
    isAgentRunning: false,
    currentModel: 'test-model',
    models: [{ name: 'test-model' }],
    tools: [],
    selectedTools: new Set(),
    selectedDataSources: new Set(),
    sessionFiles: { files: [] },
    followUpSuggestions: [],
    setFollowUpSuggestions: vi.fn(),
    sendChatMessage: vi.fn(() => true),
    ...overrides,
  }
  useChat.mockReturnValue(context)
  const view = render(<ChatArea />)
  const transcript = view.container.querySelector('.chat-messages')
  expect(transcript).not.toBeNull()
  return {
    context,
    transcript,
    update(patch) {
      Object.assign(context, patch)
      useChat.mockReturnValue({ ...context })
      view.rerender(<ChatArea />)
    },
  }
}

describe('ChatArea transcript follow (#1166)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    height = 1000
    top = bottom()
    scrollOptions = []
    frames = new Map()
    nextFrame = 0
    installGeometry()
    vi.stubGlobal('requestAnimationFrame', callback => {
      const id = ++nextFrame
      frames.set(id, callback)
      return id
    })
    vi.stubGlobal('cancelAnimationFrame', id => frames.delete(id))
    useWS.mockReturnValue({ isConnected: true, connectionStatus: 'Connected' })
  })

  afterEach(() => {
    cleanup()
    for (const [name, descriptor] of descriptors) {
      if (descriptor) Object.defineProperty(HTMLElement.prototype, name, descriptor)
      else delete HTMLElement.prototype[name]
    }
    vi.unstubAllGlobals()
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('follows stream start, tokens, tool append, end, and transcript refresh while at the bottom', async () => {
    const chat = setup()
    await settle()
    scrollOptions = []

    const steps = [
      { messages: [userMessage, stream('First')], isStreaming: true },
      { messages: [userMessage, stream('First more tokens')] },
      { messages: [userMessage, stream('First more tokens'), tool] },
      { messages: [userMessage, stream('Complete', { _streaming: false }), tool], isStreaming: false },
      { messages: [userMessage, stream('Complete', { _streaming: false }), tool, { role: 'assistant', content: 'Refreshed', _transcriptRefresh: true }] },
    ]
    for (const step of steps) {
      height += 100
      chat.update(step)
      await settle()
      expect(chat.transcript.scrollTop).toBe(bottom())
    }
    // Smooth animations can fight a user's attempt to interrupt streaming.
    expect(scrollOptions.every(options => options?.behavior !== 'smooth')).toBe(true)
  })

  it('keeps a reader 50px from the bottom detached across incoming rows and delayed work', async () => {
    const chat = setup()
    await settle()
    userScroll(chat.transcript, bottom() - 50)
    const readingPosition = chat.transcript.scrollTop

    const steps = [
      { messages: [userMessage, stream('Start')], isStreaming: true },
      { messages: [userMessage, stream('Start and tokens')] },
      { messages: [userMessage, stream('Start and tokens'), tool] },
      { messages: [userMessage, stream('Done', { _streaming: false }), tool], isStreaming: false },
      { messages: [userMessage, stream('Done', { _streaming: false }), tool, { role: 'assistant', content: 'Refreshed', _transcriptRefresh: true }] },
    ]
    for (const step of steps) {
      height += 100
      chat.update(step)
      await settle()
      expect(chat.transcript.scrollTop).toBe(readingPosition)
    }
  })

  it('does not execute a queued follow after the user scrolls up', async () => {
    const chat = setup()
    await settle()
    height += 100
    chat.update({ messages: [userMessage, stream('Start')], isStreaming: true })
    await mutations()
    expect(frames.size).toBeGreaterThan(0)
    // Scroll up before the queued frame, even though it was queued while following.
    userScroll(chat.transcript, 560)
    await settle()
    expect(chat.transcript.scrollTop).toBe(560)
  })

  it('detaches on an upward movement even inside the near-bottom tolerance', async () => {
    const chat = setup({ messages: [userMessage, stream('Start')], isStreaming: true })
    await settle()
    userScroll(chat.transcript, bottom() - 5)
    const readingPosition = chat.transcript.scrollTop
    height += 100
    chat.update({ messages: [userMessage, stream('More')] })
    await settle()
    expect(chat.transcript.scrollTop).toBe(readingPosition)

    userScroll(chat.transcript, bottom())
    height += 100
    chat.update({ messages: [userMessage, stream('More again')] })
    await settle()
    expect(chat.transcript.scrollTop).toBe(bottom())
  })

  it('does not run delayed forced scrolling after the user interrupts a new message', async () => {
    const chat = setup()
    await settle()
    height += 100
    chat.update({ messages: [userMessage, stream('Start')], isStreaming: true })
    await flushFrames()
    expect(chat.transcript.scrollTop).toBe(bottom())
    userScroll(chat.transcript, bottom() - 50)
    const readingPosition = chat.transcript.scrollTop
    await settle()
    expect(chat.transcript.scrollTop).toBe(readingPosition)
  })

  it('reattaches when the reader scrolls back within 5px of the bottom', async () => {
    const chat = setup({ messages: [userMessage, stream('Start')], isStreaming: true })
    await settle()
    userScroll(chat.transcript, bottom() - 50)
    height += 100
    chat.update({ messages: [userMessage, stream('More')] })
    await settle()
    expect(chat.transcript.scrollTop).toBe(550)

    // Moving down is not enough by itself: 20px is still outside the
    // reattachment tolerance, despite being within the old 140px threshold.
    userScroll(chat.transcript, bottom() - 20)
    const stillReading = chat.transcript.scrollTop
    height += 100
    chat.update({ messages: [userMessage, stream('A little more')] })
    await settle()
    expect(chat.transcript.scrollTop).toBe(stillReading)

    userScroll(chat.transcript, bottom() - 5)
    height += 100
    chat.update({ messages: [userMessage, stream('More again')] })
    await settle()
    expect(chat.transcript.scrollTop).toBe(bottom())
  })

  it('honors actual DOM mutations while following and while detached', async () => {
    const chat = setup()
    await settle()
    height += 100
    const expansion = document.createElement('div')
    expansion.textContent = 'Expanded content'
    chat.transcript.appendChild(expansion)
    await settle()
    expect(chat.transcript.scrollTop).toBe(bottom())

    userScroll(chat.transcript, bottom() - 50)
    const readingPosition = chat.transcript.scrollTop
    height += 100
    expansion.appendChild(document.createTextNode(' more content'))
    await settle()
    expect(chat.transcript.scrollTop).toBe(readingPosition)
  })

  it.each([
    { accepted: true, follows: true },
    { accepted: false, follows: false },
  ])('only re-enables follow after an accepted send (accepted=$accepted)', async ({ accepted, follows }) => {
    const sendChatMessage = vi.fn(() => accepted)
    const chat = setup({ sendChatMessage })
    await settle()
    userScroll(chat.transcript, bottom() - 50)
    const readingPosition = chat.transcript.scrollTop
    fireEvent.change(screen.getByPlaceholderText(/Type a message/i), { target: { value: 'Next question' } })
    await act(async () => { fireEvent.click(screen.getByTitle('Send message')) })
    expect(sendChatMessage).toHaveBeenCalledWith('Next question', {})
    await settle()

    height += 100
    chat.update({ messages: [userMessage, { role: 'user', content: 'Next question' }] })
    await settle()
    expect(chat.transcript.scrollTop).toBe(follows ? bottom() : readingPosition)
  })
})
