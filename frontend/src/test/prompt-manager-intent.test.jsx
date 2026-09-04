/**
 * How PromptManager retires an `intent` (PR #839 review).
 *
 * An intent is a one-shot request from outside the panel -- the prompt picker's
 * pencil and "New system prompt" buttons -- to open a particular editor. The
 * user prompt list arrives asynchronously, so the intent can outlive the click
 * that made it. These pin the two cases where it must be dropped rather than
 * applied late: the user has already opened an editor of their own, and the
 * target prompt no longer exists.
 */
import { render, screen, fireEvent } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PromptManager from '../components/PromptManager'
import { useChat } from '../contexts/ChatContext'

vi.mock('../contexts/ChatContext', () => ({ useChat: vi.fn() }))

const prompts = [
  { id: 'p1', title: 'Code reviewer', content: 'Review the code.' },
  { id: 'p2', title: 'Summariser', content: 'Summarise the text.' },
]

const mockChat = (overrides = {}) => {
  useChat.mockReturnValue({
    userPrompts: prompts,
    userPromptsLoading: false,
    userPromptsError: null,
    createUserPrompt: vi.fn(),
    updateUserPrompt: vi.fn(),
    deleteUserPrompt: vi.fn(),
    activePromptKey: null,
    makePromptActive: vi.fn(),
    clearActivePrompt: vi.fn(),
    ...overrides,
  })
}

describe('PromptManager intent handling', () => {
  beforeEach(() => vi.clearAllMocks())

  it('opens the editor for an edit intent', () => {
    mockChat()
    const onIntentConsumed = vi.fn()
    render(<PromptManager intent={{ type: 'edit', id: 'p1' }} onIntentConsumed={onIntentConsumed} />)

    expect(screen.getByDisplayValue('Code reviewer')).toBeInTheDocument()
    expect(onIntentConsumed).toHaveBeenCalled()
  })

  it('waits for a still-loading list before giving up on the target', () => {
    mockChat({ userPrompts: [], userPromptsLoading: true })
    const onIntentConsumed = vi.fn()
    const { rerender } = render(
      <PromptManager intent={{ type: 'edit', id: 'p1' }} onIntentConsumed={onIntentConsumed} />
    )
    expect(onIntentConsumed).not.toHaveBeenCalled()

    mockChat()
    rerender(<PromptManager intent={{ type: 'edit', id: 'p1' }} onIntentConsumed={onIntentConsumed} />)
    expect(screen.getByDisplayValue('Code reviewer')).toBeInTheDocument()
    expect(onIntentConsumed).toHaveBeenCalled()
  })

  // The prompt was deleted between the click and the panel opening. The intent
  // can never apply, so it must be retired -- left live, it would fire at the
  // next unrelated list refresh.
  it('consumes an edit intent whose target is absent from a resolved list', () => {
    mockChat()
    const onIntentConsumed = vi.fn()
    render(<PromptManager intent={{ type: 'edit', id: 'gone' }} onIntentConsumed={onIntentConsumed} />)

    expect(onIntentConsumed).toHaveBeenCalled()
    expect(screen.queryByPlaceholderText(/Prompt title/)).not.toBeInTheDocument()
  })

  // The regression: the list resolves *after* the user has started typing, and
  // the pending intent yanks the editor onto a different prompt, discarding the
  // draft.
  it('does not overwrite a draft the user started before the list resolved', () => {
    mockChat({ userPrompts: [], userPromptsLoading: true })
    const intent = { type: 'edit', id: 'p1' }
    const { rerender } = render(<PromptManager intent={intent} onIntentConsumed={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: /New Prompt/ }))
    fireEvent.change(screen.getByPlaceholderText(/Prompt title/), { target: { value: 'My own draft' } })

    // The list lands now, carrying the intent's target with it.
    mockChat()
    rerender(<PromptManager intent={intent} onIntentConsumed={vi.fn()} />)

    expect(screen.getByDisplayValue('My own draft')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('Code reviewer')).not.toBeInTheDocument()
  })

  it('does not reopen an intent over an edit the user opened themselves', () => {
    mockChat({ userPrompts: [], userPromptsLoading: true })
    const intent = { type: 'edit', id: 'p1' }
    const onIntentConsumed = vi.fn()
    const { rerender } = render(<PromptManager intent={intent} onIntentConsumed={onIntentConsumed} />)

    // The list is still loading, so the intent has not applied. The user gets
    // impatient and starts a new prompt of their own.
    fireEvent.click(screen.getByRole('button', { name: /New Prompt/ }))
    expect(onIntentConsumed).toHaveBeenCalled()  // overtaken, so retired

    // The list lands. The intent is spent and must not reopen 'Code reviewer'.
    mockChat()
    rerender(<PromptManager intent={intent} onIntentConsumed={onIntentConsumed} />)
    expect(screen.queryByDisplayValue('Code reviewer')).not.toBeInTheDocument()
  })
})
