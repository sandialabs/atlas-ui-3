import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createWebSocketHandler, cleanupStreamState } from './websocketHandlers'

const makeDeps = () => {
  return {
    addMessage: vi.fn(),
    mapMessages: vi.fn(fn => {
      // simple helper to let tests inspect mapping function behavior
      const sample = [{ tool_call_id: 'call-1', status: 'calling' }]
      fn(sample)
    }),
    setIsThinking: vi.fn(),
    setCurrentAgentStep: vi.fn(),
    setAgentPendingQuestion: vi.fn(),
    setCanvasContent: vi.fn(),
    setCanvasFiles: vi.fn(),
    setCurrentCanvasFileIndex: vi.fn(),
    setCustomUIContent: vi.fn(),
    setIsCanvasOpen: vi.fn(),
    setSessionFiles: vi.fn(),
    getFileType: vi.fn(),
    triggerFileDownload: vi.fn(),
    addAttachment: vi.fn(),
    resolvePendingFileEvent: vi.fn(),
    setIsSynthesizing: vi.fn(),
    streamToken: vi.fn(),
    streamEnd: vi.fn(),
    streamReasoningToken: vi.fn(),
    streamReasoningEnd: vi.fn(),
  }
}

describe('createWebSocketHandler – intermediate updates', () => {
  it('adds a rich system message for system_message updates', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    const payload = {
      type: 'intermediate_update',
      update_type: 'system_message',
      data: {
        message: 'Stage 1 complete',
        subtype: 'success',
        tool_call_id: 'tool-123',
        tool_name: 'progress_tool',
      },
    }

    handler(payload)

    expect(deps.addMessage).toHaveBeenCalledTimes(1)
    const msg = deps.addMessage.mock.calls[0][0]
    expect(msg).toMatchObject({
      role: 'system',
      content: 'Stage 1 complete',
      type: 'system',
      subtype: 'success',
      tool_call_id: 'tool-123',
      tool_name: 'progress_tool',
    })
    expect(typeof msg.timestamp).toBe('string')
  })

  it('updates canvas files and respects display hints for progress_artifacts', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    const payload = {
      type: 'intermediate_update',
      update_type: 'progress_artifacts',
      data: {
        artifacts: [
          {
            name: 'ignore.txt',
            b64: 'AAA',
            mime: 'text/plain',
          },
          {
            name: 'chart.html',
            b64: 'BASE64',
            mime: 'text/html',
            viewer: 'html',
            description: 'Chart artifact',
          },
        ],
        display: {
          open_canvas: true,
          primary_file: 'chart.html',
        },
      },
    }

    handler(payload)

    // Should convert artifacts with viewer hints into canvas files
    expect(deps.setCanvasFiles).toHaveBeenCalledTimes(1)
    const canvasFiles = deps.setCanvasFiles.mock.calls[0][0]
    expect(canvasFiles).toEqual([
      {
        filename: 'chart.html',
        content_base64: 'BASE64',
        mime_type: 'text/html',
        type: 'html',
        description: 'Chart artifact',
        isInline: true,
      },
    ])

    // Should select primary file and clear text/cutom UI when open_canvas
    expect(deps.setCurrentCanvasFileIndex).toHaveBeenCalledWith(0)
    expect(deps.setCanvasContent).toHaveBeenCalledWith('')
    expect(deps.setCustomUIContent).toHaveBeenCalledWith(null)

    // Should open the canvas panel when display.open_canvas is true
    expect(deps.setIsCanvasOpen).toHaveBeenCalledWith(true)
  })

  it('creates iframe canvas file from display config with type=iframe', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    const payload = {
      type: 'intermediate_update',
      update_type: 'canvas_files',
      data: {
        files: [],
        display: {
          type: 'iframe',
          url: 'https://example.com/dashboard',
          title: 'Analytics Dashboard',
          sandbox: 'allow-scripts allow-same-origin',
          open_canvas: true
        }
      }
    }

    handler(payload)

    // Should create a virtual iframe canvas file
    expect(deps.setCanvasFiles).toHaveBeenCalledTimes(1)
    const canvasFiles = deps.setCanvasFiles.mock.calls[0][0]
    expect(canvasFiles).toEqual([
      {
        filename: 'Analytics Dashboard',
        type: 'iframe',
        url: 'https://example.com/dashboard',
        sandbox: 'allow-scripts allow-same-origin',
        isInline: true
      }
    ])

    // Should select the iframe file
    expect(deps.setCurrentCanvasFileIndex).toHaveBeenCalledWith(0)
    expect(deps.setCanvasContent).toHaveBeenCalledWith('')
    expect(deps.setCustomUIContent).toHaveBeenCalledWith(null)

    // Should open the canvas panel when display.open_canvas is true
    expect(deps.setIsCanvasOpen).toHaveBeenCalledWith(true)
  })
})

