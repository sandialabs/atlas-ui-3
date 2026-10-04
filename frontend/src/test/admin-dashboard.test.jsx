import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import AdminDashboard from '../components/AdminDashboard'
import { useWS } from '../contexts/WSContext'

vi.mock('../contexts/WSContext', () => ({ useWS: vi.fn() }))
vi.mock('../components/admin/BannerMessagesCard', () => ({ default: () => <div>Banner messages</div> }))
vi.mock('../components/admin/MCPConfigurationCard', () => ({ default: () => <div>MCP configuration</div> }))
vi.mock('../components/admin/ConfigViewerCard', () => ({ default: () => <div>Configuration viewer</div> }))
vi.mock('../components/admin/MCPServerManager', () => ({ default: () => <div>MCP servers</div> }))
vi.mock('../components/admin/FeedbackViewerCard', () => ({ default: () => <div>User feedback</div> }))

function renderDashboard() {
  return render(
    <MemoryRouter initialEntries={['/admin']}>
      <Routes>
        <Route path="/admin" element={<AdminDashboard />} />
        <Route path="/admin/logview" element={<div>Log viewer page</div>} />
        <Route path="/admin/telemetry" element={<div>Telemetry page</div>} />
        <Route path="/" element={<div>Chat page</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('AdminDashboard active controls', () => {
  beforeEach(() => {
    vi.mocked(useWS).mockReturnValue({ isConnected: true })
    vi.stubGlobal('fetch', vi.fn(async (url, options) => {
      const responses = {
        '/admin/': { user: 'admin@example.com' },
        '/admin/system-status': { overall_status: 'healthy' },
        '/admin/help-config': options?.method === 'PUT'
          ? { message: 'Help updated' }
          : { content: 'Original help', file_path: 'config/help.md' },
      }
      if (!(url in responses)) throw new Error(`Unexpected request: ${url}`)
      return { ok: true, json: async () => responses[url] }
    }))
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('loads the active cards and saves help through the shared admin actions', async () => {
    const user = userEvent.setup()
    renderDashboard()

    expect(await screen.findByText('Logged in as: admin@example.com')).toBeInTheDocument()
    for (const title of ['Banner messages', 'MCP configuration', 'Configuration viewer', 'MCP servers', 'User feedback']) {
      expect(screen.getByText(title)).toBeInTheDocument()
    }
    const editHelp = await screen.findByRole('button', { name: 'Edit Help Content' })
    await waitFor(() => expect(editHelp).toBeEnabled())
    await user.click(editHelp)
    const editor = await screen.findByRole('textbox')
    expect(editor).toHaveValue('Original help')
    await user.clear(editor)
    await user.type(editor, 'Updated help')
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))

    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/admin/help-config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: 'Updated help' }),
    }))
    expect(await screen.findByText('Configuration saved successfully: Help updated')).toBeInTheDocument()
  })

  it.each([
    ['View Logs', 'Log viewer page'],
    ['View Telemetry', 'Telemetry page'],
    ['Back to Chat', 'Chat page'],
  ])('keeps the %s navigation available', async (button, destination) => {
    const user = userEvent.setup()
    renderDashboard()
    await user.click(await screen.findByRole('button', { name: button }))
    expect(screen.getByText(destination)).toBeInTheDocument()
  })

  it('shows the disconnected banner without removing the active controls', async () => {
    vi.mocked(useWS).mockReturnValue({ isConnected: false })
    renderDashboard()
    expect(await screen.findByText('Backend disconnected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'View Logs' })).toBeInTheDocument()
  })
})
