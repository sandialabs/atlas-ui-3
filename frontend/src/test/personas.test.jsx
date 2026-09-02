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
import { usePersonas } from '../hooks/usePersonas'
import { personaKey, isPersonaKey, personaIdFromKey, isUserPromptKey } from '../hooks/chat/useSelections'
import { buildPromptInfoByKey, resolvePromptInfo } from '../utils/chatExport'

vi.mock('../contexts/ChatContext', () => ({ useChat: vi.fn() }))

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
})