describe('createWebSocketHandler – agent message handling', () => {
  it('agent_control messages are silently ignored (#54)', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)
    const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

    handler({ type: 'agent_control', action: 'stop' })

    // Should NOT log an unknown message warning
    expect(consoleSpy).not.toHaveBeenCalled()
    // Should NOT add any message
    expect(deps.addMessage).not.toHaveBeenCalled()
    consoleSpy.mockRestore()
  })

  it('agent_completion clears state but does not add a message (#62)', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    handler({
      type: 'agent_update',
      update_type: 'agent_completion',
      steps: 3
    })

    // Should clear agent UI state
    expect(deps.setCurrentAgentStep).toHaveBeenCalledWith(0)
    expect(deps.setIsThinking).toHaveBeenCalledWith(false)
    expect(deps.setIsSynthesizing).toHaveBeenCalledWith(false)
    expect(deps.setAgentPendingQuestion).toHaveBeenCalledWith(null)
    // Should NOT add a duplicate completion message
    expect(deps.addMessage).not.toHaveBeenCalled()
  })

  it('agent_start adds a status message', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    handler({
      type: 'agent_update',
      update_type: 'agent_start',
      strategy: 'react',
      max_steps: 10
    })

    expect(deps.addMessage).toHaveBeenCalledTimes(1)
    const msg = deps.addMessage.mock.calls[0][0]
    expect(msg.type).toBe('agent_status')
    expect(msg.content).toContain('Agent Mode Started')
    expect(msg.content).toContain('react')
  })
})

describe('createWebSocketHandler - token streaming', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    cleanupStreamState()
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
  })

  it('buffers tokens and flushes after FLUSH_INTERVAL_MS', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    handler({ type: 'token_stream', token: 'Hello', is_first: true })
    handler({ type: 'token_stream', token: ' world' })

    // Not flushed yet
    expect(deps.streamToken).not.toHaveBeenCalled()

    // Advance past flush interval (30ms)
    vi.advanceTimersByTime(35)

    expect(deps.streamToken).toHaveBeenCalledTimes(1)
    expect(deps.streamToken).toHaveBeenCalledWith('Hello world')
  })

  it('calls streamEnd on is_last and flushes remaining buffer', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    handler({ type: 'token_stream', token: 'Hi', is_first: true })
    handler({ type: 'token_stream', token: ' there' })
    handler({ type: 'token_stream', is_last: true })

    // is_last triggers immediate flush + streamEnd
    expect(deps.streamToken).toHaveBeenCalledWith('Hi there')
    expect(deps.streamEnd).toHaveBeenCalledTimes(1)
  })

  it('clears thinking state on first token', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    handler({ type: 'token_stream', token: 'X', is_first: true })

    expect(deps.setIsThinking).toHaveBeenCalledWith(false)
  })

  it('calls streamEnd on response_complete', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    handler({ type: 'token_stream', token: 'partial', is_first: true })
    handler({ type: 'response_complete' })

    expect(deps.streamToken).toHaveBeenCalledWith('partial')
    expect(deps.streamEnd).toHaveBeenCalledTimes(1)
  })

  it('cleanupStreamState clears pending timer', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    handler({ type: 'token_stream', token: 'leaked', is_first: true })

    // Timer is pending but not fired
    cleanupStreamState()
    vi.advanceTimersByTime(35)

    // streamToken should NOT have been called because cleanup cleared the timer
    expect(deps.streamToken).not.toHaveBeenCalled()
  })

  it('calls streamEnd on error event', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    handler({ type: 'token_stream', token: 'start', is_first: true })
    handler({ type: 'error', message: 'auth failed' })

    expect(deps.streamEnd).toHaveBeenCalledTimes(1)
    expect(deps.setIsThinking).toHaveBeenCalledWith(false)
  })

  it('clears synthesizing state on is_last', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    handler({ type: 'token_stream', token: 'done', is_first: true })
    handler({ type: 'token_stream', is_last: true })

    expect(deps.setIsSynthesizing).toHaveBeenCalledWith(false)
  })
})

