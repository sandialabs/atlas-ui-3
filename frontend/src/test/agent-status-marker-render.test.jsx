/**
 * The agent_status row rendering (issue #849).
 *
 * The run-start marker arrives with no text and must render as just the
 * small purple "Agent" box; agent_status rows that do carry text (e.g. the
 * max-steps notice) keep badge + copy.
 */
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Message from '../components/Message'
import { useChat } from '../contexts/ChatContext'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn()
}))

vi.mock('../hooks/useSettings', () => ({
  useSettings: () => ({
    settings: { compactMessages: true },
    updateSettings: vi.fn(),
    getSetting: (k) => ({ compactMessages: true }[k])
  })
}))

const baseMessage = {
  role: 'system',
  type: 'agent_status',
  timestamp: new Date().toISOString(),
  agent_mode: true,
}

describe('Message agent_status rendering (issue #849)', () => {
  beforeEach(() => {
    useChat.mockReturnValue({
      appName: 'ATLAS',
      downloadFile: vi.fn(),
      isSynthesizing: false,
      settings: { compactMessages: true },
    })
  })

  it('renders only the small Agent box when the marker has no text', () => {
    render(<Message message={{ ...baseMessage, content: '' }} onRewind={null} userIndex={null} />)
    expect(screen.getByText('Agent')).toBeInTheDocument()
    expect(screen.queryByText(/Agent Mode Started/)).not.toBeInTheDocument()
  })

  it('keeps badge plus copy for agent_status rows with text', () => {
    render(
      <Message
        message={{ ...baseMessage, content: 'Agent Max Steps Reached - stop' }}
        onRewind={null}
        userIndex={null}
      />
    )
    expect(screen.getByText('Agent')).toBeInTheDocument()
    expect(screen.getByText('Agent Max Steps Reached - stop')).toBeInTheDocument()
  })
})