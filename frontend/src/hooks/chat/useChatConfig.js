import { useEffect, useState, useCallback, useRef } from 'react'

const DEFAULT_FEATURES = {
  workspaces: false,
  rag: false,
  tools: false,
  marketplace: false,
  files_panel: false,
  chat_history: false,
  compliance_levels: false,
  file_content_extraction: false
}

const DEFAULT_FILE_EXTRACTION = {
  enabled: false,
  default_behavior: 'none',
  supported_extensions: []
}

const CONFIG_CACHE_KEY = 'chatui-config-cache'

/**
 * Try to read cached config from localStorage.
 * Returns null if no cache or parse fails.
 */
function readCachedConfig() {
  try {
    const raw = localStorage.getItem(CONFIG_CACHE_KEY)
    if (!raw) return null
    return JSON.parse(raw)
  } catch {
    return null
  }
}

/**
 * Fields safe to cache globally (not user/authorization-specific).
 * User-specific data (tools, prompts, rag_servers, etc.) is excluded
 * to prevent cross-user leakage in shared browser sessions.
 */
const SAFE_CACHE_FIELDS = [
  'app_name', 'models', 'features', 'file_extraction',
  'banner_enabled', 'agent_mode_available'
]

/**
 * Per-user fields that must be stripped from model objects before caching.
 * These are derived from the current user's token storage and would leak
 * credential state across sessions (e.g. whether a user has configured
 * personal LLM API keys or Globus tokens).
 */
const PER_USER_MODEL_FIELDS = ['user_has_key', 'api_key_source', 'globus_scope']

/**
 * Write only non-sensitive config fields to localStorage cache.
 * Model objects are sanitized to remove per-user credential state.
 */
function writeCachedConfig(cfg) {
  try {
    const safe = {}
    for (const key of SAFE_CACHE_FIELDS) {
      if (key in cfg) safe[key] = cfg[key]
    }
    // Strip per-user fields from cached model objects
    if (safe.models) {
      safe.models = safe.models.map(m => {
        const cleaned = { ...m }
        for (const field of PER_USER_MODEL_FIELDS) delete cleaned[field]
        return cleaned
      })
    }
    localStorage.setItem(CONFIG_CACHE_KEY, JSON.stringify(safe))
  } catch (e) {
    console.warn('Failed to cache config to localStorage:', e)
  }
}

