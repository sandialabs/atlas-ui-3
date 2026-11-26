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

describe('Editor conflict field preservation', () => {
  const prompt = {
    id: 'p1',
    title: 'Original title',
    description: 'Original description',
    draft_content: 'Original content',
    draft_revision: 4,
  }

  it('adopts the latest draft after a clean publish conflict', async () => {
    const onSave = vi.fn()
    const onPublish = vi.fn()
      .mockRejectedValueOnce(conflict())
      .mockResolvedValueOnce({})

    render(
      <PromptLibraryEditor
        prompt={prompt}
        onSave={onSave}
        onPublish={onPublish}
        onRefresh={async () => ({
          ...prompt,
          draft_content: 'New server content',
          draft_revision: 5,
        })}
        onCancel={() => {}}
      />
    )

    fireEvent.click(screen.getByRole('button', {
      name: /Publish version/,
    }))

    fireEvent.click(await screen.findByRole('button', {
      name: /Keep my edits and use latest revision/,
    }))

    expect(screen.getByLabelText(/Draft system prompt/))
      .toHaveValue('New server content')

    expect(screen.getByRole('button', {
      name: /Save draft/,
    })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', {
      name: /Publish version/,
    }))

    await waitFor(() =>
      expect(onPublish).toHaveBeenLastCalledWith('', 5)
    )

    expect(onSave).not.toHaveBeenCalled()
  })

  it('preserves edited fields but refreshes untouched fields', async () => {
    const onSave = vi.fn().mockRejectedValueOnce(conflict())

    render(
      <PromptLibraryEditor
        prompt={prompt}
        onSave={onSave}
        onRefresh={async () => ({
          ...prompt,
          title: 'New server title',
          draft_content: 'New server content',
          draft_revision: 5,
        })}
        onCancel={() => {}}
      />
    )

    fireEvent.change(screen.getByLabelText('Description'), {
      target: { value: 'My description' },
    })

    fireEvent.click(screen.getByRole('button', {
      name: /Save draft/,
    }))

    fireEvent.click(await screen.findByRole('button', {
      name: /Keep my edits and use latest revision/,
    }))

    expect(screen.getByLabelText('Title'))
      .toHaveValue('New server title')

    expect(screen.getByLabelText('Description'))
      .toHaveValue('My description')

    expect(screen.getByLabelText(/Draft system prompt/))
      .toHaveValue('New server content')
  })
})

it('does not restore an old draft after save succeeds but publish conflicts', async () => {
  const prompt = {
    id: 'p1',
    title: 'Test prompt',
    draft_content: 'Original',
    draft_revision: 4,
  }

  const onSave = vi.fn().mockResolvedValue({
    ...prompt,
    draft_content: 'My saved edit',
    draft_revision: 5,
  })

  const onPublish = vi.fn()
    .mockRejectedValueOnce(conflict())
    .mockResolvedValueOnce({})

  render(
    <PromptLibraryEditor
      prompt={prompt}
      onSave={onSave}
      onPublish={onPublish}
      onRefresh={async () => ({
        ...prompt,
        draft_content: 'New server draft',
        draft_revision: 6,
      })}
      onCancel={() => {}}
    />
  )

  const draft = screen.getByLabelText(/Draft system prompt/)

  fireEvent.change(draft, {
    target: { value: 'My saved edit' },
  })

  fireEvent.click(screen.getByRole('button', {
    name: /Publish version/,
  }))

  fireEvent.click(await screen.findByRole('button', {
    name: /Keep my edits and use latest revision/,
  }))

  expect(draft).toHaveValue('New server draft')

  fireEvent.click(screen.getByRole('button', {
    name: /Publish version/,
  }))

  await waitFor(() =>
    expect(onPublish).toHaveBeenLastCalledWith('', 6)
  )

  expect(onSave).toHaveBeenCalledTimes(1)
})
