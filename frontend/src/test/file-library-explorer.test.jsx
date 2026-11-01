/**
 * The File Library is a folder explorer. A short library is filtered in the
 * browser. A long one is paged: the total comes from X-Total-Count and Next
 * asks for the following page instead of loading every file at once.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react'
import AllFilesView from '../components/AllFilesView'
import { categoryForFilename, filesQuery, filterAndSortFiles } from '../utils/fileLibraryModel'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/WSContext')

const notes = {
  key: 'users/a/uploads/1_aaaaaaaa_notes.txt',
  filename: 'notes.txt',
  size: 10,
  last_modified: '2026-06-01T00:00:00Z',
  tags: { source: 'user' },
}

const plot = {
  key: 'users/a/generated/2_bbbbbbbb_plot.png',
  filename: 'plot.png',
  size: 20,
  last_modified: '2026-06-02T00:00:00Z',
  tags: { source: 'tool' },
}

const jsonResponse = (files, total) => ({
  ok: true,
  json: async () => files,
  headers: {
    get: (name) => (name === 'X-Total-Count' ? String(total) : null),
  },
})

const bigPage = Array.from({ length: 50 }, (_, i) => ({
  key: `users/a/uploads/${1000 + i}_aaaaaaaa_batch_${i}.txt`,
  filename: `batch_${i}.txt`,
  size: 1,
  last_modified: '2026-06-01T00:00:00Z',
  tags: { source: 'user' },
}))

describe('file library model', () => {
  it('classifies the same folders the API does', () => {
    expect(categoryForFilename('plot.png')).toBe('image')
    expect(categoryForFilename('notes.txt')).toBe('document')
    expect(categoryForFilename('sheet.csv')).toBe('data')
    expect(categoryForFilename('main.py')).toBe('code')
    expect(categoryForFilename('archive.bin')).toBe('other')
  })

  it('matches a spaced name and ignores the storage path', () => {
    const files = [{
      key: 'users/me@example.com/uploads/1700000200_aaaaaaaa_my_report.pdf',
      filename: 'my_report.pdf',
      tags: { source: 'user' },
      last_modified: '2026-06-01T00:00:00Z',
    }]
    expect(filterAndSortFiles(files, { searchQuery: ' my report ' })).toHaveLength(1)
    expect(filterAndSortFiles(files, { searchQuery: 'uploads' })).toHaveLength(0)
    expect(filterAndSortFiles(files, { searchQuery: 'me@example.com' })).toHaveLength(0)

    const japanese = [{
      key: 'users/me@example.com/uploads/1700000200_bbbbbbbb_日本_報告.txt',
      filename: '日本 報告.txt',
      tags: { source: 'user' },
      last_modified: '2026-06-01T00:00:00Z',
    }]
    expect(filterAndSortFiles(japanese, { searchQuery: '日本 報告' })).toHaveLength(1)
    expect(filterAndSortFiles(japanese, { searchQuery: '報告' })).toHaveLength(1)
  })

  it('builds a paged query', () => {
    expect(filesQuery({
      offset: 50,
      source: 'tool',
      category: 'image',
      q: 'plot',
    })).toBe('/api/files?limit=50&offset=50&file_type=tool&category=image&q=plot')
  })
})

describe('AllFilesView explorer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useChat.mockReturnValue({
      token: 'test-token',
      user: 'user@example.com',
      ensureSession: vi.fn(),
      addSystemEvent: vi.fn(),
      addPendingFileEvent: vi.fn(),
      attachments: new Map(),
    })
    useWS.mockReturnValue({ sendMessage: vi.fn() })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('opens folders for source and type without leaving the library', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [notes, plot],
    })

    render(<AllFilesView />)
    await screen.findByText('notes.txt')
    expect(screen.getByText('plot.png')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Uploads' }))
    expect(screen.getByText('notes.txt')).toBeInTheDocument()
    expect(screen.queryByText('plot.png')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'File Library' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Images' }))
    expect(screen.getByText('plot.png')).toBeInTheDocument()
    expect(screen.queryByText('notes.txt')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'File Library' }))
    expect(screen.getByText('notes.txt')).toBeInTheDocument()
    expect(screen.getByText('plot.png')).toBeInTheDocument()
  })

  it('requests the next page instead of the whole library', async () => {
    const fetchMock = vi.fn()
    fetchMock.mockResolvedValueOnce(jsonResponse([notes, plot], 80))
    fetchMock.mockResolvedValueOnce(jsonResponse([{
      ...notes,
      key: 'users/a/uploads/3_cccccccc_later.txt',
      filename: 'later.txt',
    }], 80))
    global.fetch = fetchMock

    render(<AllFilesView />)
    await screen.findByText('Showing 1–2 of 80')

    expect(fetchMock.mock.calls[0][0]).toContain('limit=50')
    expect(fetchMock.mock.calls[0][0]).toContain('offset=0')

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    await waitFor(() => {
      expect(screen.getByText('later.txt')).toBeInTheDocument()
    })
    expect(fetchMock.mock.calls[1][0]).toContain('offset=50')
  })

  it('deletes only the rows on the current page', async () => {
    const fetchMock = vi.fn()
    fetchMock.mockResolvedValueOnce(jsonResponse([notes, plot], 80))
    fetchMock.mockResolvedValue({ ok: true, json: async () => [] })
    global.fetch = fetchMock
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<AllFilesView />)
    const button = await screen.findByRole('button', { name: /Delete Page \(2\)/ })

    await act(async () => {
      fireEvent.click(button)
    })

    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringContaining('on this page')
    )
    const deletes = fetchMock.mock.calls.filter(([, opts]) => opts?.method === 'DELETE')
    expect(deletes).toHaveLength(2)
    expect(deletes.map(([url]) => url).join(' ')).toContain('notes.txt')
    expect(deletes.map(([url]) => url).join(' ')).toContain('plot.png')
    expect(deletes.map(([url]) => url).join(' ')).not.toContain('later.txt')
  })

  it('drops a list response that finishes after a newer one', async () => {
    let resolveUploads
    const uploads = new Promise((resolve) => {
      resolveUploads = resolve
    })
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('file_type=user')) return uploads
      return Promise.resolve(jsonResponse([notes, plot], 80))
    })
    global.fetch = fetchMock

    render(<AllFilesView />)
    await screen.findByText('notes.txt')

    fireEvent.click(screen.getByRole('button', { name: 'Uploads' }))
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Breadcrumb' })).getByRole('button', { name: 'File Library' }))
    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(3)
    })

    resolveUploads(jsonResponse([{
      key: 'users/a/uploads/9_zzzzzzzz_stale.txt',
      filename: 'stale.txt',
      size: 1,
      last_modified: '2026-06-03T00:00:00Z',
      tags: { source: 'user' },
    }], 1))
    await act(async () => {
      await Promise.resolve()
    })

    expect(screen.queryByText('stale.txt')).not.toBeInTheDocument()
    expect(screen.getByText('notes.txt')).toBeInTheDocument()
    expect(screen.getByText('plot.png')).toBeInTheDocument()
  })

  it('says Delete Page whenever the result spans more than one page', async () => {
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('q=notes')) return Promise.resolve(jsonResponse([notes], 80))
      return Promise.resolve(jsonResponse(bigPage, 80))
    })
    global.fetch = fetchMock
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<AllFilesView />)
    await screen.findByText('batch_0.txt')

    fireEvent.change(screen.getByPlaceholderText('Search files...'), { target: { value: 'notes' } })

    const button = await screen.findByRole('button', { name: /Delete Page \(1\)/ }, { timeout: 3000 })
    expect(button.title).toContain('on this page')

    await act(async () => {
      fireEvent.click(button)
    })
    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringContaining('on this page')
    )
    const deletes = fetchMock.mock.calls.filter(([, opts]) => opts?.method === 'DELETE')
    expect(deletes).toHaveLength(1)
    expect(deletes[0][0]).toContain('notes.txt')
  })

  it('keeps the explorer up while an empty view reloads', async () => {
    let resolveNext
    const fetchMock = vi.fn((url) => {
      const query = String(url)
      if (query.includes('q=zzz')) return Promise.resolve(jsonResponse([], 0))
      if (query.includes('q=yyy')) {
        return new Promise((resolve) => {
          resolveNext = resolve
        })
      }
      return Promise.resolve(jsonResponse(bigPage, 80))
    })
    global.fetch = fetchMock

    render(<AllFilesView />)
    await screen.findByText('batch_0.txt')

    fireEvent.change(screen.getByPlaceholderText('Search files...'), { target: { value: 'zzz' } })
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('q=zzz'))).toBe(true)
    })
    // The server answer for the empty search has been applied.
    await screen.findByText('All files (0)')

    fireEvent.change(screen.getByPlaceholderText('Search files...'), { target: { value: 'yyy' } })
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('q=yyy'))).toBe(true)
    })

    // The reload of an empty view is an inline indicator, not a takeover.
    await waitFor(() => {
      expect(screen.getByText('Loading files...')).toBeInTheDocument()
    })
    expect(screen.getByRole('navigation', { name: 'Folders' })).toBeInTheDocument()

    const listCalls = fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/api/files'))
    expect(listCalls).toHaveLength(3)
    expect(listCalls.every(([, opts]) => opts?.signal != null)).toBe(true)

    resolveNext(jsonResponse([], 0))
    await waitFor(() => {
      expect(screen.queryByText('Loading files...')).not.toBeInTheDocument()
    })
  })

  it('offers a retry when the first load fails', async () => {
    const fetchMock = vi.fn()
    fetchMock.mockRejectedValueOnce(new Error('storage down'))
    fetchMock.mockResolvedValueOnce(jsonResponse([notes], 80))
    global.fetch = fetchMock

    render(<AllFilesView />)
    await screen.findByText('Error loading files')
    expect(screen.getByText('storage down')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await screen.findByText('notes.txt')
    const listCalls = fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/api/files'))
    expect(listCalls).toHaveLength(2)
  })

  it('notifies instead of replacing the explorer when a later load fails', async () => {
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('q=zzz')) return Promise.reject(new Error('timeout'))
      return Promise.resolve(jsonResponse(bigPage, 80))
    })
    global.fetch = fetchMock

    render(<AllFilesView />)
    await screen.findByText('batch_0.txt')

    fireEvent.change(screen.getByPlaceholderText('Search files...'), { target: { value: 'zzz' } })

    await screen.findByText('Failed to load files')
    expect(screen.queryByText('Error loading files')).not.toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Folders' })).toBeInTheDocument()
  })

  it('reloads the last valid page when the result shrinks below the offset', async () => {
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('offset=50')) return Promise.resolve(jsonResponse([], 50))
      return Promise.resolve(jsonResponse(bigPage, 80))
    })
    global.fetch = fetchMock

    render(<AllFilesView />)
    await screen.findByText('batch_0.txt')

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    // Page two came back empty with a total of 50, so page one reloads
    // instead of showing an empty library.
    await waitFor(() => {
      expect(screen.getByText('batch_0.txt')).toBeInTheDocument()
    })
    expect(screen.queryByText('No files found')).not.toBeInTheDocument()

    const listCalls = fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/api/files'))
    expect(listCalls).toHaveLength(3)
    expect(String(listCalls[1][0])).toContain('offset=50')
    expect(String(listCalls[2][0])).toContain('offset=0')
  })

  it('refreshes the current view after a delete that finished after navigation', async () => {
    let resolveDelete
    const fetchMock = vi.fn((url, opts) => {
      if (opts?.method === 'DELETE') {
        return new Promise((resolve) => {
          resolveDelete = resolve
        })
      }
      if (String(url).includes('file_type=tool')) {
        return Promise.resolve(jsonResponse([plot], 80))
      }
      return Promise.resolve(jsonResponse([notes], 80))
    })
    global.fetch = fetchMock
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<AllFilesView />)
    await screen.findByText('notes.txt')

    fireEvent.click(screen.getByTitle('Delete file'))
    // The delete is still pending when the user opens another folder.
    fireEvent.click(screen.getByRole('button', { name: 'Generated' }))
    await screen.findByText('plot.png')

    resolveDelete({ ok: true, json: async () => ({}) })

    await waitFor(() => {
      const listCalls = fetchMock.mock.calls.filter(([, opts]) => opts?.method !== 'DELETE')
      expect(listCalls.length).toBeGreaterThanOrEqual(3)
    })
    const listCalls = fetchMock.mock.calls.filter(([, opts]) => opts?.method !== 'DELETE')
    const lastListUrl = String(listCalls[listCalls.length - 1][0])
    expect(lastListUrl).toContain('file_type=tool')
    expect(lastListUrl).not.toContain('file_type=user')

    // The generated folder stays on screen; the stale view never returns.
    await waitFor(() => {
      expect(screen.getByText('plot.png')).toBeInTheDocument()
    })
    expect(screen.queryByText('notes.txt')).not.toBeInTheDocument()
  })

  it('rolls back to the loaded folder when a folder switch fails', async () => {
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('file_type=tool')) return Promise.reject(new Error('boom'))
      return Promise.resolve(jsonResponse(bigPage, 80))
    })
    global.fetch = fetchMock

    render(<AllFilesView />)
    await screen.findByText('batch_0.txt')

    fireEvent.click(screen.getByRole('button', { name: 'Generated' }))

    await screen.findByText('Failed to load files')

    // The failed switch never loaded, so the heading and rows return to
    // the folder that is actually loaded and the delete stays truthful.
    expect(screen.getByText('All files (80)')).toBeInTheDocument()
    await screen.findByText('batch_0.txt')
    expect(screen.queryByText(/Generated \(/)).not.toBeInTheDocument()
  })

  it('refreshes the restored view after a delete that follows a failed switch', async () => {
    const fetchMock = vi.fn((url, opts) => {
      if (opts?.method === 'DELETE') return Promise.resolve({ ok: true, json: async () => ({}) })
      if (String(url).includes('file_type=tool')) return Promise.reject(new Error('boom'))
      return Promise.resolve(jsonResponse(bigPage, 80))
    })
    global.fetch = fetchMock
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<AllFilesView />)
    await screen.findByText('batch_0.txt')

    fireEvent.click(screen.getByRole('button', { name: 'Generated' }))
    await screen.findByText('Failed to load files')
    await screen.findByText('batch_0.txt')

    fireEvent.click(screen.getAllByTitle('Delete file')[0])

    await waitFor(() => {
      const listCalls = fetchMock.mock.calls.filter(([, opts]) => opts?.method !== 'DELETE')
      expect(listCalls.length).toBeGreaterThanOrEqual(3)
    })
    const listCalls = fetchMock.mock.calls.filter(([, opts]) => opts?.method !== 'DELETE')
    const lastListUrl = String(listCalls[listCalls.length - 1][0])
    // The refresh follows the restored folder, not the one that failed.
    expect(lastListUrl).not.toContain('file_type=')
  })

  it('shows the API detail when the list fails with a JSON error', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      statusText: '',
      json: async () => ({ detail: 'The file library is too large to list' }),
    })

    render(<AllFilesView />)

    await screen.findByText('The file library is too large to list')
  })

  it('hides the pager when a later page comes back empty', async () => {
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('offset=50')) return Promise.resolve(jsonResponse([], 0))
      return Promise.resolve(jsonResponse(bigPage, 80))
    })
    global.fetch = fetchMock

    render(<AllFilesView />)
    await screen.findByText('batch_0.txt')

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    await screen.findByText('No files found')
    expect(screen.getByText('All files (0)')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Previous' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument()
  })

  it('keeps newer typing when an older search load fails', async () => {
    let rejectFirst
    const fetchMock = vi.fn((url) => {
      const query = String(url)
      if (query.includes('q=aaa')) {
        return new Promise((resolve, reject) => {
          rejectFirst = reject
        })
      }
      if (query.includes('q=bbb')) return Promise.resolve(jsonResponse([], 0))
      return Promise.resolve(jsonResponse(bigPage, 80))
    })
    global.fetch = fetchMock

    render(<AllFilesView />)
    await screen.findByText('batch_0.txt')

    fireEvent.change(screen.getByPlaceholderText('Search files...'), { target: { value: 'aaa' } })
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('q=aaa'))).toBe(true)
    })

    // The user keeps typing while the first search is still in flight; the
    // newer text's own reload has not started yet.
    fireEvent.change(screen.getByPlaceholderText('Search files...'), { target: { value: 'bbb' } })

    await act(async () => {
      rejectFirst(new Error('late failure'))
    })
    await screen.findByText('Failed to load files')

    // The newer text survives the older load's failure, and its pending
    // reload still runs.
    expect(screen.getByPlaceholderText('Search files...').value).toBe('bbb')
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('q=bbb'))).toBe(true)
    })
  })

  it('keeps the chosen folder when a redundant refresh fails', async () => {
    let listCalls = 0
    const fetchMock = vi.fn((url, opts) => {
      if (opts?.method === 'DELETE') return Promise.resolve({ ok: true, json: async () => ({}) })
      listCalls += 1
      if (listCalls === 1) return Promise.resolve(jsonResponse([notes, plot], 2))
      return Promise.reject(new Error('refresh failed'))
    })
    global.fetch = fetchMock
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<AllFilesView />)
    await screen.findByText('notes.txt')

    // Two files with a total of two: the library is complete, so folder
    // switches filter in the browser without a reload.
    fireEvent.click(screen.getByRole('button', { name: 'Uploads' }))
    expect(screen.getByText('Uploads (1)')).toBeInTheDocument()
    expect(screen.queryByText('plot.png')).not.toBeInTheDocument()

    fireEvent.click(screen.getAllByTitle('Delete file')[0])
    await screen.findByText('Failed to load files')

    // The failed refresh changed nothing about the view.
    await screen.findByText('notes.txt')
    expect(screen.getByText('Uploads (1)')).toBeInTheDocument()
    expect(screen.queryByText('plot.png')).not.toBeInTheDocument()
  })

  it('rolls a failed search on page two back to the page that loaded', async () => {
    const pageTwo = Array.from({ length: 30 }, (_, i) => ({
      key: `users/a/uploads/${2000 + i}_aaaaaaaa_p2_${i}.txt`,
      filename: `p2_${i}.txt`,
      size: 1,
      last_modified: '2026-06-02T00:00:00Z',
      tags: { source: 'user' },
    }))
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('q=')) return Promise.reject(new Error('search failed'))
      if (String(url).includes('offset=50')) return Promise.resolve(jsonResponse(pageTwo, 80))
      return Promise.resolve(jsonResponse(bigPage, 80))
    })
    global.fetch = fetchMock

    render(<AllFilesView />)
    await screen.findByText('batch_0.txt')

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByText('p2_0.txt')
    expect(screen.getByText('Showing 51–80 of 80')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('Search files...'), { target: { value: 'notes' } })
    await screen.findByText('Failed to load files')

    // The failed search returns the explorer to the loaded page 2 view.
    await screen.findByText('p2_0.txt')
    expect(screen.getByText('All files (80)')).toBeInTheDocument()
    expect(screen.getByText('Showing 51–80 of 80')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Search files...').value).toBe('')
  })

  it('pages without a pending search reload overriding the page', async () => {
    const pageTwo = Array.from({ length: 30 }, (_, i) => ({
      key: `users/a/uploads/${2000 + i}_aaaaaaaa_p2_${i}.txt`,
      filename: `p2_${i}.txt`,
      size: 1,
      last_modified: '2026-06-02T00:00:00Z',
      tags: { source: 'user' },
    }))
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('offset=50')) return Promise.resolve(jsonResponse(pageTwo, 80))
      return Promise.resolve(jsonResponse(bigPage, 80))
    })
    global.fetch = fetchMock

    render(<AllFilesView />)
    await screen.findByText('batch_0.txt')

    // The search's debounced reload is still pending when the user turns
    // the page; the explicit page turn wins.
    fireEvent.change(screen.getByPlaceholderText('Search files...'), { target: { value: 'p2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    await screen.findByText('p2_0.txt')
    expect(screen.getByText('Sorting applies to this page')).toBeInTheDocument()

    await new Promise((resolve) => setTimeout(resolve, 350))
    const listCalls = fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/api/files'))
    expect(listCalls).toHaveLength(2)
    expect(String(listCalls[1][0])).toContain('offset=50')
    expect(screen.getByText('Showing 51–80 of 80')).toBeInTheDocument()
  })
})
