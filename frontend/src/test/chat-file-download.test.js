/**
 * Chat-side file download: a file an MCP tool produced must be downloadable
 * from chat, not only from the File Manager library.
 *
 * Storage sanitizes a filename on the way in, so the session file list is
 * keyed by (say) `Q3_Sales_Report_final_.csv` while the chat and canvas
 * download controls carry the name the tool advertised,
 * `Q3 Sales Report (final).csv`. The backend reconciles the two; the client's
 * job is to ask, and to say something when the answer is no.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createWebSocketHandler } from '../handlers/chat/websocketHandlers'

describe('file_download frames', () => {
  let deps
  let handler

  beforeEach(() => {
    deps = {
      addMessage: vi.fn(),
      mapMessages: vi.fn(),
      setIsThinking: vi.fn(),
      setCurrentAgentStep: vi.fn(),
      triggerFileDownload: vi.fn(),
      streamToken: vi.fn(),
      streamEnd: vi.fn(),
    }
    handler = createWebSocketHandler(deps)
  })

  it('saves the file under the name the tool advertised', () => {
    handler({
      type: 'file_download',
      filename: 'Q3 Sales Report (final).csv',
      content_base64: 'cmVnaW9uLHJldmVudWUK',
    })

    expect(deps.triggerFileDownload).toHaveBeenCalledWith(
      'Q3 Sales Report (final).csv',
      'cmVnaW9uLHJldmVudWUK'
    )
    expect(deps.addMessage).not.toHaveBeenCalled()
  })

  it('surfaces a download error instead of failing silently', () => {
    handler({
      type: 'file_download',
      filename: 'missing.csv',
      error: 'File not found in session',
    })

    expect(deps.triggerFileDownload).not.toHaveBeenCalled()
    expect(deps.addMessage).toHaveBeenCalledTimes(1)
    const [[message]] = deps.addMessage.mock.calls
    expect(message.role).toBe('system')
    expect(message.content).toContain('missing.csv')
    expect(message.content).toContain('File not found in session')
  })
})
