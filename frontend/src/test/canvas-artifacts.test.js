import { describe, expect, it } from 'vitest'
import { buildPersistedMessage, DISPLAY_ONLY_MESSAGE_TYPES } from '../utils/chatExport'
import {
  isCanvasArtifactMessage,
  latestCanvasArtifactContent,
  messagesForRestore,
} from '../utils/canvasArtifacts'

const SCRIPT = "print('hello')\nCANVAS>>>\nprint('bye')\n"

const storedCanvas = (content, extra = {}) => ({
  role: 'system',
  content: 'Canvas artifact',
  timestamp: '2026-01-01T00:00:00Z',
  message_type: 'canvas_artifact',
  metadata: {
    message_type: 'canvas_artifact',
    artifact_id: 'c1',
    tool_call_id: 'c1',
    tool_name: 'atlas_canvas',
    canvas_content: content,
    status: 'completed',
    ...extra,
  },
})

describe('canvas artifacts (issue #1157)', () => {
  it('is display-only, like a tool row, but the restore payload keeps the text', () => {
    expect(DISPLAY_ONLY_MESSAGE_TYPES).toContain('canvas_artifact')
    const messages = [
      { role: 'user', content: 'write a script', message_type: 'chat' },
      {
        role: 'tool',
        content: 'Tool call: calc',
        message_type: 'tool_call',
        metadata: { tool_name: 'calc' },
      },
      storedCanvas(SCRIPT),
      { role: 'assistant', content: 'Wrote it.', message_type: 'chat' },
    ]
    const restore = messagesForRestore(messages)
    expect(restore.map(m => m.role)).toEqual(['user', 'system', 'assistant'])
    expect(restore[1].message_type).toBe('canvas_artifact')
    expect(restore[1].metadata.canvas_content).toBe(SCRIPT)
    expect(restore[1].metadata.artifact_id).toBe('c1')
    expect(restore.some(m => m.content === 'Tool call: calc')).toBe(false)
  })

  it('restores the newest canvas, including one carried only in metadata', () => {
    const messages = [
      storedCanvas('first'),
      storedCanvas('second', { artifact_id: 'c2', tool_call_id: 'c2' }),
    ]
    expect(latestCanvasArtifactContent(messages)).toBe('second')
    // A later display can sit earlier in the array. canvas_seq is that order.
    expect(latestCanvasArtifactContent([
      { type: 'canvas_artifact', canvas_content: 'shown last', canvas_seq: 2 },
      { type: 'canvas_artifact', canvas_content: 'shown first', canvas_seq: 1 },
    ])).toBe('shown last')
    expect(latestCanvasArtifactContent([
      { message_type: 'canvas_artifact', metadata: { canvas_content: '' } },
      { message_type: 'chat', content: 'nope' },
    ])).toBe('')
    expect(isCanvasArtifactMessage({ type: 'canvas_artifact', canvas_content: SCRIPT })).toBe(true)
  })

  it('persists the exact canvas fields on a live row', () => {
    const out = buildPersistedMessage({
      role: 'system',
      content: 'Canvas artifact',
      timestamp: 't1',
      type: 'canvas_artifact',
      artifact_id: 'c1',
      tool_call_id: 'c1',
      tool_name: 'atlas_canvas',
      canvas_content: SCRIPT,
      status: 'completed',
      invocation_id: 'inv-1',
      canvas_seq: 2,
    })
    expect(out.message_type).toBe('canvas_artifact')
    expect(out.metadata).toEqual({
      message_type: 'canvas_artifact',
      artifact_id: 'c1',
      tool_call_id: 'c1',
      tool_name: 'atlas_canvas',
      canvas_content: SCRIPT,
      status: 'completed',
      invocation_id: 'inv-1',
      canvas_seq: 2,
    })
  })
})

describe('seen canvas record', () => {
  it('remembers shown documents per conversation', async () => {
    const { canvasSeen, createSeenCanvas, noteCanvasSeen } = await import('../utils/canvasArtifacts')
    const record = createSeenCanvas('conv-1', 'a')
    expect(canvasSeen(record, 'conv-1', 'a')).toBe(true)
    expect(canvasSeen(record, 'conv-2', 'a')).toBe(false)
    noteCanvasSeen(record, 'conv-1', 'b')
    expect(canvasSeen(record, 'conv-1', 'a')).toBe(true)
    noteCanvasSeen(record, 'conv-2', 'c')
    expect(canvasSeen(record, 'conv-1', 'a')).toBe(false)
    expect(canvasSeen(record, 'conv-2', 'c')).toBe(true)
  })

  it('carries a new chat\'s shown documents over to its first saved id', async () => {
    const { canvasSeen, createSeenCanvas, noteCanvasSeen } = await import('../utils/canvasArtifacts')
    const record = createSeenCanvas()
    noteCanvasSeen(record, null, 'draft doc')
    expect(canvasSeen(record, 'conv-9', 'draft doc')).toBe(true)
    noteCanvasSeen(record, 'conv-9', 'next')
    expect(record.id).toBe('conv-9')
    expect(canvasSeen(record, 'conv-9', 'draft doc')).toBe(true)
  })
})
