/**
 * Tests for the bulk "Delete All" / "Delete Filtered" action in AllFilesView.
 *
 * Covers the destructive path: scope-aware labeling, the in-flight re-entry
 * guard, and partial-failure reporting.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import AllFilesView from '../components/AllFilesView'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/WSContext')

const makeFiles = (n) =>
  Array.from({ length: n }, (_, i) => ({
    key: `sessions/s1/file-${i}.txt`,
    filename: `file-${i}.txt`,
    size: 10,
    last_modified: '2026-06-01T00:00:00Z',
    tags: { source: i % 2 === 0 ? 'user' : 'tool' }
  }))

describe('AllFilesView - bulk delete', () => {
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
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  // Resolves the initial GET /api/files load, then lets the test control
  // subsequent fetch calls (the per-file DELETEs and the refresh GET).
  const primeInitialLoad = (files) => {
    const fetchMock = vi.fn()
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => files })
    global.fetch = fetchMock
    return fetchMock
  }

  it('labels the button "Delete All" and deletes every visible file', async () => {
    const files = makeFiles(3)
    const fetchMock = primeInitialLoad(files)
    // 3 DELETEs succeed, then the refresh GET returns an empty list.
    fetchMock.mockResolvedValue({ ok: true, json: async () => [] })

    render(<AllFilesView />)

    const button = await screen.findByRole('button', { name: /Delete All \(3\)/ })
    await act(async () => {
      fireEvent.click(button)
    })

    await waitFor(() => {
      expect(screen.getByText('Deleted 3 file(s) successfully')).toBeInTheDocument()
    })

    const deleteCalls = fetchMock.mock.calls.filter(([, opts]) => opts?.method === 'DELETE')
    expect(deleteCalls).toHaveLength(3)
  })

  it('relabels to "Delete Filtered" and confirms the filtered scope when a search is active', async () => {
    const files = makeFiles(4)
    const fetchMock = primeInitialLoad(files)
    fetchMock.mockResolvedValue({ ok: true, json: async () => [] })

    render(<AllFilesView />)

    await screen.findByRole('button', { name: /Delete All \(4\)/ })

    // Narrow to a single file via search.
    fireEvent.change(screen.getByPlaceholderText('Search files...'), {
      target: { value: 'file-1' }
    })

    const button = await screen.findByRole('button', { name: /Delete Filtered \(1\)/ })
    await act(async () => {
      fireEvent.click(button)
    })

    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringContaining('matching your current filters')
    )
    const deleteCalls = fetchMock.mock.calls.filter(([, opts]) => opts?.method === 'DELETE')
    expect(deleteCalls).toHaveLength(1)
  })

  it('guards against re-entry: a second click while deleting issues no extra requests', async () => {
    const files = makeFiles(2)
    const fetchMock = primeInitialLoad(files)

    // Hold the first DELETE open so the handler stays in flight.
    let releaseFirstDelete
    const pendingDelete = new Promise((resolve) => {
      releaseFirstDelete = () => resolve({ ok: true })
    })
    fetchMock.mockImplementationOnce(() => pendingDelete) // first DELETE (held)
    fetchMock.mockResolvedValue({ ok: true, json: async () => [] }) // later DELETE + refresh

    render(<AllFilesView />)

    const button = await screen.findByRole('button', { name: /Delete All \(2\)/ })

    await act(async () => {
      fireEvent.click(button)
    })

    // Button should now be disabled while in flight.
    const busyButton = screen.getByRole('button', { name: /Deleting/ })
    expect(busyButton).toBeDisabled()

    // A second click while busy must not start another batch.
    fireEvent.click(busyButton)

    await act(async () => {
      releaseFirstDelete()
      await pendingDelete
    })

    await waitFor(() => {
      expect(screen.getByText('Deleted 2 file(s) successfully')).toBeInTheDocument()
    })

    // window.confirm fired exactly once -> only one batch ran.
    expect(window.confirm).toHaveBeenCalledTimes(1)
  })

  it('reports partial failures with a count', async () => {
    const files = makeFiles(3)
    const fetchMock = primeInitialLoad(files)
    fetchMock
      .mockResolvedValueOnce({ ok: true }) // delete 1 ok
      .mockResolvedValueOnce({ ok: false }) // delete 2 fails
      .mockResolvedValueOnce({ ok: true }) // delete 3 ok
      .mockResolvedValue({ ok: true, json: async () => [] }) // refresh

    render(<AllFilesView />)

    const button = await screen.findByRole('button', { name: /Delete All \(3\)/ })
    await act(async () => {
      fireEvent.click(button)
    })

    await waitFor(() => {
      expect(screen.getByText('Failed to delete 1 of 3 file(s)')).toBeInTheDocument()
    })
  })
})
