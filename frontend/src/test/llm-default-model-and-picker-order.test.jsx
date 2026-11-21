/**
 * llmconfig.yml model picker order and default model / default LiteLLM team.
 *
 * - `model_picker_order: gateways_first` puts the LiteLLM team pickers above
 *   the static models in the dropdown.
 * - A gateway's `default_team` is preselected in its team dropdown, and its
 *   `default_model` is marked in that team's model list.
 * - A fresh session (no saved pick) starts on the configured default: a
 *   static `default_model` directly, or a LiteLLM team default the server
 *   confirms for the user (GET /api/llm/default-model), with a fallback to
 *   the static default when the user is not in the team.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, renderHook, act } from '@testing-library/react'
import ModelSelector from '../components/ModelSelector'
import { useChat } from '../contexts/ChatContext'
import {
  useChatConfig,
  defaultModelOf,
  hasGatewayDefault,
  fetchServerDefaultModel,
} from '../hooks/chat/useChatConfig'
import { rememberLastTeam, teamLabelFor } from '../utils/gatewayModels'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext', () => ({ useOptionalMarketplace: () => null }))
vi.mock('../hooks/useLLMAuthStatus', () => ({
  useLLMAuthStatus: () => ({
    fetchAuthStatus: vi.fn(),
    getModelAuth: () => null,
    uploadToken: vi.fn(),
    loading: false,
    error: null,
  }),
}))

const USER = 'test@test.com'
const ALPHA_KEY = 'enterprise::team-alpha::gpt-4o-mini'

const GATEWAY = {
  name: 'enterprise',
  display_name: 'Enterprise LiteLLM',
  supports_tools: true,
  supports_vision: false,
}
const GATEWAY_WITH_DEFAULTS = { ...GATEWAY, default_team_id: 'team-alpha', default_model_id: 'gpt-4o-mini' }

const STATIC_MODELS = [{ name: 'static-a' }, { name: 'static-b' }]

describe('default model helpers', () => {
  it('defaultModelOf prefers a static default_model, else the first static model', () => {
    expect(defaultModelOf({ models: STATIC_MODELS })).toBe('static-a')
    expect(defaultModelOf({ models: STATIC_MODELS, default_model: 'static-b' })).toBe('static-b')
    // A default the user is not offered (or a gateway key) is not a static pick.
    expect(defaultModelOf({ models: STATIC_MODELS, default_model: 'gone' })).toBe('static-a')
    expect(defaultModelOf({ models: STATIC_MODELS, default_model: ALPHA_KEY })).toBe('static-a')
    expect(defaultModelOf({ models: [] })).toBe('')
    expect(defaultModelOf({ models: ['plain-name'] })).toBe('plain-name')
  })

  it('hasGatewayDefault is true for a gateway-key default or a gateway team default', () => {
    expect(hasGatewayDefault({ llm_gateways: [GATEWAY] })).toBe(false)
    expect(hasGatewayDefault({ llm_gateways: [GATEWAY], default_model: 'static-a' })).toBe(false)
    expect(hasGatewayDefault({ llm_gateways: [GATEWAY], default_model: ALPHA_KEY })).toBe(true)
    expect(hasGatewayDefault({ llm_gateways: [GATEWAY_WITH_DEFAULTS] })).toBe(true)
    // A default team alone only preselects the team; it starts nobody on a model.
    expect(hasGatewayDefault({ llm_gateways: [{ ...GATEWAY, default_team_id: 'team-alpha' }] })).toBe(false)
  })

  describe('fetchServerDefaultModel', () => {
    afterEach(() => vi.unstubAllGlobals())

    it('returns the model, empty when none, null on failure', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ model: ALPHA_KEY }) })))
      expect(await fetchServerDefaultModel()).toBe(ALPHA_KEY)
      expect(fetch).toHaveBeenCalledWith('/api/llm/default-model')
      vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ model: null }) })))
      expect(await fetchServerDefaultModel()).toBe('')
      vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })))
      expect(await fetchServerDefaultModel()).toBeNull()
      vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline') }))
      expect(await fetchServerDefaultModel()).toBeNull()
    })
  })
})

describe('useChatConfig starting model from llmconfig.yml', () => {
  let defaultModelResponse
  let teamsResponse

  const config = (extra = {}) => ({
    app_name: 'Atlas',
    user: USER,
    models: STATIC_MODELS,
    llm_gateways: [GATEWAY_WITH_DEFAULTS],
    features: {},
    ...extra,
  })

  function mockFetch(cfg) {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (url.startsWith('/api/config')) return { ok: true, json: async () => cfg }
      if (url === '/api/llm/default-model') return defaultModelResponse()
      if (url.startsWith('/api/llm/gateways/enterprise/teams')) return teamsResponse()
      return { ok: false, json: async () => ({}) }
    }))
  }

  const calledDefaultEndpoint = () => fetch.mock.calls.some(([url]) => url === '/api/llm/default-model')

  beforeEach(() => {
    localStorage.clear()
    defaultModelResponse = () => ({ ok: true, json: async () => ({ model: ALPHA_KEY, source: 'configured' }) })
    teamsResponse = () => ({ ok: true, json: async () => ({ teams: [{ team_id: 'team-alpha', label: 'Alpha' }] }) })
  })

  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it('exposes model_picker_order, defaulting to models_first', async () => {
    mockFetch(config({ llm_gateways: [GATEWAY] }))
    const plain = renderHook(() => useChatConfig())
    await waitFor(() => expect(plain.result.current.currentModel).toBe('static-a'))
    expect(plain.result.current.modelPickerOrder).toBe('models_first')
    plain.unmount()

    mockFetch(config({ llm_gateways: [GATEWAY], model_picker_order: 'gateways_first' }))
    const first = renderHook(() => useChatConfig())
    await waitFor(() => expect(first.result.current.modelPickerOrder).toBe('gateways_first'))
  })

  it('starts on a static default_model without asking the server', async () => {
    mockFetch(config({ llm_gateways: [GATEWAY], default_model: 'static-b' }))
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(result.current.currentModel).toBe('static-b'))
    expect(result.current.defaultModel).toBe('static-b')
    expect(calledDefaultEndpoint()).toBe(false)
  })

  it('starts a fresh session on the default LiteLLM team model the server confirms', async () => {
    defaultModelResponse = () => ({ ok: true, json: async () => (
      { model: ALPHA_KEY, source: 'configured', team_label: 'Project Alpha' }) })
    mockFetch(config())
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(result.current.currentModel).toBe(ALPHA_KEY))
    // The picker names the team, not its id.
    expect(teamLabelFor('enterprise', 'team-alpha', USER)).toBe('Project Alpha')
    expect(result.current.models.find(m => m.name === ALPHA_KEY).display_name).toBe('gpt-4o-mini (Project Alpha)')
    expect(localStorage.getItem(`chatui-current-model:${USER}`)).toBe(ALPHA_KEY)
    // The selected team model joins the model list like any configured model.
    expect(result.current.models.some(m => m.name === ALPHA_KEY)).toBe(true)
  })

  it('falls back to the static default when the user is not in the default team', async () => {
    // The server checked membership and fell back itself.
    defaultModelResponse = () => ({ ok: true, json: async () => ({ model: 'static-a', source: 'first_model' }) })
    mockFetch(config())
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(result.current.currentModel).toBe('static-a'))
  })

  it('falls back to the static default_model when the lookup fails', async () => {
    defaultModelResponse = () => ({ ok: false, json: async () => ({}) })
    mockFetch(config({ default_model: 'static-b' }))
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(result.current.currentModel).toBe('static-b'))
  })

  it('keeps a saved pick over any configured default', async () => {
    localStorage.setItem(`chatui-current-model:${USER}`, 'static-b')
    mockFetch(config())
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(result.current.currentModel).toBe('static-b'))
    expect(calledDefaultEndpoint()).toBe(false)
  })

  it('replaces a saved pick the config no longer offers with the default', async () => {
    localStorage.setItem(`chatui-current-model:${USER}`, 'retired-model')
    mockFetch(config())
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(result.current.currentModel).toBe(ALPHA_KEY))
  })

  it('leaves a gateway-only user without a usable default to pick one', async () => {
    defaultModelResponse = () => ({ ok: true, json: async () => ({ model: null, source: null }) })
    mockFetch(config({ models: [] }))
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(calledDefaultEndpoint()).toBe(true))
    await new Promise(r => setTimeout(r, 0))
    expect(result.current.currentModel).toBe('')
  })

  it('starts a gateway-only member on the default team model', async () => {
    mockFetch(config({ models: [] }))
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(result.current.currentModel).toBe(ALPHA_KEY))
  })

  it('does not override a pick made while the lookup is in flight', async () => {
    let release
    defaultModelResponse = () => new Promise(resolve => {
      release = () => resolve({ ok: true, json: async () => ({ model: ALPHA_KEY }) })
    })
    mockFetch(config())
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(calledDefaultEndpoint()).toBe(true))
    result.current.setCurrentModel('static-b')
    await waitFor(() => expect(result.current.currentModel).toBe('static-b'))
    release()
    await new Promise(r => setTimeout(r, 10))
    expect(result.current.currentModel).toBe('static-b')
  })

  it('keeps a pick made during the lookup in a gateway-only deployment', async () => {
    let release
    defaultModelResponse = () => new Promise(resolve => {
      release = () => resolve({ ok: true, json: async () => ({ model: ALPHA_KEY }) })
    })
    mockFetch(config({ models: [] }))
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(calledDefaultEndpoint()).toBe(true))
    // No static default to apply first: nothing is selected yet.
    expect(result.current.currentModel).toBe('')
    act(() => result.current.setCurrentModel('enterprise::team-beta::llama'))
    release()
    await new Promise(r => setTimeout(r, 10))
    expect(result.current.currentModel).toBe('enterprise::team-beta::llama')
    expect(localStorage.getItem(`chatui-current-model:${USER}`)).toBe('enterprise::team-beta::llama')
    expect(localStorage.getItem(`chatui-current-model-auto:${USER}`)).toBeNull()
  })

  it('shows the static default at once and upgrades it when the server answers', async () => {
    let release
    defaultModelResponse = () => new Promise(resolve => {
      release = () => resolve({ ok: true, json: async () => ({ model: ALPHA_KEY }) })
    })
    mockFetch(config())
    const { result } = renderHook(() => useChatConfig())
    // Usable before the LiteLLM lookup returns.
    await waitFor(() => expect(result.current.currentModel).toBe('static-a'))
    await waitFor(() => expect(calledDefaultEndpoint()).toBe(true))
    release()
    await waitFor(() => expect(result.current.currentModel).toBe(ALPHA_KEY))
  })

  it('does not let one failed lookup pin the fallback: the next load retries', async () => {
    defaultModelResponse = () => ({ ok: false, json: async () => ({}) })
    mockFetch(config())
    const first = renderHook(() => useChatConfig())
    await waitFor(() => expect(calledDefaultEndpoint()).toBe(true))
    await waitFor(() => expect(first.result.current.currentModel).toBe('static-a'))
    expect(localStorage.getItem(`chatui-current-model-auto:${USER}`)).toBe('1')
    first.unmount()

    defaultModelResponse = () => ({ ok: true, json: async () => ({ model: ALPHA_KEY }) })
    mockFetch(config())
    const second = renderHook(() => useChatConfig())
    await waitFor(() => expect(second.result.current.currentModel).toBe(ALPHA_KEY))
  })

  it('keeps an explicit pick across loads and never re-resolves it', async () => {
    mockFetch(config())
    const first = renderHook(() => useChatConfig())
    await waitFor(() => expect(first.result.current.currentModel).toBe(ALPHA_KEY))
    act(() => first.result.current.setCurrentModel('static-b'))
    expect(localStorage.getItem(`chatui-current-model-auto:${USER}`)).toBeNull()
    first.unmount()

    mockFetch(config())
    const second = renderHook(() => useChatConfig())
    await waitFor(() => expect(second.result.current.currentModel).toBe('static-b'))
    expect(calledDefaultEndpoint()).toBe(false)
  })

  it('follows a changed static default for a user who never picked a model', async () => {
    localStorage.setItem(`chatui-current-model:${USER}`, 'static-a')
    localStorage.setItem(`chatui-current-model-auto:${USER}`, '1')
    mockFetch(config({ llm_gateways: [GATEWAY], default_model: 'static-b' }))
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(result.current.currentModel).toBe('static-b'))
  })

  it('moves a saved team model the user lost access to onto the configured default', async () => {
    localStorage.setItem(`chatui-current-model:${USER}`, 'enterprise::team-beta::llama')
    // Still listed in Alpha (the default team), no longer in Beta.
    mockFetch(config())
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(result.current.currentModel).toBe(ALPHA_KEY))
    expect(localStorage.getItem(`chatui-current-model:${USER}`)).toBe(ALPHA_KEY)
  })

  it('moves a saved team model to the static default when the server offers no team default', async () => {
    localStorage.setItem(`chatui-current-model:${USER}`, 'enterprise::team-beta::llama')
    teamsResponse = () => ({ ok: true, json: async () => ({ teams: [] }) })
    defaultModelResponse = () => ({ ok: true, json: async () => ({ model: 'static-a', source: 'first_model' }) })
    mockFetch(config({ default_model: 'static-b' }))
    const { result } = renderHook(() => useChatConfig())
    await waitFor(() => expect(result.current.currentModel).toBe('static-a'))
  })
})

// ---------------------------------------------------------------------------
// The dropdown
// ---------------------------------------------------------------------------

const TEAMS = { teams: [
  { team_id: 'team-alpha', label: 'Project Alpha' },
  { team_id: 'team-beta', label: 'Project Beta' },
] }
const MODELS = {
  'team-alpha': { models: [
    { name: ALPHA_KEY, model_id: 'gpt-4o-mini', label: 'gpt-4o-mini' },
    { name: 'enterprise::team-alpha::claude-sonnet', model_id: 'claude-sonnet', label: 'claude-sonnet' },
  ] },
  'team-beta': { models: [
    { name: 'enterprise::team-beta::llama-3.3-70b', model_id: 'llama-3.3-70b', label: 'llama-3.3-70b' },
  ] },
}

function mockPickerFetch(teams = TEAMS) {
  global.fetch = vi.fn(async (url) => {
    const parsed = new URL(url, 'http://localhost')
    if (parsed.pathname === '/api/llm/gateways/enterprise/teams') {
      return { ok: true, status: 200, json: async () => teams }
    }
    if (parsed.pathname === '/api/llm/gateways/enterprise/models') {
      const body = MODELS[parsed.searchParams.get('team_id')]
      if (!body) return { ok: false, status: 403, json: async () => ({ detail: 'not a member' }) }
      return { ok: true, status: 200, json: async () => body }
    }
    return { ok: false, status: 404, json: async () => ({}) }
  })
}

function renderSelector({ modelPickerOrder, gateways = [GATEWAY], currentModel = 'static-a' } = {}) {
  const setCurrentModel = vi.fn()
  useChat.mockReturnValue({
    models: STATIC_MODELS,
    llmGateways: gateways,
    modelPickerOrder,
    user: USER,
    currentModel,
    setCurrentModel,
    features: {},
    complianceLevelFilter: null,
  })
  render(<ModelSelector />)
  fireEvent.click(screen.getByRole('button', { name: /select chat model/i }))
  return { setCurrentModel }
}

const precedes = (a, b) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)

describe('ModelSelector section order', () => {
  beforeEach(() => {
    localStorage.clear()
    mockPickerFetch()
  })
  afterEach(() => vi.restoreAllMocks())

  it('lists static models first by default', async () => {
    renderSelector({ currentModel: '' })
    const gateway = await screen.findByTestId('gateway-enterprise')
    expect(precedes(screen.getByText('static-a'), gateway)).toBe(true)
    expect(gateway.dataset.placement).toBe('after_models')
  })

  it('lists the LiteLLM gateway first with model_picker_order: gateways_first', async () => {
    renderSelector({ modelPickerOrder: 'gateways_first', currentModel: '' })
    const gateway = await screen.findByTestId('gateway-enterprise')
    expect(precedes(gateway, screen.getByText('static-a'))).toBe(true)
    expect(precedes(gateway, screen.getByText('static-b'))).toBe(true)
    expect(gateway.dataset.placement).toBe('before_models')
  })

  it('keeps several gateways in file order above the static models', async () => {
    global.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ teams: [] }) }))
    renderSelector({
      modelPickerOrder: 'gateways_first',
      currentModel: '',
      gateways: [GATEWAY, { ...GATEWAY, name: 'second', display_name: 'Second' }],
    })
    const first = await screen.findByTestId('gateway-enterprise')
    const second = screen.getByTestId('gateway-second')
    expect(precedes(first, second)).toBe(true)
    expect(precedes(second, screen.getByText('static-a'))).toBe(true)
  })
})

describe('GatewayModelPicker default team', () => {
  beforeEach(() => {
    localStorage.clear()
    mockPickerFetch()
  })
  afterEach(() => vi.restoreAllMocks())

  it('preselects the configured default team and marks the default model', async () => {
    const { setCurrentModel } = renderSelector({ gateways: [GATEWAY_WITH_DEFAULTS] })
    const teamSelect = await screen.findByLabelText('1. Team')
    await waitFor(() => expect(teamSelect.value).toBe('team-alpha'))
    const defaultModel = await screen.findByRole('button', { name: /gpt-4o-mini/ })
    expect(defaultModel).toHaveTextContent('default')
    expect(screen.getAllByTestId('gateway-default-model')).toHaveLength(1)
    fireEvent.click(defaultModel)
    expect(setCurrentModel).toHaveBeenCalledWith(ALPHA_KEY)
  })

  it('preselects the default team with no default model and marks nothing', async () => {
    renderSelector({ gateways: [{ ...GATEWAY, default_team_id: 'team-beta' }] })
    const teamSelect = await screen.findByLabelText('1. Team')
    await waitFor(() => expect(teamSelect.value).toBe('team-beta'))
    await screen.findByRole('button', { name: /llama-3.3-70b/ })
    expect(screen.queryByTestId('gateway-default-model')).toBeNull()
  })

  it('leaves the team unselected when the user is not in the default team', async () => {
    mockPickerFetch({ teams: [{ team_id: 'team-beta', label: 'Project Beta' }] })
    renderSelector({ gateways: [GATEWAY_WITH_DEFAULTS] })
    const teamSelect = await screen.findByLabelText('1. Team')
    expect(teamSelect.value).toBe('')
    expect(screen.queryByRole('button', { name: /gpt-4o-mini/ })).toBeNull()
  })

  it('prefers the user\'s last team over the configured default', async () => {
    rememberLastTeam('enterprise', 'team-beta', USER)
    renderSelector({ gateways: [GATEWAY_WITH_DEFAULTS] })
    const teamSelect = await screen.findByLabelText('1. Team')
    await waitFor(() => expect(teamSelect.value).toBe('team-beta'))
  })

  it('replaces a remembered team the user left with the default team', async () => {
    rememberLastTeam('enterprise', 'team-gone', USER)
    renderSelector({ gateways: [GATEWAY_WITH_DEFAULTS] })
    const teamSelect = await screen.findByLabelText('1. Team')
    await waitFor(() => expect(teamSelect.value).toBe('team-alpha'))
  })

  it('shows the selected model\'s team over the default team', async () => {
    renderSelector({ gateways: [GATEWAY_WITH_DEFAULTS], currentModel: 'enterprise::team-beta::llama-3.3-70b' })
    const teamSelect = await screen.findByLabelText('1. Team')
    await waitFor(() => expect(teamSelect.value).toBe('team-beta'))
  })
})
