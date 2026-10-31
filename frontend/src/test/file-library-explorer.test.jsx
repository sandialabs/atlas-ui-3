/**
 * The File Library is a folder explorer. A short library is filtered in the
 * browser. A long one is paged: the total comes from X-Total-Count and Next
 * asks for the following page instead of loading every file at once.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import AllFilesView from '../components/AllFilesView'
import { categoryForFilename, filesQuery } from '../utils/fileLibraryModel'
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

describe('file library model', () => {
  it('classifies the same folders the API does', () => {
    expect(categoryForFilename('plot.png')).toBe('image')
    expect(categoryForFilename('notes.txt')).toBe('document')
    expect(categoryForFilename('sheet.csv')).toBe('data')
    expect(categoryForFilename('main.py')).toBe('code')
    expect(categoryForFilename('archive.bin')).toBe('other')
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
})
