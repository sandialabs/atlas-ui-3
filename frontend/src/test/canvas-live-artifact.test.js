import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createWebSocketHandler } from '../handlers/chat/websocketHandlers'
import { buildPersistedMessage } from '../utils/chatExport'
import { latestCanvasArtifactContent, messagesForRestore } from '../utils/canvasArtifacts'

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
      tool_name: 'atlas_canvas',
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
    handler({ type: 'canvas_content', tool_call_id: 'b', tool_name: 'atlas_canvas', content: 'B-shown' })
    handler({ type: 'canvas_content', tool_call_id: 'a', tool_name: 'atlas_canvas', content: 'A-shown' })

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

  it('stores nothing for a denied or failed write after a shown one', () => {
    handler({ type: 'tool_start', tool_call_id: 'c1', tool_name: 'atlas_canvas', arguments: { content: 'real' } })
    handler({ type: 'canvas_content', tool_call_id: 'c1', tool_name: 'atlas_canvas', content: 'real' })
    handler({ type: 'tool_complete', tool_call_id: 'c1', tool_name: 'atlas_canvas', success: true, result: 'ok' })
    // Hook-denied: tool_start, then tool_error, and no canvas frame.
    handler({ type: 'tool_start', tool_call_id: 'c2', tool_name: 'atlas_canvas', arguments: { content: 'denied draft' } })
    handler({ type: 'tool_error', tool_call_id: 'c2', tool_name: 'atlas_canvas', error: 'Tool blocked by policy hook' })
    // Failed completion: no canvas frame either.
    handler({ type: 'tool_start', tool_call_id: 'c3', tool_name: 'atlas_canvas', arguments: { content: 'failed draft' } })
    handler({ type: 'tool_complete', tool_call_id: 'c3', tool_name: 'atlas_canvas', success: false, result: 'blocked' })

    const artifacts = messages.filter(m => m.type === 'canvas_artifact')
    expect(artifacts.map(m => m.canvas_content)).toEqual(['real'])
    const persisted = messages.map(m => buildPersistedMessage(m))
    expect(latestCanvasArtifactContent(persisted)).toBe('real')
    expect(JSON.stringify(messagesForRestore(persisted))).not.toContain('draft')
  })

  it('ignores another tool\'s stamped canvas frame that reuses a canvas call id', () => {
    handler({ type: 'tool_start', tool_call_id: 'call_0', tool_name: 'atlas_canvas', arguments: { content: 'doc' } })
    handler({ type: 'canvas_content', tool_call_id: 'call_0', tool_name: 'atlas_canvas', content: 'doc' })
    handler({ type: 'tool_complete', tool_call_id: 'call_0', tool_name: 'atlas_canvas', success: true, result: 'ok' })
    // A tracked MCP tool's progress update, stamped with its own identity.
    handler({
      type: 'canvas_content', tool_call_id: 'call_0', tool_name: 'demo_render',
      invocation_id: 'run-1:1:call_0', content: 'progress view',
    })

    expect(setCanvasContent).toHaveBeenLastCalledWith('progress view')
    const artifacts = messages.filter(m => m.type === 'canvas_artifact')
    expect(artifacts.map(m => m.canvas_content)).toEqual(['doc'])
  })

  it('a failed artifact row is never the restored document', () => {
    const rows = [
      { type: 'canvas_artifact', canvas_content: 'shown', status: 'completed' },
      { message_type: 'canvas_artifact', metadata: { canvas_content: 'draft', status: 'failed' } },
    ]
    expect(latestCanvasArtifactContent(rows)).toBe('shown')
    expect(messagesForRestore(rows)).toHaveLength(1)
  })
})
