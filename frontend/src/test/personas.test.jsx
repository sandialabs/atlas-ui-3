/**
 * Preconfigured personas (issue #880).
 *
 * Covers the loader hook, the selector section, and the export labelling —
 * i.e. that a persona reaches the user as a selectable system prompt.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { renderHook } from '@testing-library/react'
import PromptSelector from '../components/PromptSelector'
import { useChat } from '../contexts/ChatContext'
import { useMarketplace } from '../contexts/MarketplaceContext'
import { usePersonas } from '../hooks/usePersonas'
import { personaKey, isPersonaKey, personaIdFromKey, isUserPromptKey } from '../hooks/chat/useSelections'
import { buildPromptInfoByKey, resolvePromptInfo } from '../utils/chatExport'

vi.mock('../contexts/ChatContext', () => ({ useChat: vi.fn() }))
vi.mock('../contexts/MarketplaceContext', () => ({ useMarketplace: vi.fn() }))

// Default: everything accessible (compliance filtering is a no-op). Individual
// tests override with a strict implementation. Set at module scope because
// clearAllMocks clears call history, not mockReturnValue implementations.
useMarketplace.mockReturnValue({ isComplianceAccessible: () => true })

vi.mock('lucide-react', () => ({
  ChevronDown: () => <span>v</span>,
  Sparkles: () => <span>*</span>,
  User: () => <span>u</span>,
  Users: () => <span>U</span>,
}))

const PERSONAS = [
  { id: 'research-assistant', name: 'Research Assistant', description: 'Careful answers', content: 'You are careful.' },
  { id: 'code-reviewer', name: 'Code Reviewer', description: '', content: 'You review code.' },
]

const baseContext = {
  prompts: [],
  selectedPrompts: new Set(),
  activePromptKey: null,
  personas: [],
  userPrompts: [],
  features: {},
  togglePrompt: vi.fn(),
  makePromptActive: vi.fn(),
  clearActivePrompt: vi.fn(),
  removePrompts: vi.fn(),
}

describe('persona keys', () => {
  it('round-trips an id and stays distinct from user prompt keys', () => {
    const key = personaKey('research-assistant')
    expect(key).toBe('persona:research-assistant')
    expect(isPersonaKey(key)).toBe(true)
    expect(personaIdFromKey(key)).toBe('research-assistant')
    expect(isUserPromptKey(key)).toBe(false)
    expect(isPersonaKey('atlas_summarize')).toBe(false)
  })
})

describe('usePersonas', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('loads personas from /api/personas', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({ personas: PERSONAS }),
    })))

    const { result } = renderHook(() => usePersonas())

    await waitFor(() => expect(result.current.personas).toHaveLength(2))
    expect(result.current.error).toBeNull()
    expect(global.fetch).toHaveBeenCalledWith('/api/personas')
  })

  it('reports an error and stays empty when the request fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500 })))

    const { result } = renderHook(() => usePersonas())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.personas).toEqual([])
    expect(result.current.error).toMatch(/500/)
  })
})

describe('PromptSelector personas section', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('is hidden when no personas are configured', () => {
    useChat.mockReturnValue(baseContext)
    render(<PromptSelector />)
    fireEvent.click(screen.getByRole('button'))
    expect(screen.queryByText('Personas')).not.toBeInTheDocument()
  })

  it('lists personas and activates the one clicked', () => {
    const makePromptActive = vi.fn()
    useChat.mockReturnValue({ ...baseContext, personas: PERSONAS, makePromptActive })

    render(<PromptSelector />)
    fireEvent.click(screen.getAllByRole('button')[0])

    expect(screen.getByText('Personas')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Research Assistant'))

    expect(makePromptActive).toHaveBeenCalledWith('persona:research-assistant')
  })

  it('shows the active persona name on the button', () => {
    useChat.mockReturnValue({
      ...baseContext,
      personas: PERSONAS,
      activePromptKey: 'persona:code-reviewer',
    })

    render(<PromptSelector />)

    expect(screen.getByText('Code Reviewer')).toBeInTheDocument()
  })
})

describe('chat export labelling', () => {
  it('names personas in the exported prompt info', () => {
    const info = buildPromptInfoByKey([], [], PERSONAS)
    const resolved = resolvePromptInfo('persona:research-assistant', info)

    expect(resolved.name).toBe('Research Assistant')
    expect(resolved.server).toBe('personas')
  })
})

describe('usePersonas loaded flag', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('stays false until a fetch succeeds', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 503 })))
    const { result } = renderHook(() => usePersonas())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.loaded).toBe(false)
  })

  it('is true after a successful empty response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ personas: [] }) })))
    const { result } = renderHook(() => usePersonas())

    await waitFor(() => expect(result.current.loaded).toBe(true))
    expect(result.current.personas).toEqual([])
  })
})

describe('persona description fallback', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('truncates a long prompt used as the fallback description', () => {
    const long = 'x'.repeat(5000)
    useChat.mockReturnValue({
      ...baseContext,
      personas: [{ id: 'long', name: 'Long', description: '', content: long }],
    })

    render(<PromptSelector />)
    fireEvent.click(screen.getAllByRole('button')[0])

    const rendered = screen.getByText(/^x+\.\.\.$/)
    expect(rendered.textContent.length).toBeLessThan(200)
  })

  it('prefers the server-computed preview over the prompt body', () => {
    // The list endpoint ships a 160-char preview instead of the full content.
    useChat.mockReturnValue({
      ...baseContext,
      personas: [{ id: 'p', name: 'Previewed', description: '', preview: 'Server preview', content: 'Full body' }],
    })

    render(<PromptSelector />)
    fireEvent.click(screen.getAllByRole('button')[0])

    expect(screen.getByText('Server preview')).toBeInTheDocument()
    expect(screen.queryByText('Full body')).not.toBeInTheDocument()
  })
})

describe('PromptSelector compliance filtering', () => {
  const LEVELED = [
    { id: 'int', name: 'Internal One', description: '', compliance_level: 'Internal' },
    { id: 'pub', name: 'Public One', description: '', compliance_level: 'Public' },
    { id: 'free', name: 'No Level', description: '' },
  ]

  // Mirrors MarketplaceContext.isComplianceAccessible (strict mode).
  const strictAccessible = (userLevel, resourceLevel) => {
    if (!userLevel) return true
    if (!resourceLevel) return false
    const levels = { Internal: ['Internal'], Public: ['Public'] }
    return (levels[userLevel] || []).includes(resourceLevel)
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useMarketplace.mockReturnValue({ isComplianceAccessible: strictAccessible })
  })

  const openPicker = () => {
    render(<PromptSelector />)
    fireEvent.click(screen.getAllByRole('button')[0])
  }

  it('shows only personas the active compliance level allows', () => {
    useChat.mockReturnValue({
      ...baseContext,
      personas: LEVELED,
      complianceLevelFilter: 'Internal',
      features: { compliance_levels: true },
    })

    openPicker()

    expect(screen.getByText('Internal One')).toBeInTheDocument()
    expect(screen.queryByText('Public One')).not.toBeInTheDocument()
    // A level-less persona is hidden while a filter is active (strict mode).
    expect(screen.queryByText('No Level')).not.toBeInTheDocument()
  })

  it('shows every persona when no compliance filter is set', () => {
    useChat.mockReturnValue({
      ...baseContext,
      personas: LEVELED,
      complianceLevelFilter: null,
      features: { compliance_levels: true },
    })

    openPicker()

    expect(screen.getByText('Internal One')).toBeInTheDocument()
    expect(screen.getByText('Public One')).toBeInTheDocument()
    expect(screen.getByText('No Level')).toBeInTheDocument()
  })

  it('ignores the filter when the compliance feature is off', () => {
    useChat.mockReturnValue({
      ...baseContext,
      personas: LEVELED,
      complianceLevelFilter: 'Internal',
      features: {},
    })

    openPicker()

    expect(screen.getByText('Public One')).toBeInTheDocument()
    expect(screen.getByText('No Level')).toBeInTheDocument()
  })
})

describe('PromptSelector personas load failure', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('shows an inline error with a retry instead of rendering nothing', () => {
    const fetchPersonas = vi.fn()
    useChat.mockReturnValue({
      ...baseContext,
      personas: [],
      personasError: 'Failed to load personas (500)',
      fetchPersonas,
    })

    render(<PromptSelector />)
    fireEvent.click(screen.getAllByRole('button')[0])

    // A failed load must be distinguishable from "no personas configured".
    expect(screen.getByText('Personas')).toBeInTheDocument()
    expect(screen.getByText(/Could not load personas/)).toBeInTheDocument()

    fireEvent.click(screen.getByText('Retry'))
    expect(fetchPersonas).toHaveBeenCalled()
  })

  it('renders no error block when the load succeeded', () => {
    useChat.mockReturnValue({ ...baseContext, personas: PERSONAS })

    render(<PromptSelector />)
    fireEvent.click(screen.getAllByRole('button')[0])

    expect(screen.queryByText(/Could not load personas/)).not.toBeInTheDocument()
  })
})
