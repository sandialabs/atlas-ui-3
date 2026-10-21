/**
 * Externally hosted Help documentation (issue #1107).
 *
 * The three display modes behave differently at the two places a user meets
 * the Help page: the header button (which opens the in-app page or an
 * external site) and the page itself (which renders Markdown or points at
 * the external site).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'

vi.mock('../contexts/ChatContext', () => ({ useChat: vi.fn() }))
vi.mock('../contexts/WSContext', () => ({ useWS: vi.fn() }))
vi.mock('../contexts/MarketplaceContext', () => ({ useMarketplace: vi.fn() }))
vi.mock('react-router-dom', () => ({
  useNavigate: vi.fn(),
}))

import Header from '../components/Header'
import HelpPage from '../components/HelpPage'
import HelpConfigCard from '../components/admin/HelpConfigCard'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'
import { useMarketplace } from '../contexts/MarketplaceContext'
import { useNavigate } from 'react-router-dom'

const setChat = (over = {}) => {
  useChat.mockReturnValue({
    user: 'test@test.com',
    agentModeAvailable: false,
    agentModeEnabled: false,
    setAgentModeEnabled: vi.fn(),
    saveMode: 'none',
    setSaveMode: vi.fn(),
    openChatAsText: vi.fn(),
    messages: [],
    clearChat: vi.fn(),
    features: { tools: true },
    settings: {},
    complianceLevelFilter: null,
    setComplianceLevelFilter: vi.fn(),
    selectedDataSources: new Set(),
    ...over,
  })
}

const renderHeader = () => render(
  <Header
    onToggleSidebar={vi.fn()}
    onToggleRag={vi.fn()}
    onToggleFiles={vi.fn()}
    onToggleCanvas={vi.fn()}
    onCloseCanvas={vi.fn()}
    onToggleSettings={vi.fn()}
  />
)

const mockHelpResponse = (body) => {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    json: async () => body,
  })))
}

beforeEach(() => {
  vi.clearAllMocks()
  useWS.mockReturnValue({ isConnected: true, connectionStatus: 'connected' })
  useMarketplace.mockReturnValue({ complianceLevels: [] })
  useNavigate.mockReturnValue(vi.fn())
  setChat()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('header Help button', () => {
  it('opens the in-app Help page in the default local mode', () => {
    const navigate = vi.fn()
    useNavigate.mockReturnValue(navigate)
    renderHeader()

    fireEvent.click(screen.getByRole('button', { name: 'Help' }))
    expect(navigate).toHaveBeenCalledWith('/help')
  })

  it('opens the external documentation in a new tab in external-link mode', () => {
    const navigate = vi.fn()
    useNavigate.mockReturnValue(navigate)
    setChat({
      help: { mode: 'external_link', content_url: 'https://docs.example.org/atlas/' },
    })
    renderHeader()

    const link = screen.getByRole('link', { name: /Help/ })
    expect(link).toHaveAttribute('href', 'https://docs.example.org/atlas/')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    // The conversation stays where it is: nothing navigates.
    expect(navigate).not.toHaveBeenCalled()
  })

  it('falls back to the in-app page when the external mode has no URL', () => {
    const navigate = vi.fn()
    useNavigate.mockReturnValue(navigate)
    setChat({ help: { mode: 'external_link', content_url: '' } })
    renderHeader()

    fireEvent.click(screen.getByRole('button', { name: 'Help' }))
    expect(navigate).toHaveBeenCalledWith('/help')
  })
})

describe('Help page', () => {
  it('renders the Markdown the server returned', async () => {
    mockHelpResponse({ mode: 'remote_markdown', content: '# Hosted Help\n\nBody text.' })
    render(<HelpPage />)

    await waitFor(() => expect(screen.getByText('Hosted Help')).toBeInTheDocument())
    expect(screen.getByText('Body text.')).toBeInTheDocument()
  })

  it('renders the local Markdown in local mode', async () => {
    mockHelpResponse({ mode: 'local', content: '# Local Help' })
    render(<HelpPage />)

    await waitFor(() => expect(screen.getByText('Local Help')).toBeInTheDocument())
  })

  it('points at the external site in external-link mode', async () => {
    mockHelpResponse({
      mode: 'external_link',
      content: '',
      link_url: 'https://docs.example.org/atlas/',
    })
    render(<HelpPage />)

    const link = await screen.findByRole('link', { name: /Open the documentation/ })
    expect(link).toHaveAttribute('href', 'https://docs.example.org/atlas/')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('shows the configuration error the server reported', async () => {
    mockHelpResponse({
      mode: 'local',
      content: '# Local Help',
      error: "HELP_DISPLAY_MODE='remote_markdown' requires a usable HELP_CONTENT_URL",
    })
    render(<HelpPage />)

    expect(await screen.findByText('Help configuration problem')).toBeInTheDocument()
    expect(screen.getByText(/requires a usable HELP_CONTENT_URL/)).toBeInTheDocument()
  })

  it('reports a documentation host that is down separately from a misconfiguration', async () => {
    mockHelpResponse({
      mode: 'local',
      content: '# Local Help',
      error: null,
      fetch_error: 'the documentation host answered with HTTP 500',
      fallback: 'local',
    })
    render(<HelpPage />)

    expect(
      await screen.findByText('The hosted documentation is not available right now')
    ).toBeInTheDocument()
    expect(
      screen.getByText(/The local Help content is shown instead/)
    ).toBeInTheDocument()
    // A host that is down is not a configuration problem.
    expect(screen.queryByText('Help configuration problem')).not.toBeInTheDocument()
  })

  it('says when the copy on screen is a stale one', async () => {
    mockHelpResponse({
      mode: 'remote_markdown',
      content: '# Hosted Help',
      stale: true,
      fetch_error: 'could not reach the documentation host',
    })
    render(<HelpPage />)

    expect(
      await screen.findByText('The hosted documentation is not available right now')
    ).toBeInTheDocument()
    expect(
      screen.getByText(/This is the last copy that was fetched/)
    ).toBeInTheDocument()
  })

  it('reports a failed request', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500 })))
    render(<HelpPage />)

    expect(await screen.findByText(/Error loading help content/)).toBeInTheDocument()
  })
})

describe('admin Help card', () => {
  const renderCard = () => render(
    <HelpConfigCard openModal={vi.fn()} addNotification={vi.fn()} />
  )

  it('says the local editor is not what users see when help is hosted elsewhere', async () => {
    mockHelpResponse({
      content: '# Local help',
      file_path: 'config/help.md',
      mode: 'external_link',
      content_url: 'https://docs.example.org/atlas/',
    })
    renderCard()

    expect(await screen.findByText('Source: External documentation link')).toBeInTheDocument()
    expect(screen.getByText(/this file is not what users see/)).toBeInTheDocument()
    expect(
      screen.getByText(/^URL: https:\/\/docs\.example\.org\/atlas\/$/)
    ).toBeInTheDocument()
  })

  it('says the local editor is live in local mode', async () => {
    mockHelpResponse({
      content: '# Local help',
      file_path: 'config/help.md',
      mode: 'local',
      content_url: '',
    })
    renderCard()

    expect(await screen.findByText('Source: Local Markdown (this file)')).toBeInTheDocument()
    expect(screen.queryByText(/not what users see/)).not.toBeInTheDocument()
  })

  it('says the local file is only the fallback in remote-markdown mode', async () => {
    mockHelpResponse({
      content: '# Local help',
      file_path: 'config/help.md',
      mode: 'remote_markdown',
      content_url: 'https://docs.example.org/atlas/help.md',
    })
    renderCard()

    expect(
      await screen.findByText(/only shown when the documentation cannot be reached/)
    ).toBeInTheDocument()
    expect(screen.queryByText(/not what users see/)).not.toBeInTheDocument()
  })

  it('says the local file is not what users see in external-link mode', async () => {
    mockHelpResponse({
      content: '# Local help',
      file_path: 'config/help.md',
      mode: 'external_link',
      content_url: 'https://docs.example.org/atlas/',
    })
    renderCard()

    expect(await screen.findByText(/not what users see/)).toBeInTheDocument()
  })

  it('shows the configuration error', async () => {
    mockHelpResponse({
      content: '',
      file_path: 'config/help.md',
      mode: 'local',
      content_url: '',
      error: 'HELP_CONTENT_URL is empty',
    })
    renderCard()

    expect(await screen.findByText('Help configuration problem')).toBeInTheDocument()
    expect(screen.getByText('HELP_CONTENT_URL is empty')).toBeInTheDocument()
  })

  it('says the local file is still what users see when the external source is unusable', async () => {
    mockHelpResponse({
      content: '# Local help',
      file_path: 'config/help.md',
      mode: 'local',
      content_url: '',
      error: 'HELP_CONTENT_URL is empty',
    })
    renderCard()

    expect(await screen.findByText(/the Help page is showing this file/)).toBeInTheDocument()
    expect(screen.queryByText(/not what users see/)).not.toBeInTheDocument()
  })
})
