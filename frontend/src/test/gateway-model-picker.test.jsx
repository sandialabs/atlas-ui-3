/**
 * Enterprise LiteLLM gateways: the user picks a team, then one of that team's
 * models, and the selection is a gateway model key that carries the team.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import ModelSelector from '../components/ModelSelector'
import { useChat } from '../contexts/ChatContext'
import {
  parseGatewayModelKey,
  gatewayModelEntry,
  withGatewayModel,
  rememberTeamLabel,
} from '../utils/gatewayModels'
import { isComplianceAccessible } from '../utils/complianceAccess'

const mocks = vi.hoisted(() => ({ marketplace: null }))

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext', () => ({ useOptionalMarketplace: () => mocks.marketplace }))
vi.mock('../hooks/useLLMAuthStatus', () => ({
  useLLMAuthStatus: () => ({
    fetchAuthStatus: vi.fn(),
    getModelAuth: () => null,
    uploadToken: vi.fn(),
    loading: false,
    error: null,
  }),
}))

const GATEWAY = {
  name: 'enterprise',
  display_name: 'Enterprise LiteLLM',
  supports_tools: true,
  supports_vision: false,
}

const TEAMS = { teams: [
  { team_id: 'team-alpha', label: 'Project Alpha' },
  { team_id: 'team-beta', label: 'Project Beta' },
] }

const MODELS = {
  'team-alpha': { models: [
    { name: 'enterprise::team-alpha::gpt-4o-mini', model_id: 'gpt-4o-mini', label: 'gpt-4o-mini',
      supports_vision: false, supports_pdf: false, supports_tools: false },
    { name: 'enterprise::team-alpha::claude-sonnet', model_id: 'claude-sonnet', label: 'claude-sonnet',
      supports_vision: true, supports_pdf: true, supports_tools: true },
  ] },
  'team-beta': { models: [
    { name: 'enterprise::team-beta::llama-3.3-70b', model_id: 'llama-3.3-70b', label: 'llama-3.3-70b' },
  ] },
}

function mockFetch() {
  global.fetch = vi.fn(async (url) => {
    const parsed = new URL(url, 'http://localhost')
    if (parsed.pathname === '/api/llm/gateways/enterprise/teams') {
      return { ok: true, status: 200, json: async () => TEAMS }
    }
    if (parsed.pathname === '/api/llm/gateways/enterprise/models') {
      const body = MODELS[parsed.searchParams.get('team_id')]
      if (!body) return { ok: false, status: 403, json: async () => ({ detail: 'not a member' }) }
      return { ok: true, status: 200, json: async () => body }
    }
    return { ok: false, status: 404, json: async () => ({}) }
  })
}

function setup({ currentModel = 'static-model', models, gateways = [GATEWAY], features = {}, complianceLevelFilter = null } = {}) {
  const setCurrentModel = vi.fn()
  useChat.mockReturnValue({
    models: withGatewayModel(models || [{ name: 'static-model', supports_tools: true }], currentModel, gateways, 'test@test.com'),
    llmGateways: gateways,
    user: 'test@test.com',
    currentModel,
    setCurrentModel,
    features,
    complianceLevelFilter,
  })
  render(<ModelSelector />)
  return { setCurrentModel }
}

describe('gateway model keys', () => {
  beforeEach(() => localStorage.clear())

  it('parses keys whose model id contains separators', () => {
    expect(parseGatewayModelKey('enterprise::t1::bedrock/claude:v2', [GATEWAY])).toEqual({
      gateway: 'enterprise', teamId: 't1', modelId: 'bedrock/claude:v2',
    })
  })

  it('ignores ordinary names and unknown gateways', () => {
    expect(parseGatewayModelKey('gpt-4o', [GATEWAY])).toBeNull()
    expect(parseGatewayModelKey('other::t1::m', [GATEWAY])).toBeNull()
  })

  it('builds a model entry labelled with the remembered team name', () => {
    rememberTeamLabel('enterprise', 't1', 'Project One', 'a@x.com')
    const entry = gatewayModelEntry('enterprise::t1::gpt-4o-mini', [GATEWAY], 'a@x.com')
    expect(entry.display_name).toBe('gpt-4o-mini (Project One)')
    // Another user on the same browser does not see it.
    expect(gatewayModelEntry('enterprise::t1::gpt-4o-mini', [GATEWAY], 'b@x.com').display_name)
      .toBe('gpt-4o-mini (t1)')
    expect(entry.supports_tools).toBe(true)
    expect(entry.gateway).toBe('enterprise')
  })

  it('gives a saved selection its own model\'s capabilities, else the gateway\'s', () => {
    const gateway = {
      ...GATEWAY,
      model_capabilities: {
        'claude-sonnet': { supports_vision: true, supports_pdf: true, supports_tools: true },
        'llama-3.3-70b': { supports_vision: false, supports_pdf: false, supports_tools: false },
      },
    }
    const sonnet = gatewayModelEntry('enterprise::t1::claude-sonnet', [gateway], 'a@x.com')
    expect([sonnet.supports_vision, sonnet.supports_pdf, sonnet.supports_tools]).toEqual([true, true, true])
    const llama = gatewayModelEntry('enterprise::t1::llama-3.3-70b', [gateway], 'a@x.com')
    expect([llama.supports_vision, llama.supports_tools]).toEqual([false, false])
    // A model the map doesn't name: the gateway's defaults.
    const other = gatewayModelEntry('enterprise::t1::other', [gateway], 'a@x.com')
    expect([other.supports_vision, other.supports_tools]).toEqual([false, true])
  })
})

describe('ModelSelector with an enterprise LiteLLM gateway', () => {
  beforeEach(() => {
    localStorage.clear()
    mockFetch()
  })
  afterEach(() => vi.restoreAllMocks())

  it('selects a model only after a team is chosen, and the key carries the team', async () => {
    const { setCurrentModel } = setup()
    fireEvent.click(screen.getByRole('button', { name: /select chat model/i }))

    const teamSelect = await screen.findByLabelText('1. Team')
    expect(screen.queryByText('gpt-4o-mini')).toBeNull()

    fireEvent.change(teamSelect, { target: { value: 'team-beta' } })
    fireEvent.click(await screen.findByRole('button', { name: /llama-3.3-70b/ }))

    expect(setCurrentModel).toHaveBeenCalledWith('enterprise::team-beta::llama-3.3-70b')
    expect(global.fetch).toHaveBeenCalledWith('/api/llm/gateways/enterprise/models?team_id=team-beta')
  })

  it('shows each listed model\'s own vision and tool support', async () => {
    setup()
    fireEvent.click(screen.getByRole('button', { name: /select chat model/i }))
    fireEvent.change(await screen.findByLabelText('1. Team'), { target: { value: 'team-alpha' } })
    const iconClasses = async (label) => {
      const row = await screen.findByRole('button', { name: new RegExp(label) })
      return [row.querySelector('.lucide-eye'), row.querySelector('.lucide-wrench')]
        .map(icon => icon.getAttribute('class'))
    }
    const [sonnetEye, sonnetWrench] = await iconClasses('claude-sonnet')
    expect(sonnetEye).toContain('text-green-400')
    expect(sonnetWrench).toContain('text-blue-400')
    const [miniEye, miniWrench] = await iconClasses('gpt-4o-mini')
    expect(miniEye).toContain('text-gray-600')
    expect(miniWrench).toContain('text-gray-600')
  })

  it('keeps gateway models out of the flat list and labels the current one', async () => {
    rememberTeamLabel('enterprise', 'team-alpha', 'Project Alpha', 'test@test.com')
    setup({ currentModel: 'enterprise::team-alpha::claude-sonnet' })
    const trigger = screen.getByRole('button', { name: /select chat model/i })
    expect(trigger.textContent).toContain('claude-sonnet (Project Alpha)')

    fireEvent.click(trigger)
    // The current team is preselected, so its models load straight away.
    await waitFor(() => expect(screen.getByLabelText('1. Team').value).toBe('team-alpha'))
    const current = await screen.findByRole('button', { name: /claude-sonnet/, pressed: true })
    expect(current).toBeTruthy()
    expect(screen.getAllByText('static-model')).toHaveLength(1)
  })

  it('flags a saved selection whose team is gone and can refresh', async () => {
    setup({ currentModel: 'enterprise::team-removed::gpt-4o-mini' })
    fireEvent.click(screen.getByRole('button', { name: /select chat model/i }))
    expect(await screen.findByRole('status')).toHaveTextContent('selected team is no longer available')
    fireEvent.click(screen.getByRole('button', { name: /refresh teams and models/i }))
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith('/api/llm/gateways/enterprise/teams?refresh=true')
    )
  })

  it('shows the gateway error when teams cannot be listed', async () => {
    global.fetch = vi.fn(async () => ({
      ok: false, status: 401, json: async () => ({ detail: 'Please sign in again.' }),
    }))
    setup()
    fireEvent.click(screen.getByRole('button', { name: /select chat model/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Please sign in again.')
    expect(screen.queryByTestId('gateway-signin-link')).toBeNull()
  })

  it('offers a sign-in link when the backend says a fresh sign-in can fix it', async () => {
    global.fetch = vi.fn(async () => ({
      ok: false,
      status: 401,
      headers: new Headers({ 'X-Atlas-Sign-In': '/auth/oidc/login' }),
      json: async () => ({ detail: 'Your sign-in session has no token. Please sign in again.' }),
    }))
    setup()
    fireEvent.click(screen.getByRole('button', { name: /select chat model/i }))
    const link = await screen.findByTestId('gateway-signin-link')
    const next = window.location.pathname + window.location.search
    expect(link.getAttribute('href')).toBe(`/auth/oidc/login?next=${encodeURIComponent(next)}`)
  })

  it('offers the sign-in link when listing a team\'s models fails the same way', async () => {
    global.fetch = vi.fn(async (url) => {
      if (url.startsWith('/api/llm/gateways/enterprise/teams')) {
        return { ok: true, status: 200, json: async () => TEAMS }
      }
      return {
        ok: false,
        status: 401,
        headers: new Headers({ 'X-Atlas-Sign-In': '/auth/oidc/login' }),
        json: async () => ({ detail: 'Please sign in again.' }),
      }
    })
    setup()
    fireEvent.click(screen.getByRole('button', { name: /select chat model/i }))
    fireEvent.change(await screen.findByLabelText('1. Team'), { target: { value: 'team-alpha' } })
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load models: Please sign in again.')
    expect(screen.getByTestId('gateway-signin-link').getAttribute('href')).toMatch(/^\/auth\/oidc\/login\?next=/)
  })

  it.each([
    '//evil.example/login',
    '/\\evil.example/login',
    'https://evil.example/login',
    'javascript:alert(1)',
  ])('ignores a sign-in header that is not a same-origin path: %s', async (value) => {
    global.fetch = vi.fn(async () => ({
      ok: false,
      status: 401,
      headers: new Headers({ 'X-Atlas-Sign-In': value }),
      json: async () => ({ detail: 'Please sign in again.' }),
    }))
    setup()
    fireEvent.click(screen.getByRole('button', { name: /select chat model/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Please sign in again.')
    expect(screen.queryByTestId('gateway-signin-link')).toBeNull()
  })
})

describe('admin-allowlisted gateway models with their own classifications', () => {
  // A gateway whose llmconfig `models` allowlist gives gpt-4o-mini its own
  // level and llama explicit classifications; claude-sonnet inherits the
  // gateway's level.
  const LEVELED_GATEWAY = {
    ...GATEWAY,
    compliance_level: 'Internal',
    model_compliance_levels: { 'gpt-4o-mini': 'Public', 'claude-sonnet': 'Internal', llama: null },
    compliance_levels: ['Internal', 'Public', null],
    model_allowed_data_classifications: {
      'gpt-4o-mini': ['Public'],
      'claude-sonnet': ['Internal'],
      llama: ['Public', 'Internal'],
    },
    model_classifications: [['Public'], ['Internal'], ['Public', 'Internal']],
  }
  const LEVELED_MODELS = { models: [
    { name: 'enterprise::team-alpha::gpt-4o-mini', model_id: 'gpt-4o-mini', label: 'gpt-4o-mini', compliance_level: 'Public', allowed_data_classifications: ['Public'] },
    { name: 'enterprise::team-alpha::claude-sonnet', model_id: 'claude-sonnet', label: 'claude-sonnet', compliance_level: 'Internal', allowed_data_classifications: ['Internal'] },
    { name: 'enterprise::team-alpha::llama', model_id: 'llama', label: 'llama', allowed_data_classifications: ['Public', 'Internal'] },
  ] }
  // Internal's allowed_with lists Public, which no longer widens access.
  const LEVELS = [
    { name: 'Public', aliases: [], allowed_with: ['Public'] },
    { name: 'Internal', aliases: [], allowed_with: ['Internal', 'Public'] },
  ]

  beforeEach(() => {
    localStorage.clear()
    global.fetch = vi.fn(async (url) => {
      const parsed = new URL(url, 'http://localhost')
      if (parsed.pathname.endsWith('/teams')) return { ok: true, status: 200, json: async () => TEAMS }
      return { ok: true, status: 200, json: async () => LEVELED_MODELS }
    })
    // The real shared rule, as MarketplaceContext binds it.
    mocks.marketplace = {
      isComplianceAccessible: (filter, classifications) =>
        isComplianceAccessible(LEVELS, filter, classifications),
    }
  })
  afterEach(() => {
    mocks.marketplace = null
    vi.restoreAllMocks()
  })

  it('labels a saved selection with the model level, not the gateway level', () => {
    expect(gatewayModelEntry('enterprise::t1::gpt-4o-mini', [LEVELED_GATEWAY], 'a@x.com').compliance_level)
      .toBe('Public')
    expect(gatewayModelEntry('enterprise::t1::other', [LEVELED_GATEWAY], 'a@x.com').compliance_level)
      .toBe('Internal')
    // Unleveled on the server (unknown level at load): no gateway fallback.
    const unleveled = { ...LEVELED_GATEWAY, model_compliance_levels: { 'gpt-4o-mini': null } }
    expect(gatewayModelEntry('enterprise::t1::gpt-4o-mini', [unleveled], 'a@x.com').compliance_level)
      .toBeNull()
  })

  it('carries the per-model classifications onto a saved selection', () => {
    expect(gatewayModelEntry('enterprise::t1::llama', [LEVELED_GATEWAY], 'a@x.com').allowed_data_classifications)
      .toEqual(['Public', 'Internal'])
    expect(gatewayModelEntry('enterprise::t1::gpt-4o-mini', [LEVELED_GATEWAY], 'a@x.com').allowed_data_classifications)
      .toEqual(['Public'])
  })

  it('keeps the gateway listed when one model passes and hides the others', async () => {
    lastTeam('team-alpha')
    setup({
      gateways: [LEVELED_GATEWAY],
      features: { compliance_levels: true },
      complianceLevelFilter: 'Public',
    })
    fireEvent.click(screen.getByRole('button', { name: /select chat model/i }))
    expect(await screen.findByRole('button', { name: /gpt-4o-mini/ })).toHaveTextContent('Public')
    expect(screen.getByRole('button', { name: /llama/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /claude-sonnet/ })).toBeNull()
  })

  it('does not widen Internal to Public-only models through allowed_with', async () => {
    lastTeam('team-alpha')
    setup({
      gateways: [LEVELED_GATEWAY],
      features: { compliance_levels: true },
      complianceLevelFilter: 'Internal',
    })
    fireEvent.click(screen.getByRole('button', { name: /select chat model/i }))
    expect(await screen.findByRole('button', { name: /claude-sonnet/ })).toBeInTheDocument()
    // Classified for both Public and Internal: listed.
    expect(screen.getByRole('button', { name: /llama/ })).toBeInTheDocument()
    // Public-only: hidden even though Internal's allowed_with lists Public.
    expect(screen.queryByRole('button', { name: /gpt-4o-mini/ })).toBeNull()
  })

  it('hides the gateway when none of its models pass the filter', () => {
    setup({
      gateways: [LEVELED_GATEWAY],
      features: { compliance_levels: true },
      complianceLevelFilter: 'Secret',
    })
    fireEvent.click(screen.getByRole('button', { name: /select chat model/i }))
    expect(screen.queryByTestId('gateway-enterprise')).toBeNull()
  })
})

function lastTeam(teamId) {
  localStorage.setItem('chatui-gateway-last-team:test@test.com', JSON.stringify({ enterprise: teamId }))
}
