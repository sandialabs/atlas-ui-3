import { describe, it, expect, vi } from 'vitest'
import { newRequestId, rememberSteerFrame, takeSteerRetry, STEER_RETRY_WINDOW_MS } from '../utils/steerRetry'
import { createWebSocketHandler } from '../handlers/chat/websocketHandlers'

const unavailable = (requestId, extra = {}) => ({
  type: 'error',
  error_type: 'run_control_unavailable',
  steering: true,
  request_id: requestId,
  run_id: 'run-1',
  message: 'Try again in a moment.',
  ...extra,
})

describe('steer retry (issue #1128)', () => {
  it('gives every request a fresh id', () => {
    expect(newRequestId()).not.toEqual(newRequestId())
  })

  it('repeats a frame once, with its own request id', () => {
    const frames = new Map()
    const frame = { type: 'chat', request_id: 'r1', content: 'go left' }
    rememberSteerFrame(frames, 'r1', frame)
    expect(takeSteerRetry(frames, unavailable('r1'))).toEqual({ ...frame, steer_only: true, run_id: 'run-1' })
    expect(takeSteerRetry(frames, unavailable('r1'))).toBeNull()
  })

  it('binds the repeat to the run the first attempt was routed to', () => {
    const frames = new Map()
    rememberSteerFrame(frames, 'r1', { type: 'chat', request_id: 'r1', conversation_id: 'conv-1' })
    rememberSteerFrame(frames, 'r2', { type: 'chat', request_id: 'r2', conversation_id: 'conv-1' })
    expect(takeSteerRetry(frames, unavailable('r1'))).toEqual({
      type: 'chat', request_id: 'r1', conversation_id: 'conv-1', steer_only: true, run_id: 'run-1',
    })
    // An error frame that names no run leaves the frame unbound.
    const unbound = takeSteerRetry(frames, unavailable('r2', { run_id: undefined }))
    expect(unbound).not.toHaveProperty('run_id')
  })

  it('retries only an unanswered steer, not a refusal or a timeout code', () => {
    const frames = new Map()
    rememberSteerFrame(frames, 'r1', { type: 'chat', request_id: 'r1' })
    rememberSteerFrame(frames, 'r2', { type: 'chat', request_id: 'r2' })
    expect(takeSteerRetry(frames, unavailable('r2', { error_type: 'steering_queue_full' }))).toBeNull()
    // The server reports an unanswered steer as run_control_unavailable, so
    // command_timeout never matches a steer frame.
    expect(takeSteerRetry(frames, unavailable('r1', { error_type: 'command_timeout' }))).toBeNull()
  })

  it('expires frames by deadline, not by count', () => {
    const frames = new Map()
    for (let i = 0; i < 20; i += 1) rememberSteerFrame(frames, `r${i}`, { request_id: `r${i}` }, 1000)
    expect(frames.size).toBe(20)
    expect(takeSteerRetry(frames, unavailable('r0'), 1000 + STEER_RETRY_WINDOW_MS - 1)).not.toBeNull()
    // Past the deadline the frame is not repeated, and is dropped.
    expect(takeSteerRetry(frames, unavailable('r1'), 1000 + STEER_RETRY_WINDOW_MS + 1)).toBeNull()
    expect(frames.has('r1')).toBe(false)
    // Remembering a new frame sweeps the expired ones.
    rememberSteerFrame(frames, 'new', { request_id: 'new' }, 1000 + STEER_RETRY_WINDOW_MS + 1)
    expect(frames.size).toBe(1)
  })

  it('repeats a steer even when another conversation is on screen', () => {
    const frames = new Map()
    const sent = []
    rememberSteerFrame(frames, 'r1', { type: 'chat', request_id: 'r1' })
    const addMessage = vi.fn()
    const handler = createWebSocketHandler({
      addMessage,
      mapMessages: vi.fn(),
      setIsThinking: vi.fn(),
      getVisibleConversationId: () => 'other-conversation',
      retrySteer: (errorFrame) => {
        const frame = takeSteerRetry(frames, errorFrame)
        if (frame) sent.push(frame)
        return !!frame
      },
    })
    handler(unavailable('r1', { conversation_id: 'conv-1' }))
    expect(sent).toHaveLength(1)
    expect(addMessage).not.toHaveBeenCalled()
  })

  it('shows nothing for the first failure and the error for the second', () => {
    const frames = new Map()
    const sent = []
    rememberSteerFrame(frames, 'r1', { type: 'chat', request_id: 'r1', content: 'x' })
    const addMessage = vi.fn()
    const handler = createWebSocketHandler({
      addMessage,
      mapMessages: vi.fn(),
      setIsThinking: vi.fn(),
      retrySteer: (errorFrame) => {
        const frame = takeSteerRetry(frames, errorFrame)
        if (frame) sent.push(frame)
        return !!frame
      },
    })
    handler(unavailable('r1'))
    expect(sent).toEqual([{ type: 'chat', request_id: 'r1', content: 'x', steer_only: true, run_id: 'run-1' }])
    expect(addMessage).not.toHaveBeenCalled()
    handler(unavailable('r1'))
    expect(sent).toHaveLength(1)
    expect(addMessage).toHaveBeenCalledTimes(1)
  })
})
