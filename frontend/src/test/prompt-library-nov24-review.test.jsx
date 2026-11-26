/**
 * November 24 review of the team prompt library (issue #1169), browser side:
 * a stale clear refreshes its revision before the retry (finding 6), "Follow
 * latest" never claims a version the open conversation will not run
 * (finding 7), a version whose text fails to load can be retried (note 10),
 * and the pin-state frame reaches its handler (finding 1).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import TeamPromptRecovery from '../components/TeamPromptRecovery'
import PromptLibraryVersions from '../components/PromptLibraryVersions'
import { useChat } from '../contexts/ChatContext'
import { createWebSocketHandler } from '../handlers/chat/websocketHandlers'

vi.mock('../contexts/ChatContext', () => ({ useChat: vi.fn() }))

describe('Clear team prompt after a 409 (finding 6)', () => {
  let clear
  beforeEach(() => {
    clear = vi.fn()
    useChat.mockReturnValue({ clearConversationTeamPrompt: clear })
  })

  it('retries with the revision the 409 reported, after a fresh confirmation', async () => {
    clear.mockRejectedValueOnce(Object.assign(new Error("This conversation's team prompt changed since then."),
      { status: 409, currentRevision: 9 }))
    clear.mockResolvedValueOnce({ cleared: true })
    render(<TeamPromptRecovery conversationId="conv-1" revision={4} />)
    fireEvent.click(screen.getByRole('button', { name: 'Clear team prompt' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear team prompt' })[0])
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/changed since/))
    expect(screen.getByRole('alert')).toHaveTextContent(/check the selected prompt/i)
    fireEvent.click(screen.getByRole('button', { name: 'Try clearing again' }))
    // Confirmation is asked again: the pin is not the one the user agreed to clear.
    expect(screen.getByRole('group', { name: 'Confirm clearing the team prompt' })).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear team prompt' })[0])
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/cleared/))
    expect(clear.mock.calls).toEqual([['conv-1', 4], ['conv-1', 9]])
  })

  it('keeps the old revision when a failure reports none', async () => {
    clear.mockRejectedValueOnce(Object.assign(new Error('Could not clear the team prompt (503).'), { status: 503 }))
    clear.mockResolvedValueOnce({ cleared: true })
    render(<TeamPromptRecovery conversationId="conv-1" revision={4} />)
    fireEvent.click(screen.getByRole('button', { name: 'Clear team prompt' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear team prompt' })[0])
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Try clearing again' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear team prompt' })[0])
    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument())
    expect(clear.mock.calls).toEqual([['conv-1', 4], ['conv-1', 4]])
  })
})

describe('Version history (finding 7, note 10)', () => {
  const prompt = { id: 'p1', latest_version: { id: 'v3', version_number: 3 } }
  const versions = [3, 2, 1].map(n => ({ id: `v${n}`, version_number: n, author_email: 'a@x.com' }))

  function lib(getVersion) {
    return {
      listVersions: vi.fn(async () => ({ versions, total: 3 })),
      getVersion: getVersion || vi.fn(async (id, vid) => ({ id: vid, content: `text of ${vid}` })),
    }
  }

  const props = extra => ({ prompt, onFork: () => {}, onClose: () => {}, ...extra })

  it('in a conversation pinned to an earlier version, the latest button selects that exact version', async () => {
    const onUseVersion = vi.fn()
    render(<PromptLibraryVersions {...props({ onUseVersion, conversationPinnedVersionId: 'v1' })} library={lib()} />)
    await screen.findByText('v3')
    expect(screen.queryByRole('button', { name: 'Follow latest' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Use latest \(v3\)/ }))
    expect(onUseVersion).toHaveBeenCalledWith('v3')
  })

  it('outside such a conversation, Follow latest says what it does and sends no version', async () => {
    const onUseVersion = vi.fn()
    render(<PromptLibraryVersions {...props({ onUseVersion })} library={lib()} />)
    await screen.findByText('v3')
    const button = screen.getByRole('button', { name: 'Follow latest' })
    expect(button).toHaveAccessibleDescription(/new conversations.*keeps the version/i)
    fireEvent.click(button)
    expect(onUseVersion).toHaveBeenCalledWith(null)
  })

  it('a version whose text fails to load shows an error and can be retried', async () => {
    const getVersion = vi.fn()
      .mockRejectedValueOnce(new Error('Could not load this version.'))
      .mockResolvedValueOnce({ id: 'v2', content: 'text of v2' })
    render(<PromptLibraryVersions {...props({ onUseVersion: () => {} })} library={lib(getVersion)} />)
    fireEvent.click(await screen.findByText('v2'))
    const row = (await screen.findByText('Could not load this version.')).closest('li')
    expect(row).not.toHaveTextContent('Loading...')
    fireEvent.click(screen.getByRole('button', { name: 'Retry loading version 2' }))
    expect(await screen.findByText('text of v2')).toBeInTheDocument()
    expect(getVersion).toHaveBeenCalledTimes(2)
    // The panel-wide history alert is for the history list, not one version.
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows a loading state while a version\'s text is on its way', async () => {
    let resolve
    const getVersion = vi.fn(() => new Promise(r => { resolve = r }))
    render(<PromptLibraryVersions {...props({ onUseVersion: () => {} })} library={lib(getVersion)} />)
    fireEvent.click(await screen.findByText('v2'))
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    resolve({ id: 'v2', content: 'text of v2' })
    expect(await screen.findByText('text of v2')).toBeInTheDocument()
  })
})

describe('pin-state frame (finding 1)', () => {
  it('is handed to the pin-state handler', () => {
    const onPromptPinState = vi.fn()
    const handler = createWebSocketHandler({ onPromptPinState })
    const frame = { type: 'prompt_pin_state', conversation_id: 'c', prompt_library_pin_revision: 2 }
    handler(frame)
    expect(onPromptPinState).toHaveBeenCalledWith(frame)
  })
})
