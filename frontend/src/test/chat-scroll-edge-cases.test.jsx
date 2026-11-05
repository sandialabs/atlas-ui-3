/** Layout and navigation regressions for transcript follow (#1166). */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render } from '@testing-library/react'
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

const question = { role: 'user', content: 'Question' }
const stream = content => ({ role: 'assistant', content, _streaming: true })
let height
let viewportHeight
let top
let frames
let nextFrame
let descriptors
let resizeObservers
const bottom = () => Math.max(0, height - viewportHeight)
const isTranscript = el => el.classList?.contains('chat-messages')

// JSDOM does not perform layout. Changes to height/clientHeight below are
// explicit, as is the browser's clamping and subsequent scroll event.
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
      if (isTranscript(this)) this.scrollTop = typeof options === 'object' ? options.top : y
    },
    writable: true,
  })
  define('scrollIntoView', {
    value() {
      const transcript = this.closest('.chat-messages')
      if (transcript) transcript.scrollTop = transcript.scrollHeight
    },
    writable: true,
  })
}

class TestResizeObserver {
  constructor(callback) {
    this.callback = callback
    this.targets = new Set()
    resizeObservers.add(this)
  }
  observe(target) { this.targets.add(target) }
  unobserve(target) { this.targets.delete(target) }
  disconnect() { this.targets.clear() }
}

async function deliverResize(transcript) {
  await act(async () => {
    for (const observer of resizeObservers) {
      const targets = [...observer.targets].filter(target => target === transcript || transcript.contains(target))
      if (targets.length) observer.callback(targets.map(target => ({ target })), observer)
    }
  })
}

async function mutations() {
  await act(async () => { await Promise.resolve() })
}

async function flushFrames() {
  await mutations()
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
  await act(async () => { vi.advanceTimersByTime(300) })
  await flushFrames()
}

function wheelScroll(transcript, position, deltaY) {
  fireEvent.wheel(transcript, { deltaY })
  transcript.scrollTop = position
  fireEvent.scroll(transcript)
}

function setup(overrides = {}) {
  const context = {
    messages: [question, stream('Start')],
    isWelcomeVisible: false,
    isThinking: false,
    isStreaming: true,
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
    activeConversationId: null,
    conversationNavigationVersion: 0,
    ...overrides,
  }
  useChat.mockReturnValue(context)
  const view = render(<ChatArea />)
  const transcript = view.container.querySelector('.chat-messages')
  expect(transcript).not.toBeNull()
  return {
    transcript,
    update(patch) {
      Object.assign(context, patch)
      useChat.mockReturnValue({ ...context })
      view.rerender(<ChatArea />)
    },
  }
}