export function useChatConfig() {
  // Try to hydrate initial state from localStorage cache
  const cached = useRef(readCachedConfig())

  // Cache only contains non-sensitive fields (app_name, models, features, etc.)
  // User-specific fields (user, tools, prompts, rag_servers, etc.) are never cached.
  const [appName, setAppName] = useState(cached.current?.app_name || 'Chat UI')
  const [user, setUser] = useState('Unknown')
  const [models, setModels] = useState(cached.current?.models || [])
  const [tools, setTools] = useState([])
  const [prompts, setPrompts] = useState([])
  const [dataSources, setDataSources] = useState([])
  const [ragServers, setRagServers] = useState([])
  const [features, setFeatures] = useState(
    cached.current?.features
      ? { ...DEFAULT_FEATURES, ...cached.current.features }
      : DEFAULT_FEATURES
  )
  const [fileExtraction, setFileExtraction] = useState(
    cached.current?.file_extraction
      ? { ...DEFAULT_FILE_EXTRACTION, ...cached.current.file_extraction }
      : DEFAULT_FILE_EXTRACTION
  )
  const [isCanvasOpen, setIsCanvasOpen] = useState(false)
  const [currentModel, setCurrentModel] = useState(() => {
    try {
      return localStorage.getItem('chatui-current-model') || ''
    } catch {
      return ''
    }
  })
  const [agentModeAvailable, setAgentModeAvailable] = useState(
    cached.current ? !!cached.current.agent_mode_available : false
  )
  const [isInAdminGroup, setIsInAdminGroup] = useState(false)
  // Tracks whether we have received at least one config response (cache or network)
  const [configReady, setConfigReady] = useState(!!cached.current)
  const configReadyRef = useRef(!!cached.current)

  // Apply a config response object to state.
  // When isShell=true, tools/prompts/RAG fields are not present and are left unchanged.
  const applyConfig = useCallback((cfg, isShell = false) => {
    setAppName(cfg.app_name || 'Chat UI')
    setModels(cfg.models || [])
    setUser(cfg.user || 'Unknown')
    setFeatures(prev => ({ ...DEFAULT_FEATURES, ...(cfg.features || prev) }))
    setFileExtraction(prev => ({ ...DEFAULT_FILE_EXTRACTION, ...(cfg.file_extraction || prev) }))
    setAgentModeAvailable(!!cfg.agent_mode_available)
    setIsInAdminGroup(!!cfg.is_in_admin_group)

    if (!isShell) {
      const uniqueTools = (cfg.tools || []).map(server => ({
        ...server,
        tools: Array.from(new Set(server.tools))
      }))
      setTools(uniqueTools)
      setPrompts(cfg.prompts || [])
      setDataSources(cfg.data_sources || [])
      setRagServers(cfg.rag_servers || [])
    }

    setConfigReady(true)
    configReadyRef.current = true
  }, [])

  // Phase 1: Fetch /api/config/shell (fast - no MCP/RAG discovery)
  const fetchShellConfig = useCallback(async () => {
    try {
      const res = await fetch('/api/config/shell')
      if (!res.ok) return null
      const cfg = await res.json()
      applyConfig(cfg, true)
      return cfg
    } catch (err) {
      console.warn('Failed to fetch /api/config/shell:', err)
      return null
    }
  }, [applyConfig])

  // Phase 2: Fetch full /api/config (includes tools, prompts, RAG - slower)
  const fetchFullConfig = useCallback(async () => {
    try {
      const res = await fetch('/api/config')
      if (!res.ok) throw new Error(res.status)
      const cfg = await res.json()
      applyConfig(cfg, false)
      // Cache the full response for next page load
      writeCachedConfig(cfg)
      return cfg
    } catch (err) {
      console.error('Failed to fetch /api/config:', err)
      // Only reset to unauthenticated on the initial startup fetch when no prior
      // config source (cache or shell) has loaded. Once configReady is true, a
      // failed refresh (e.g. after admin MCP reload) intentionally preserves the
      // existing UI state rather than wiping to defaults — stale-but-functional
      // is better UX than flashing to "Unauthenticated" on transient errors.
      if (!configReadyRef.current) {
        setAppName('Chat UI (Unauthenticated)')
        setModels([])
        setTools([])
        setDataSources([])
        setUser('Unauthenticated')
        setFeatures(DEFAULT_FEATURES)
        setAgentModeAvailable(false)
      }
      return null
    }
  }, [applyConfig])

  // Combined fetch: shell first (fast), then full config (slow)
  const fetchConfig = useCallback(async () => {
    // Start shell fetch immediately for fast UI hydration
    const shellPromise = fetchShellConfig()
    // Start full config fetch in parallel
    const fullPromise = fetchFullConfig()

    // Wait for shell first to update UI quickly
    await shellPromise
    // Then wait for full config
    const cfg = await fullPromise
    return cfg
  }, [fetchShellConfig, fetchFullConfig])

  useEffect(() => {
    (async () => {
      const cfg = await fetchConfig()
      if (cfg) {
        // Set default model if none saved and models available
        if (!currentModel && cfg.models?.length) {
          const defaultModel = cfg.models[0].name || cfg.models[0]
          setCurrentModel(defaultModel)
          localStorage.setItem('chatui-current-model', defaultModel)
        }
        // Validate saved model is still available
        else if (currentModel && cfg.models?.length) {
          const modelNames = cfg.models.map(m => m.name || m)
          if (!modelNames.includes(currentModel)) {
            const defaultModel = cfg.models[0].name || cfg.models[0]
            setCurrentModel(defaultModel)
            localStorage.setItem('chatui-current-model', defaultModel)
          }
        }
      }
    })()
  }, [currentModel, fetchConfig])

  return {
    appName,
    user,
    models,
    tools,
    prompts,
    dataSources,
    ragServers,
    features,
    setFeatures,
    isCanvasOpen,
    setIsCanvasOpen,
    currentModel,
    setCurrentModel: (model) => {
      setCurrentModel(model)
      try {
        localStorage.setItem('chatui-current-model', model)
      } catch (e) {
        console.warn('Failed to save current model to localStorage:', e)
      }
    },
    agentModeAvailable,
    isInAdminGroup,
    fileExtraction,
    configReady,
    refreshConfig: fetchConfig, // Allow manual refresh of config (e.g., after MCP reload)
  }
}
