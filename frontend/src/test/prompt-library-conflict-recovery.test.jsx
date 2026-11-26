import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import PromptLibraryEditor from '../components/PromptLibraryEditor'
import PromptLibrarySharing from '../components/PromptLibrarySharing'

const conflict = () => Object.assign(
  new Error('Changed by another user'),
  { status: 409 },
)

describe('Prompt library conflict recovery', () => {
  it('preserves draft edits and retries with the refreshed revision', async () => {
    const prompt = {
      id: 'p1',
      title: 'Test prompt',
      draft_content: 'Original',
      draft_revision: 4,
    }

    const onSave = vi.fn()
      .mockRejectedValueOnce(conflict())
      .mockResolvedValueOnce({
        ...prompt,
        draft_content: 'My edits',
        draft_revision: 6,
      })

    const onRefresh = vi.fn(async () => ({
      ...prompt,
      draft_content: 'Another editor changed this',
      draft_revision: 5,
    }))

    render(
      <PromptLibraryEditor
        prompt={prompt}
        onSave={onSave}
        onRefresh={onRefresh}
        onCancel={() => {}}
      />
    )

    const draft = screen.getByLabelText(/Draft system prompt/)
    fireEvent.change(draft, { target: { value: 'My edits' } })
    fireEvent.click(screen.getByRole('button', { name: /Save draft/ }))

    const confirm = await screen.findByRole('button', {
      name: /Keep my edits and use latest revision/,
    })

    expect(draft).toHaveValue('My edits')
    expect(screen.getByText('Another editor changed this')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Save draft/ })).toBeDisabled()
    expect(onSave).toHaveBeenCalledTimes(1)

    fireEvent.click(confirm)
    fireEvent.click(screen.getByRole('button', { name: /Save draft/ }))

    await waitFor(() => expect(onSave).toHaveBeenLastCalledWith(
      { title: 'Test prompt', description: '', content: 'My edits' },
      5,
    ))
  })

  it('preserves sharing edits and requires confirmation before retrying', async () => {
    const library = {
      getSharing: vi.fn()
        .mockResolvedValueOnce({
          groups: ['eng'], users: [], revision: 2,
        })
        .mockResolvedValueOnce({
          groups: [], users: [], revision: 3,
        }),
      listShareableGroups: vi.fn(async () => ['eng', 'ops']),
      setSharing: vi.fn()
        .mockRejectedValueOnce(conflict())
        .mockResolvedValueOnce({}),
    }

    render(
      <PromptLibrarySharing
        prompt={{ id: 'p1', is_owner: true }}
        library={library}
        onClose={() => {}}
      />
    )

    fireEvent.click(await screen.findByLabelText('ops'))
    fireEvent.click(screen.getByRole('button', { name: /Save sharing/ }))

    const confirm = await screen.findByRole('button', {
      name: /Keep my edits and use latest revision/,
    })

    expect(screen.getByLabelText('ops')).toBeChecked()
    expect(screen.getByRole('button', { name: /Save sharing/ })).toBeDisabled()
    expect(library.setSharing).toHaveBeenCalledTimes(1)

    fireEvent.click(confirm)
    fireEvent.click(screen.getByRole('button', { name: /Save sharing/ }))

    await waitFor(() => expect(library.setSharing).toHaveBeenLastCalledWith(
      'p1',
      { groups: ['ops'], users: [], expected_revision: 3 },
    ))
  })
})
