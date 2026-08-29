/**
 * Message-level integration tests for the atlas_agent_sleep timer copy (#838).
 *
 * The unit tests in ToolElapsedTime.test.jsx cover the timer component in
 * isolation; these render the full Message tree (with the ChatContext mock used
 * elsewhere) to verify that the active sleep tool row actually shows the
 * progress-clock copy and not the generic "taking longer than expected" warning.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import Message from '../components/Message'
import { useChat } from '../contexts/ChatContext'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn(),
}))

const setChat = (settings = {}) => {
  useChat.mockReturnValue({
    appName: 'Atlas',
    downloadFile: vi.fn(),
    isSynthesizing: false,
    sendApprovalResponse: vi.fn(),
    updateSettings: vi.fn(),
    updateToolResult: vi.fn(),
    settings: { autoApproveTools: false, ...settings },
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  localStorage.clear()
  setChat()
})

afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

const tick = (seconds) => {
  act(() => {
    vi.advanceTimersByTime(seconds * 1000)
  })
}

// Importing act lazily to keep the top of the file clean; vitest/react-testing
// need it for state updates driven by fake-timer advances.
let act
beforeEach(async () => {
  const { act: reactAct } = await import('@testing-library/react')
  act = reactAct
})

const sleepToolCall = (overrides = {}) => ({
  role: 'system',
  type: 'tool_call',
  tool_call_id: 'sleep-1',
  tool_name: 'atlas_agent_sleep',
  server_name: 'atlas_agent',
  status: 'calling',
  arguments: { seconds: 1200, reason: 'waiting on a sim' },
  timestamp: new Date().toISOString(),
  ...overrides,
})

describe('Message -- atlas_agent_sleep timer copy (#838)', () => {
  it('shows the progress clock against the requested duration', () => {
    const { container } = render(<Message message={sleepToolCall()} />)
    tick(60)
    const summary = [...container.querySelectorAll('button')].find((b) =>
      b.textContent.includes('atlas_agent_sleep')
    )
    expect(summary).toBeTruthy()
    expect(summary.textContent).toContain('01:00 of 20:00')
    expect(summary.textContent).not.toContain('taking longer than expected')
  })

  it('switches to the completing hint once the requested wait has elapsed', () => {
    const { container } = render(
      <Message message={sleepToolCall({ arguments: { seconds: 5 } })} />
    )
    tick(6)
    const summary = [...container.querySelectorAll('button')].find((b) =>
      b.textContent.includes('atlas_agent_sleep')
    )
    expect(summary.textContent).toContain('completing...')
  })

  it('renders the same progress clock in classic (non-compact) mode', () => {
    setChat({ compactMessages: false })
    const { container } = render(<Message message={sleepToolCall()} />)
    tick(30)
    const summary = [...container.querySelectorAll('button')].find((b) =>
      b.textContent.includes('atlas_agent_sleep')
    )
    expect(summary.textContent).toContain('00:30 of 20:00')
    expect(summary.textContent).not.toContain('taking longer than expected')
  })
})