describe('createWebSocketHandler - reasoning token streaming', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    cleanupStreamState()
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
  })

  it('buffers reasoning tokens and flushes after FLUSH_INTERVAL_MS', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    handler({ type: 'reasoning_token', token: 'Let me' })
    handler({ type: 'reasoning_token', token: ' think' })

    // Not flushed yet
    expect(deps.streamReasoningToken).not.toHaveBeenCalled()

    // Advance past flush interval (30ms)
    vi.advanceTimersByTime(35)

    expect(deps.streamReasoningToken).toHaveBeenCalledTimes(1)
    expect(deps.streamReasoningToken).toHaveBeenCalledWith('Let me think')
  })

  it('clears isSynthesizing and isThinking on first reasoning_token', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    handler({ type: 'reasoning_token', token: 'Step 1' })

    expect(deps.setIsSynthesizing).toHaveBeenCalledWith(false)
    expect(deps.setIsThinking).toHaveBeenCalledWith(false)
  })

  it('reasoning_content flushes remaining buffer and calls streamReasoningEnd', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    handler({ type: 'reasoning_token', token: 'partial' })
    handler({ type: 'reasoning_content', content: 'full reasoning text' })

    // Buffer should be flushed immediately (not waiting for timer)
    expect(deps.streamReasoningToken).toHaveBeenCalledWith('partial')
    expect(deps.streamReasoningEnd).toHaveBeenCalledTimes(1)
  })

  it('reasoning_content arriving before flush timer fires flushes synchronously', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    // Send reasoning tokens (starts 30ms timer)
    handler({ type: 'reasoning_token', token: 'buffered' })
    expect(deps.streamReasoningToken).not.toHaveBeenCalled()

    // reasoning_content arrives before timer fires — should flush immediately
    handler({ type: 'reasoning_content', content: 'full' })
    expect(deps.streamReasoningToken).toHaveBeenCalledWith('buffered')
    expect(deps.streamReasoningEnd).toHaveBeenCalledTimes(1)

    // Advancing timer should not double-flush
    vi.advanceTimersByTime(35)
    expect(deps.streamReasoningToken).toHaveBeenCalledTimes(1)
  })

  it('cleanupStreamState clears reasoning buffer and timer', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    handler({ type: 'reasoning_token', token: 'leaked reasoning' })

    cleanupStreamState()
    vi.advanceTimersByTime(35)

    // Should NOT have flushed because cleanup cleared everything
    expect(deps.streamReasoningToken).not.toHaveBeenCalled()
  })

  it('reasoning tokens followed by content tokens create correct sequence', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    // Reasoning phase
    handler({ type: 'reasoning_token', token: 'thinking...' })
    vi.advanceTimersByTime(35)
    expect(deps.streamReasoningToken).toHaveBeenCalledWith('thinking...')

    // Reasoning ends
    handler({ type: 'reasoning_content', content: 'thinking...' })
    expect(deps.streamReasoningEnd).toHaveBeenCalledTimes(1)

    // Content phase begins
    handler({ type: 'token_stream', token: 'Answer:', is_first: true })
    handler({ type: 'token_stream', token: ' 42' })
    vi.advanceTimersByTime(35)

    expect(deps.streamToken).toHaveBeenCalledWith('Answer: 42')
  })

  it('stream-end after reasoning-only clears state for synthesis', () => {
    const deps = makeDeps()
    const handler = createWebSocketHandler(deps)

    // Reasoning tokens arrive, then stream ends (reasoning → tool calls, no content)
    handler({ type: 'reasoning_token', token: 'I should call a tool' })
    vi.advanceTimersByTime(35)
    handler({ type: 'reasoning_content', content: 'I should call a tool' })
    handler({ type: 'token_stream', is_last: true })

    expect(deps.streamEnd).toHaveBeenCalledTimes(1)

    // Later: synthesis reasoning tokens should trigger new calls
    deps.streamReasoningToken.mockClear()
    handler({ type: 'reasoning_token', token: 'synthesis thought' })
    vi.advanceTimersByTime(35)

    expect(deps.streamReasoningToken).toHaveBeenCalledWith('synthesis thought')
  })
})
