/**
 * "Clear team prompt" (issue #1169, review finding 5): a refusal caused by a
 * stored team-prompt pin offers its owner an accessible way out, instead of
 * telling a browser user to call an API route. Confirmation first; loading,
 * success and failure states; always the conversation the server named.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import TeamPromptRecovery from '../components/TeamPromptRecovery'
import Message from '../components/Message'
import { useChat } from '../contexts/ChatContext'

vi.mock('../contexts/ChatContext', () => ({ useChat: vi.fn() }))

let clear
beforeEach(() => {
  clear = vi.fn()
  useChat.mockReturnValue({ clearConversationTeamPrompt: clear, activeConversationId: 'conv-on-screen' })
})

function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

describe('TeamPromptRecovery', () => {
  it('asks for confirmation before clearing, and Cancel changes nothing', () => {
    render(<TeamPromptRecovery conversationId="conv-1" />)
    fireEvent.click(screen.getByRole('button', { name: 'Clear team prompt' }))
    const dialog = screen.getByRole('group', { name: 'Confirm clearing the team prompt' })
    expect(dialog).toHaveTextContent(/no longer available to you/)
    expect(dialog).toHaveTextContent(/keeps its history/)
    expect(document.activeElement).toBe(screen.getAllByRole('button', { name: 'Clear team prompt' })[0])
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(clear).not.toHaveBeenCalled()
    expect(screen.queryByRole('group')).toBeNull()
  })

  it('clears the conversation the refusal named, with a busy state, then says what happens next', async () => {
    const pending = deferred()
    clear.mockReturnValue(pending.promise)
    render(<TeamPromptRecovery conversationId="conv-1" />)
    fireEvent.click(screen.getByRole('button', { name: 'Clear team prompt' }))
    fireEvent.click(screen.getByRole('button', { name: 'Clear team prompt' }))
    expect(clear).toHaveBeenCalledWith('conv-1', null)  // not the conversation on screen
    const busy = screen.getByRole('button', { name: /Clearing team prompt/ })
    expect(busy).toBeDisabled()
    expect(busy).toHaveAttribute('aria-busy', 'true')
    await act(async () => { pending.resolve({ cleared: true, recovered: false }) })
    expect(screen.getByRole('status')).toHaveTextContent(/team prompt was cleared.*default prompt/)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('explains a damaged pin and reports the reset', async () => {
    clear.mockResolvedValue({ cleared: true, recovered: true })
    render(<TeamPromptRecovery conversationId="conv-1" problem="damaged" />)
    fireEvent.click(screen.getByRole('button', { name: 'Clear team prompt' }))
    expect(screen.getByRole('group')).toHaveTextContent(/settings are damaged/)
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear team prompt' })[0])
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/damaged team-prompt settings were reset/))
  })

  it('reports a refusal (for example, not the owner) and lets the user try again', async () => {
    clear.mockRejectedValueOnce(Object.assign(new Error("Only this conversation's owner can clear its team prompt."), { status: 404 }))
    clear.mockResolvedValueOnce({ cleared: false })
    render(<TeamPromptRecovery conversationId="conv-1" />)
    fireEvent.click(screen.getByRole('button', { name: 'Clear team prompt' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear team prompt' })[0])
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/Only this conversation's owner/))
    fireEvent.click(screen.getByRole('button', { name: 'Try clearing again' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear team prompt' })[0])
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/no team prompt any more/))
    expect(clear).toHaveBeenCalledTimes(2)
  })

  it('passes the revision the refusal saw, so a pin changed since is not cleared', async () => {
    clear.mockRejectedValueOnce(Object.assign(new Error("This conversation's team prompt changed since then. Nothing was cleared."), { status: 409 }))
    render(<TeamPromptRecovery conversationId="conv-1" revision={4} />)
    fireEvent.click(screen.getByRole('button', { name: 'Clear team prompt' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear team prompt' })[0])
    expect(clear).toHaveBeenCalledWith('conv-1', 4)
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/changed since then. Nothing was cleared/))
  })

  it('says so when a recovered conversation stays closed (nothing else could be read)', async () => {
    clear.mockResolvedValue({ cleared: true, recovered: true, classificationUnknown: true })
    render(<TeamPromptRecovery conversationId="conv-1" problem="damaged" />)
    fireEvent.click(screen.getByRole('button', { name: 'Clear team prompt' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear team prompt' })[0])
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/stays closed to new messages. Start a new conversation/))
    expect(screen.getByRole('status')).not.toHaveTextContent(/Send your message again/)
  })

  it('renders nothing without a conversation to act on', () => {
    const { container } = render(<TeamPromptRecovery conversationId={null} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('the refusal message shows the action', () => {
  it('renders the refusal text and the action for a recovery message', () => {
    useChat.mockReturnValue({ clearConversationTeamPrompt: clear, settings: {}, isPrinting: false })
    render(<Message message={{
      role: 'system', content: 'Error: pinned prompt unavailable',
      recovery: { action: 'clear_team_prompt', conversationId: 'conv-9', problem: 'unavailable' },
    }} />)
    expect(screen.getByText('Error: pinned prompt unavailable')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clear team prompt' })).toBeInTheDocument()
  })

  it('shows no action on an ordinary error', () => {
    useChat.mockReturnValue({ clearConversationTeamPrompt: clear, settings: {}, isPrinting: false })
    render(<Message message={{ role: 'system', content: 'Error: something else' }} />)
    expect(screen.queryByRole('button', { name: 'Clear team prompt' })).toBeNull()
  })
})
