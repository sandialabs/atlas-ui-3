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
    { name: 'enterprise::team-alpha::gpt-4o-mini', model_id: 'gpt-4o-mini', label: 'gpt-4o-mini' },
    { name: 'enterprise::team-alpha::claude-sonnet', model_id: 'claude-sonnet', label: 'claude-sonnet' },
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

function setup({ currentModel = 'static-model', models } = {}) {
  const setCurrentModel = vi.fn()
  const gateways = [GATEWAY]
  useChat.mockReturnValue({
    models: withGatewayModel(models || [{ name: 'static-model', supports_tools: true }], currentModel, gateways, 'test@test.com'),
    llmGateways: gateways,
    user: 'test@test.com',
    currentModel,
    setCurrentModel,
    features: {},
    complianceLevelFilter: null,
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
  })
})
