/**
 * A long-running MCP tool call that the server runs as a background task is
 * announced with tool_task_started / tool_task_completed. The frames mark the
 * open tool row; they are not unknown message types.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import Message from '../components/Message'
import { useChat } from '../contexts/ChatContext'
import { createWebSocketHandler } from '../handlers/chat/websocketHandlers'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn(),
}))

const toolRow = (overrides = {}) => ({
  role: 'system',
  type: 'tool_call',
  tool_call_id: 'call_1',
  tool_name: 'task_demo_data_pipeline',
  server_name: 'task_demo',
  status: 'calling',
  arguments: {},
  timestamp: new Date().toISOString(),
  ...overrides,
})

describe('tool_task_started / tool_task_completed frames', () => {
  let messages
  let handler
  let warn

  beforeEach(() => {
    messages = []
    warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    handler = createWebSocketHandler({
      addMessage: vi.fn(message => { messages = messages.concat(message) }),
      mapMessages: vi.fn(mapper => { messages = mapper(messages.slice()) }),
      setIsThinking: vi.fn(),
      setCurrentAgentStep: vi.fn(),
      setCanvasContent: vi.fn(),
      setCanvasFiles: vi.fn(),
      setCurrentCanvasFileIndex: vi.fn(),
      setCustomUIContent: vi.fn(),
      setSessionFiles: vi.fn(),
      getFileType: vi.fn(),
      triggerFileDownload: vi.fn(),
      addAttachment: vi.fn(),
      resolvePendingFileEvent: vi.fn(),
    })
    handler({ type: 'tool_start', tool_call_id: 'call_1', tool_name: 'task_demo_data_pipeline', server_name: 'task_demo' })
  })

  afterEach(() => {
    warn.mockRestore()
  })

  const row = () => messages.find(message => message.tool_call_id === 'call_1')

  it('marks the open tool row while the task runs', () => {
    handler({ type: 'tool_task_started', tool_call_id: 'call_1', tool_name: 'data_pipeline', server_name: 'task_demo' })

    expect(row().backgroundTask).toBe(true)
    expect(row().status).toBe('calling')
  })

  it('clears the mark when the task ends', () => {
    handler({ type: 'tool_task_started', tool_call_id: 'call_1' })
    handler({ type: 'tool_task_completed', tool_call_id: 'call_1' })

    expect(row().backgroundTask).toBe(false)
  })

  it('does not report either frame as an unknown message type', () => {
    handler({ type: 'tool_task_started', tool_call_id: 'call_1' })
    handler({ type: 'tool_task_completed', tool_call_id: 'call_1' })

    expect(warn).not.toHaveBeenCalledWith('Unknown WebSocket message type:', expect.anything(), expect.anything())
  })

  it('leaves other rows alone', () => {
    handler({ type: 'tool_start', tool_call_id: 'call_2', tool_name: 'calculator_add', server_name: 'calculator' })
    handler({ type: 'tool_task_started', tool_call_id: 'call_1' })

    expect(messages.find(message => message.tool_call_id === 'call_2').backgroundTask).toBeUndefined()
  })
})

describe('tool row for a background task', () => {
  beforeEach(() => {
    useChat.mockReturnValue({
      appName: 'Atlas',
      downloadFile: vi.fn(),
      isSynthesizing: false,
      sendApprovalResponse: vi.fn(),
      updateSettings: vi.fn(),
      updateToolResult: vi.fn(),
      settings: { autoApproveTools: false },
    })
  })

  afterEach(cleanup)

  it('says the call is running as a background task', () => {
    render(<Message message={toolRow({ backgroundTask: true })} />)

    expect(screen.getAllByLabelText('BACKGROUND TASK').length).toBeGreaterThan(0)
    expect(screen.getByText('running in the background')).toBeInTheDocument()
    expect(screen.queryByLabelText('CALLING')).not.toBeInTheDocument()
  })

  it('shows the ordinary status without the mark', () => {
    render(<Message message={toolRow()} />)

    expect(screen.getAllByLabelText('CALLING').length).toBeGreaterThan(0)
    expect(screen.queryByText('running in the background')).not.toBeInTheDocument()
  })

  it('shows the outcome once the call has settled', () => {
    render(<Message message={toolRow({ backgroundTask: true, status: 'completed', result: 'done' })} />)

    expect(screen.queryByLabelText('BACKGROUND TASK')).not.toBeInTheDocument()
    expect(screen.queryByText('running in the background')).not.toBeInTheDocument()
  })
})
