import { describe, it, expect, vi } from 'vitest'
import { createWebSocketHandler } from './websocketHandlers'

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
    setSessionFiles: vi.fn(),
    getFileType: vi.fn(),
    triggerFileDownload: vi.fn(),
    addAttachment: vi.fn(),
    resolvePendingFileEvent: vi.fn(),
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
  })
})
