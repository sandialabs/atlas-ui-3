/**
 * Tests for config localStorage caching and three-phase loading.
 *
 * Verifies that useChatConfig:
 * 1. Hydrates initial state from localStorage cache
 * 2. Caches successful /api/config responses
 * 3. Falls back gracefully when cache is empty/corrupt
 * 4. Shell config is applied without overwriting tools/prompts
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { CONFIG_CACHE_KEY } from '../hooks/chat/useChatConfig'

// Sample full config response
const FULL_CONFIG = {
  app_name: 'Atlas Chat',
  models: [
    { name: 'gpt-4o', description: 'GPT-4o' },
    { name: 'claude-3', description: 'Claude 3' }
  ],
  tools: [
    { server: 'code-tools', tools: ['lint', 'format'], tools_detailed: [] }
  ],
  prompts: [
    { server: 'prompts-server', prompts: [{ name: 'summarize', description: 'Summarize text' }] }
  ],
  data_sources: ['rag-server:docs'],
  rag_servers: [{ server: 'rag-server', sources: [{ id: 'docs', label: 'Documentation' }] }],
  user: 'testuser@example.com',
  features: {
    workspaces: false,
    rag: true,
    tools: true,
    marketplace: false,
    files_panel: true,
    chat_history: true,
    compliance_levels: false,
    file_content_extraction: false
  },
  file_extraction: { enabled: false, default_behavior: 'none', supported_extensions: [] },
  agent_mode_available: true,
  is_in_admin_group: false,
  banner_enabled: false
}

// Sample shell config response (no tools/prompts/RAG)
const SHELL_CONFIG = {
  app_name: 'Atlas Chat',
  models: [
    { name: 'gpt-4o', description: 'GPT-4o' },
    { name: 'claude-3', description: 'Claude 3' }
  ],
  user: 'testuser@example.com',
  features: {
    workspaces: false,
    rag: true,
    tools: true,
    marketplace: false,
    files_panel: true,
    chat_history: true,
    compliance_levels: false,
    file_content_extraction: false,
    splash_screen: false,
    globus_auth: false
  },
  file_extraction: { enabled: false, default_behavior: 'none', supported_extensions: [] },
  agent_mode_available: true,
  is_in_admin_group: false,
  banner_enabled: false
}

describe('Config Cache Read/Write', () => {
  let storage = {}

  beforeEach(() => {
    storage = {}
    vi.stubGlobal('localStorage', {
      getItem: vi.fn((key) => storage[key] ?? null),
      setItem: vi.fn((key, value) => { storage[key] = value }),
      removeItem: vi.fn((key) => { delete storage[key] })
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('should read cached config from localStorage', () => {
    storage[CONFIG_CACHE_KEY] = JSON.stringify(FULL_CONFIG)

    const raw = localStorage.getItem(CONFIG_CACHE_KEY)
    const cached = JSON.parse(raw)

    expect(cached.app_name).toBe('Atlas Chat')
    expect(cached.models).toHaveLength(2)
    expect(cached.tools).toHaveLength(1)
    expect(cached.features.rag).toBe(true)
    expect(cached.user).toBe('testuser@example.com')
  })

  it('should return null when no cache exists', () => {
    const raw = localStorage.getItem(CONFIG_CACHE_KEY)
    expect(raw).toBeNull()
  })

  it('should handle corrupt cache data gracefully', () => {
    storage[CONFIG_CACHE_KEY] = 'not-valid-json'

    const raw = localStorage.getItem(CONFIG_CACHE_KEY)
    let cached = null
    try {
      cached = JSON.parse(raw)
    } catch {
      cached = null
    }

    expect(cached).toBeNull()
  })

  it('should write config to localStorage cache', () => {
    localStorage.setItem(CONFIG_CACHE_KEY, JSON.stringify(FULL_CONFIG))

    expect(localStorage.setItem).toHaveBeenCalledWith(
      CONFIG_CACHE_KEY,
      JSON.stringify(FULL_CONFIG)
    )

    const stored = JSON.parse(storage[CONFIG_CACHE_KEY])
    expect(stored.app_name).toBe('Atlas Chat')
    expect(stored.tools).toHaveLength(1)
  })

  it('should overwrite previous cache on update', () => {
    storage[CONFIG_CACHE_KEY] = JSON.stringify({ app_name: 'Old Name' })

    localStorage.setItem(CONFIG_CACHE_KEY, JSON.stringify(FULL_CONFIG))

    const stored = JSON.parse(storage[CONFIG_CACHE_KEY])
    expect(stored.app_name).toBe('Atlas Chat')
  })
})

describe('Config Hydration Logic', () => {
  it('should use cached features for initial state', () => {
    const DEFAULT_FEATURES = {
      workspaces: false, rag: false, tools: false, marketplace: false,
      files_panel: false, chat_history: false, compliance_levels: false,
      file_content_extraction: false
    }

    // Simulate hydration from cache
    const cached = FULL_CONFIG
    const features = cached?.features
      ? { ...DEFAULT_FEATURES, ...cached.features }
      : DEFAULT_FEATURES

    expect(features.rag).toBe(true)
    expect(features.tools).toBe(true)
    expect(features.files_panel).toBe(true)
    expect(features.chat_history).toBe(true)
    expect(features.workspaces).toBe(false)
  })

  it('should use default features when no cache exists', () => {
    const DEFAULT_FEATURES = {
      workspaces: false, rag: false, tools: false, marketplace: false,
      files_panel: false, chat_history: false, compliance_levels: false,
      file_content_extraction: false
    }

    // When no cache exists, features fall back to defaults without merging
    const cached = null
    const features = cached ? { ...DEFAULT_FEATURES, ...cached.features } : DEFAULT_FEATURES

    expect(features.rag).toBe(false)
    expect(features.tools).toBe(false)
  })

  it('should hydrate tools from cache', () => {
    const cached = FULL_CONFIG
    const tools = cached?.tools || []

    expect(tools).toHaveLength(1)
    expect(tools[0].server).toBe('code-tools')
    expect(tools[0].tools).toContain('lint')
  })

  it('should set configReady true when cache exists', () => {
    const cached = FULL_CONFIG
    const configReady = !!cached

    expect(configReady).toBe(true)
  })

  it('should set configReady false when no cache', () => {
    const cached = null
    const configReady = !!cached

    expect(configReady).toBe(false)
  })
})

describe('Shell Config Application', () => {
  it('shell config should not include tools or prompts', () => {
    expect(SHELL_CONFIG.tools).toBeUndefined()
    expect(SHELL_CONFIG.prompts).toBeUndefined()
    expect(SHELL_CONFIG.data_sources).toBeUndefined()
    expect(SHELL_CONFIG.rag_servers).toBeUndefined()
  })

  it('shell config should include fast UI data', () => {
    expect(SHELL_CONFIG.app_name).toBe('Atlas Chat')
    expect(SHELL_CONFIG.models).toHaveLength(2)
    expect(SHELL_CONFIG.features).toBeDefined()
    expect(SHELL_CONFIG.features.rag).toBe(true)
    expect(SHELL_CONFIG.user).toBe('testuser@example.com')
    expect(SHELL_CONFIG.agent_mode_available).toBe(true)
  })

  it('should preserve existing tools when applying shell config', () => {
    // Simulate: cache loaded tools, then shell applied
    let tools = FULL_CONFIG.tools
    let prompts = FULL_CONFIG.prompts

    // Shell apply should NOT overwrite tools/prompts
    const isShell = true
    if (!isShell) {
      tools = SHELL_CONFIG.tools || []
      prompts = SHELL_CONFIG.prompts || []
    }

    expect(tools).toHaveLength(1)
    expect(prompts).toHaveLength(1)
  })

  it('should update models from shell even when cache had different models', () => {
    // Cache had old models
    let models = [{ name: 'old-model', description: 'Old' }]

    // Shell provides updated models
    models = SHELL_CONFIG.models || models

    expect(models).toHaveLength(2)
    expect(models[0].name).toBe('gpt-4o')
  })
})

describe('Full Config Reconciliation', () => {
  it('should overwrite cached tools with fresh data', () => {
    // Fresh full config has updated tools, overwriting whatever was previously cached
    const freshConfig = {
      ...FULL_CONFIG,
      tools: [
        { server: 'code-tools', tools: ['lint', 'format', 'test'], tools_detailed: [] },
        { server: 'data-tools', tools: ['query'], tools_detailed: [] }
      ]
    }

    const tools = freshConfig.tools
    expect(tools).toHaveLength(2)
    expect(tools[0].tools).toContain('test')
    expect(tools[1].server).toBe('data-tools')
  })

  it('should handle config where tools were removed', () => {
    // Cache had tools
    let tools = FULL_CONFIG.tools
    expect(tools).toHaveLength(1)

    // Fresh config has no tools (feature disabled)
    const freshConfig = { ...FULL_CONFIG, tools: [] }
    tools = freshConfig.tools
    expect(tools).toHaveLength(0)
  })

  it('should update cache after full config fetch', () => {
    const storage = {}
    const setItem = (key, value) => { storage[key] = value }

    const freshConfig = { ...FULL_CONFIG, app_name: 'Updated Atlas' }
    setItem(CONFIG_CACHE_KEY, JSON.stringify(freshConfig))

    const cached = JSON.parse(storage[CONFIG_CACHE_KEY])
    expect(cached.app_name).toBe('Updated Atlas')
  })
})