describe('ChatArea scroll layout and navigation edge cases (#1166)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    height = 1000
    viewportHeight = 400
    top = bottom()
    frames = new Map()
    nextFrame = 0
    resizeObservers = new Set()
    installGeometry()
    vi.stubGlobal('ResizeObserver', TestResizeObserver)
    // JSDOM may not provide PointerEvent; retain pointer fields for the drag.
    vi.stubGlobal('PointerEvent', class extends MouseEvent {
      constructor(type, options = {}) {
        super(type, options)
        this.pointerId = options.pointerId ?? 0
        this.pointerType = options.pointerType ?? ''
      }
    })
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

  it.each(['content shrinks', 'viewport grows'])(
    'keeps following when %s clamps scrollTop and emits a scroll event', async cause => {
      const chat = setup()
      await settle()
      expect(chat.transcript.scrollTop).toBe(600)
      const previousTop = chat.transcript.scrollTop

      if (cause === 'content shrinks') height -= 150
      else viewportHeight += 150
      // Simulate the browser clamping the previous bottom to the new maximum.
      chat.transcript.scrollTop = previousTop
      expect(chat.transcript.scrollTop).toBe(450)
      fireEvent.scroll(chat.transcript)
      await deliverResize(chat.transcript)
      await settle()

      height += 100
      chat.update({ messages: [question, stream('More')] })
      await settle()
      expect(chat.transcript.scrollTop).toBe(bottom())
    },
  )

  it('does not reattach on a layout-generated downward scroll near bottom, but does on a real wheel down', async () => {
    const chat = setup()
    await settle()
    wheelScroll(chat.transcript, 550, -50)
    expect(chat.transcript.scrollTop).toBe(550)

    // Simulate scroll anchoring during a content resize, which nudges the
    // viewport down into the near-bottom tolerance without a user gesture.
    height = 955
    chat.transcript.scrollTop = 553
    fireEvent.scroll(chat.transcript)
    await deliverResize(chat.transcript)
    await settle()
    expect(chat.transcript.scrollTop).toBe(553)

    height += 100
    chat.update({ messages: [question, stream('More')] })
    await settle()
    expect(chat.transcript.scrollTop).toBe(553)

    wheelScroll(chat.transcript, bottom() - 5, 100)
    height += 100
    chat.update({ messages: [question, stream('More again')] })
    await settle()
    expect(chat.transcript.scrollTop).toBe(bottom())
  })

  it('preserves an upward wheel movement when a DOM mutation arrives before its scroll event', async () => {
    const chat = setup()
    await settle()
    fireEvent.wheel(chat.transcript, { deltaY: -50 })
    chat.transcript.scrollTop = 550 // browser default action; scroll event is still pending

    height += 100
    chat.transcript.appendChild(document.createElement('div'))
    await mutations() // deliver MutationObserver before the browser's scroll event
    expect(chat.transcript.scrollTop).toBe(550)
    fireEvent.scroll(chat.transcript)
    await settle()
    expect(chat.transcript.scrollTop).toBe(550)

    height += 100
    chat.update({ messages: [question, stream('More')] })
    await settle()
    expect(chat.transcript.scrollTop).toBe(550)
  })

  it.each([
    ['wheel', el => fireEvent.wheel(el, { deltaY: -50 })],
    ['touch', el => {
      fireEvent.touchStart(el, { touches: [{ clientY: 200 }] })
      fireEvent.touchMove(el, { touches: [{ clientY: 250 }] })
    }],
    ['keyboard', el => fireEvent.keyDown(el, { key: 'PageUp' })],
    ['scrollbar pointer drag', el => {
      fireEvent.pointerDown(el, { pointerId: 1, pointerType: 'mouse', button: 0, clientX: 398, clientY: 300 })
      fireEvent.pointerMove(el, { pointerId: 1, pointerType: 'mouse', button: 0, clientX: 398, clientY: 250 })
    }],
  ])('honors upward %s intent even when geometry changes before the scroll event', async (_name, gesture) => {
    const chat = setup()
    await settle()
    // An actual scrollbar gesture starts at the right edge of the viewport.
    vi.spyOn(chat.transcript, 'getBoundingClientRect').mockReturnValue({
      left: 0, right: 400, top: 0, bottom: 400, width: 400, height: 400,
    })
    height += 40
    gesture(chat.transcript)
    chat.transcript.scrollTop = 550
    fireEvent.scroll(chat.transcript)
    if (_name === 'scrollbar pointer drag') {
      fireEvent.pointerUp(chat.transcript, { pointerId: 1, pointerType: 'mouse', button: 0 })
    }

    height += 100
    chat.update({ messages: [question, stream('More')] })
    await settle()
    expect(chat.transcript.scrollTop).toBe(550)
  })

  it.each(['touch', 'wheel'])('ignores %s scrolling consumed by a nested panel', async gesture => {
    const chat = setup()
    await settle()
    const panel = document.createElement('div')
    panel.style.overflowY = 'auto'
    Object.defineProperties(panel, {
      scrollHeight: { value: 500 },
      clientHeight: { value: 100 },
    })
    panel.scrollTop = 200
    chat.transcript.appendChild(panel)
    await mutations()
    if (gesture === 'touch') {
      fireEvent.touchStart(panel, { touches: [{ clientY: 200 }] })
      fireEvent.touchMove(panel, { touches: [{ clientY: 250 }] })
    } else fireEvent.wheel(panel, { deltaY: -50 })

    height += 100
    await deliverResize(chat.transcript)
    expect(chat.transcript.scrollTop).toBe(bottom())

    // Once the panel reaches its top, the same gesture can scroll the transcript.
    panel.scrollTop = 0
    if (gesture === 'touch') fireEvent.touchMove(panel, { touches: [{ clientY: 300 }] })
    else fireEvent.wheel(panel, { deltaY: -50 })
    chat.transcript.scrollTop -= 50
    fireEvent.scroll(chat.transcript)
    const readingPosition = chat.transcript.scrollTop
    height += 100
    await deliverResize(chat.transcript)
    expect(chat.transcript.scrollTop).toBe(readingPosition)
  })

  it('accepts direct user scrolling when geometry has not changed', async () => {
    const chat = setup()
    await settle()
    // Scrollbar, assistive technology, and browser controls need not emit a
    // wheel event. Direction alone is sufficient when geometry is unchanged.
    chat.transcript.scrollTop = 550
    fireEvent.scroll(chat.transcript)
    height += 100
    chat.update({ messages: [question, stream('More')] })
    await settle()
    expect(chat.transcript.scrollTop).toBe(550)
  })

  it('resets following on explicit navigation, not when a new conversation receives its ID', async () => {
    const chat = setup()
    await settle()
    wheelScroll(chat.transcript, 550, -50)

    height += 100
    chat.update({ activeConversationId: 'saved-1', messages: [question, stream('Saved')] })
    await settle()
    expect(chat.transcript.scrollTop).toBe(550)

    height += 100
    chat.update({ activeConversationId: 'saved-2', conversationNavigationVersion: 1, messages: [question, stream('Loaded')] })
    await settle()
    expect(chat.transcript.scrollTop).toBe(bottom())

    // An explicit reload of the same conversation is navigation too.
    wheelScroll(chat.transcript, bottom() - 50, -50)
    height += 100
    chat.update({ conversationNavigationVersion: 2, messages: [question, stream('Reloaded')] })
    await settle()
    expect(chat.transcript.scrollTop).toBe(bottom())
  })
})
