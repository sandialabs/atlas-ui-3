/**
 * The "answer in progress" marker on a replayed bubble (issue #957).
 *
 * A conversation reopened mid-run shows the assistant reply built from the
 * run's replay buffer. In a tab that will receive no further frames (another
 * tab, a reload) the bubble would otherwise read as a complete answer that
 * happens to end mid-sentence, so it carries a marker saying it will refresh
 * when the run finishes. The marker is bound to the replay flag: a bubble
 * that is streaming normally never shows it.
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
    settings: {},
    updateSettings: vi.fn(),
    getSetting: () => undefined
  })
}))

const streamingMessage = {
  role: 'assistant',
  content: 'The answer streamed so far',
  timestamp: new Date().toISOString(),
  _streaming: true,
}

describe('Message stream-replay marker (issue #957)', () => {
  beforeEach(() => {
    useChat.mockReturnValue({
      appName: 'ATLAS',
      downloadFile: vi.fn(),
      isSynthesizing: false,
      settings: {},
    })
  })

  it('marks a replayed, still-streaming bubble as in progress', () => {
    render(<Message message={{ ...streamingMessage, _replayed: true }} onRewind={null} userIndex={null} />)

    const marker = screen.getByTestId('stream-replay-in-progress')
    expect(marker).toBeInTheDocument()
    expect(marker.textContent).toMatch(/answer in progress/i)
    expect(marker.textContent).toMatch(/refresh when the response finishes/i)
  })

  it('never marks a bubble that is streaming normally', () => {
    render(<Message message={{ ...streamingMessage }} onRewind={null} userIndex={null} />)

    expect(screen.queryByTestId('stream-replay-in-progress')).not.toBeInTheDocument()
  })

  it('drops the marker once the replayed stream closes', () => {
    render(
      <Message
        message={{ ...streamingMessage, _replayed: true, _streaming: false }}
        onRewind={null}
        userIndex={null}
      />
    )

    expect(screen.queryByTestId('stream-replay-in-progress')).not.toBeInTheDocument()
  })
})