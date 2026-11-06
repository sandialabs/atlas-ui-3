/**
 * A saved canvas artifact is not a transcript bubble (issue #1157).
 * The script stays out of the DOM; the panel is what shows it.
 */
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Message from '../components/Message'
import { useChat } from '../contexts/ChatContext'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn(),
}))

const SCRIPT = "print('secret-canvas-line')\nCANVAS>>>\n"

describe('Message hides canvas artifacts (issue #1157)', () => {
  beforeEach(() => {
    useChat.mockReturnValue({
      appName: 'ATLAS',
      downloadFile: vi.fn(),
      isSynthesizing: false,
      settings: { compactMessages: true },
    })
  })

  it('renders no transcript text for a canvas artifact', () => {
    const { container } = render(
      <Message
        message={{
          role: 'system',
          type: 'canvas_artifact',
          content: 'Canvas artifact',
          canvas_content: SCRIPT,
          timestamp: new Date().toISOString(),
        }}
      />
    )
    expect(container.querySelector('[hidden]')).not.toBeNull()
    expect(screen.queryByText(/secret-canvas-line/)).not.toBeInTheDocument()
    expect(screen.queryByText('Canvas artifact')).not.toBeInTheDocument()
  })
})
