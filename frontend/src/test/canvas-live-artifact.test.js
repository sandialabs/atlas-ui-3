import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createWebSocketHandler } from '../handlers/chat/websocketHandlers'
import { buildPersistedMessage } from '../utils/chatExport'
import { latestCanvasArtifactContent } from '../utils/canvasArtifacts'

// Live canvas frames have to become a hidden message. Local autosave writes
// messages, not panel state, and a reopen reads that message back.

describe('live canvas frames become a restorable artifact', () => {
  let messages
  let handler
  let setCanvasContent

  beforeEach(() => {
    messages = []
    setCanvasContent = vi.fn()
    handler = createWebSocketHandler({
      addMessage: vi.fn(message => { messages = messages.concat(message) }),
      mapMessages: vi.fn(mapper => { messages = mapper(messages.slice()) }),
      setIsThinking: vi.fn(),
      setCurrentAgentStep: vi.fn(),
      setCanvasContent,
    })
  })

  it('stores the displayed text once and reopens that document', () => {
    const script = "print('kept')\n"
    handler({
      type: 'tool_start',
      tool_call_id: 'call_0',
      tool_name: 'atlas_canvas',
      arguments: { content: 'draft' },
    })
    handler({
      type: 'canvas_content',
      tool_call_id: 'call_0',
      content: script,
    })
    handler({
      type: 'tool_complete',
      tool_call_id: 'call_0',
      tool_name: 'atlas_canvas',
      success: true,
      result: 'Canvas content displayed: draft',
    })
    // A progress frame from another tool updates the panel only.
    handler({ type: 'canvas_content', content: 'not an artifact' })

    expect(setCanvasContent).toHaveBeenCalledWith(script)
    expect(messages.filter(m => m.type === 'canvas_artifact')).toHaveLength(1)
    expect(messages.some(m => m.type === 'tool_call')).toBe(false)
    const artifact = messages[0]
    expect(artifact.canvas_content).toBe(script)
    expect(artifact.status).toBe('completed')

    const persisted = messages.map(m => buildPersistedMessage(m))
    expect(latestCanvasArtifactContent(persisted)).toBe(script)
  })

  it('orders two writes by the canvas frame, not by tool_start', () => {
    handler({
      type: 'tool_start', tool_call_id: 'a', tool_name: 'atlas_canvas',
      arguments: { content: 'A' },
    })
    handler({
      type: 'tool_start', tool_call_id: 'b', tool_name: 'atlas_canvas',
      arguments: { content: 'B' },
    })
    handler({ type: 'canvas_content', tool_call_id: 'b', content: 'B-shown' })
    handler({ type: 'canvas_content', tool_call_id: 'a', content: 'A-shown' })

    const persisted = messages.map(m => buildPersistedMessage(m))
    expect(latestCanvasArtifactContent(persisted)).toBe('A-shown')
    expect(messages.filter(m => m.type === 'canvas_artifact')).toHaveLength(2)
  })

  it('keeps a reused call id apart when the invocation id differs', () => {
    const frame = (step, content) => ({
      invocation_id: `run-1:${step}:call_0`,
      run_id: 'run-1',
      step,
      tool_call_id: 'call_0',
      tool_name: 'atlas_canvas',
      content,
    })
    handler({ type: 'tool_start', ...frame(0, undefined), arguments: { content: 'first' } })
    handler({ type: 'canvas_content', ...frame(0, 'first') })
    handler({ type: 'tool_complete', ...frame(0, undefined), success: true, result: 'ok' })
    handler({ type: 'tool_start', ...frame(1, undefined), arguments: { content: 'second' } })
    handler({ type: 'canvas_content', ...frame(1, 'second') })

    const artifacts = messages.filter(m => m.type === 'canvas_artifact')
    expect(artifacts.map(m => m.canvas_content)).toEqual(['first', 'second'])
    expect(artifacts.map(m => m.artifact_id)).toEqual(['run-1:0:call_0', 'run-1:1:call_0'])
    expect(latestCanvasArtifactContent(messages)).toBe('second')
  })
})
