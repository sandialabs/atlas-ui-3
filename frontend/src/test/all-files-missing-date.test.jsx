/**
 * Tests for how the All Files view handles a file without a timestamp.
 *
 * The file API reports `last_modified` as an ISO-8601 string, and `null` when
 * the storage backend cannot report one. A missing value must read as
 * "Unknown" and must not be sorted as if it were from 1970.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import AllFilesView from '../components/AllFilesView'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/WSContext')

const datedFile = {
  key: 'sessions/s1/dated.txt',
  filename: 'dated.txt',
  size: 10,
  last_modified: '2026-06-01T00:00:00Z',
  tags: { source: 'user' }
}

const undatedFile = {
  key: 'sessions/s1/undated.txt',
  filename: 'undated.txt',
  size: 10,
  last_modified: null,
  tags: { source: 'user' }
}

const unparseableFile = {
  key: 'sessions/s1/unparseable.txt',
  filename: 'unparseable.txt',
  size: 10,
  last_modified: 'not-a-timestamp',
  tags: { source: 'user' }
}

const renderWithFiles = async (files) => {
  global.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => files })
  render(<AllFilesView />)
  await screen.findByText('dated.txt')
  await screen.findByText('undated.txt')
}

describe('AllFilesView - missing timestamp', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useChat.mockReturnValue({
      token: 'test-token',
      user: 'user@example.com',
      ensureSession: vi.fn(),
      addSystemEvent: vi.fn(),
      addPendingFileEvent: vi.fn(),
      attachments: new Map()
    })
    useWS.mockReturnValue({ sendMessage: vi.fn() })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows "Unknown" instead of the epoch for a file without a timestamp', async () => {
    await renderWithFiles([datedFile, undatedFile])

    expect(screen.getByText('Unknown')).toBeInTheDocument()
  })

  it('shows "Unknown" for a timestamp it cannot parse', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [datedFile, unparseableFile]
    })
    render(<AllFilesView />)

    await screen.findByText('unparseable.txt')
    expect(screen.getByText('Unknown')).toBeInTheDocument()
    expect(screen.queryByText('Invalid Date')).not.toBeInTheDocument()
  })

  it('sorts a file without a timestamp last, in both directions', async () => {
    await renderWithFiles([undatedFile, datedFile])

    const order = () =>
      screen
        .getAllByRole('heading', { level: 3 })
        .map((heading) => heading.textContent)
        .filter((text) => text.endsWith('.txt')) // the file rows, not the header

    // Newest first (the default): the dated file leads, the unknown one trails.
    await waitFor(() => expect(order()).toEqual(['dated.txt', 'undated.txt']))

    // Flipping the direction keeps the unknown timestamp last.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Date/ }))
    })
    await waitFor(() => expect(order()).toEqual(['dated.txt', 'undated.txt']))
  })

  it('sorts a file with an unparseable timestamp last as well', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [unparseableFile, datedFile]
    })
    render(<AllFilesView />)

    await screen.findByText('unparseable.txt')
    const order = () =>
      screen
        .getAllByRole('heading', { level: 3 })
        .map((heading) => heading.textContent)
        .filter((text) => text.endsWith('.txt'))

    await waitFor(() => expect(order()).toEqual(['dated.txt', 'unparseable.txt']))
  })
})
