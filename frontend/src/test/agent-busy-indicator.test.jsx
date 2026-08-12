/**
 * Guard: the agent loop must never look frozen between steps.
 *
 * After a tool completes, the loop calls the LLM again for the next turn. The
 * legacy "Thinking..." indicator is gated on `isThinking`, which is already
 * false by then, so nothing moved in the chat area during that wait. The
 * agent-busy indicator fills that window: visible whenever the agent is running
 * and neither the thinking indicator nor a token stream is on screen.
 */

import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import AgentBusyIndicator from '../components/AgentBusyIndicator'

describe('AgentBusyIndicator', () => {
  it('renders while the agent is running with no thinking or streaming state', () => {
    render(<AgentBusyIndicator isAgentRunning isThinking={false} isStreaming={false} currentAgentStep={0} />)
    expect(screen.getByTestId('agent-busy-indicator')).toBeTruthy()
    expect(screen.getByText('Agent thinking...')).toBeTruthy()
  })

  it('names the current step when the agent loop has reported one', () => {
    render(<AgentBusyIndicator isAgentRunning isThinking={false} isStreaming={false} currentAgentStep={3} />)
    expect(screen.getByText('Agent working on step 3...')).toBeTruthy()
  })

  it('stays hidden when the agent is not running', () => {
    render(<AgentBusyIndicator isAgentRunning={false} isThinking={false} isStreaming={false} currentAgentStep={2} />)
    expect(screen.queryByTestId('agent-busy-indicator')).toBeNull()
  })

  it('defers to the existing thinking indicator', () => {
    render(<AgentBusyIndicator isAgentRunning isThinking isStreaming={false} currentAgentStep={2} />)
    expect(screen.queryByTestId('agent-busy-indicator')).toBeNull()
  })

  it('stays hidden while tokens are streaming', () => {
    render(<AgentBusyIndicator isAgentRunning isThinking={false} isStreaming currentAgentStep={2} />)
    expect(screen.queryByTestId('agent-busy-indicator')).toBeNull()
  })

  it('shows the app name above the spinner', () => {
    render(<AgentBusyIndicator isAgentRunning isThinking={false} isStreaming={false} appName="Atlas" />)
    expect(screen.getByText('Atlas')).toBeTruthy()
  })
})
