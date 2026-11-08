import { describe, it, expect, vi } from 'vitest'
import { newRequestId, rememberSteerFrame, takeSteerRetry } from '../utils/steerRetry'
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
    expect(takeSteerRetry(frames, unavailable('r1'))).toEqual({ ...frame, steer_only: true })
    expect(takeSteerRetry(frames, unavailable('r1'))).toBeNull()
  })

  it('also retries a command timeout, but not a refusal', () => {
    const frames = new Map()
    rememberSteerFrame(frames, 'r1', { type: 'chat', request_id: 'r1' })
    rememberSteerFrame(frames, 'r2', { type: 'chat', request_id: 'r2' })
    expect(takeSteerRetry(frames, unavailable('r2', { error_type: 'steering_queue_full' }))).toBeNull()
    expect(takeSteerRetry(frames, unavailable('r1', { error_type: 'command_timeout' }))).not.toBeNull()
  })

  it('forgets the oldest frames', () => {
    const frames = new Map()
    for (let i = 0; i < 20; i += 1) rememberSteerFrame(frames, `r${i}`, { request_id: `r${i}` })
    expect(frames.size).toBe(8)
    expect(takeSteerRetry(frames, unavailable('r0'))).toBeNull()
    expect(takeSteerRetry(frames, unavailable('r19'))).not.toBeNull()
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
    expect(sent).toEqual([{ type: 'chat', request_id: 'r1', content: 'x', steer_only: true }])
    expect(addMessage).not.toHaveBeenCalled()
    handler(unavailable('r1'))
    expect(sent).toHaveLength(1)
    expect(addMessage).toHaveBeenCalledTimes(1)
  })
})
