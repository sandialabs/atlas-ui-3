import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { parseGatewayModelKey, withGatewayModel } from '../../utils/gatewayModels'
import { isKnownUser, userStorageKey } from '../../utils/userStorage'

const DEFAULT_FEATURES = {
  workspaces: false,
  rag: false,
  tools: false,
  marketplace: false,
  files_panel: false,
  chat_history: false,
  custom_prompts: false,
  compliance_levels: false,
  file_content_extraction: false,
  finetune_capture: false
}

const DEFAULT_FILE_EXTRACTION = {
  enabled: false,
  default_behavior: 'none',
  supported_extensions: []
}

const DEFAULT_FILE_UPLOAD = {
  max_file_size_mb: 250,
  max_file_size_bytes: 250 * 1024 * 1024
}

export const CONFIG_CACHE_KEY = 'chatui-config-cache'

// The selected model is remembered per user (issue #1050): on a shared
// browser the next account must not start on the previous one's model.
const CURRENT_MODEL_KEY = 'chatui-current-model'

function readSavedModel(user) {
  if (!isKnownUser(user)) return ''
  try {
    return localStorage.getItem(userStorageKey(CURRENT_MODEL_KEY, user)) || ''
  } catch {
    return ''
  }
}

function writeSavedModel(user, model) {
  if (!isKnownUser(user)) return
  try {
    localStorage.setItem(userStorageKey(CURRENT_MODEL_KEY, user), model)
  } catch (e) {
    console.warn('Failed to save current model to localStorage:', e)
  }
}

function defaultModelOf(cfg) {
  const first = cfg.models?.[0]
  return first ? (first.name || first) : ''
}

/**
 * The model to start `cfg.user` on: their saved model while the config still
 * offers it (a configured model, or a key on a configured gateway), else the
 * default. Team membership of a gateway model is checked separately
 * (gatewayTeamStillMember), since it needs a request.
 */
export function resolveStartingModel(saved, cfg) {
  if (saved) {
    const modelNames = (cfg.models || []).map(m => m.name || m)
    if (modelNames.includes(saved) || parseGatewayModelKey(saved, cfg.llm_gateways || [])) {
      return saved
    }
  }
  return defaultModelOf(cfg)
}

/**
 * Whether the signed-in user is still in the team a gateway model key names.
 * Resolves true when it cannot tell (a failed request): the server checks
 * membership on every turn anyway, so a transient error must not discard
 * the user's choice.
 */
export async function gatewayTeamStillMember(parsedKey) {
  try {
    const res = await fetch(`/api/llm/gateways/${encodeURIComponent(parsedKey.gateway)}/teams`)
    if (!res.ok) return true
    const body = await res.json()
    const teams = Array.isArray(body?.teams) ? body.teams : []
    return teams.some(t => t?.team_id === parsedKey.teamId)
  } catch {
    return true
  }
}

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
  'app_name', 'models', 'features', 'file_extraction', 'file_upload',
  'banner_enabled', 'agent_mode_available', 'agent_max_steps'
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
  // Enterprise LiteLLM gateways whose models are chosen by team (see
  // utils/gatewayModels.js); their models are not part of `models`.
  const [llmGateways, setLlmGateways] = useState(cached.current?.llm_gateways || [])
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
  const [fileUpload, setFileUpload] = useState(
    cached.current?.file_upload
      ? { ...DEFAULT_FILE_UPLOAD, ...cached.current.file_upload }
      : DEFAULT_FILE_UPLOAD
  )
  const [isCanvasOpen, setIsCanvasOpen] = useState(false)
  // Empty until /api/config says who is signed in; then their saved model.
  const [currentModel, setCurrentModel] = useState('')
  const currentModelRef = useRef('')
  currentModelRef.current = currentModel
  const userRef = useRef('Unknown')
  const [agentModeAvailable, setAgentModeAvailable] = useState(
    cached.current ? !!cached.current.agent_mode_available : false
  )
  // Admin-configured ceiling for the agent loop (issue #849): bounds the Max
  // Agent Iterations slider so its upper end matches what the server will
  // actually honor. Falls back to the server's default AGENT_MAX_STEPS until
  // config lands, so a pre-config choice is already within the real ceiling.
  const [agentMaxStepsLimit, setAgentMaxStepsLimit] = useState(() => {
    const v = Number(cached.current?.agent_max_steps)
    return Number.isFinite(v) && v > 0 ? v : 10
  })
  // Whether a live config response (not the cache) has supplied a valid
  // agent_max_steps ceiling. A cached value alone must not count: an old
  // cache predates the field entirely, and a stale cache can disagree with a
  // changed AGENT_MAX_STEPS, so consumers must not persist anything derived
  // from the ceiling until a current response confirms it (#849 review).
  const [agentCeilingConfirmed, setAgentCeilingConfirmed] = useState(false)
  const [isInAdminGroup, setIsInAdminGroup] = useState(false)
  // Tracks whether we have received at least one config response (cache or network)
  const [configReady, setConfigReady] = useState(!!cached.current)
  const configReadyRef = useRef(!!cached.current)
  // Tracks whether the full /api/config response has been applied.
  // Shell responses must not overwrite state once full config has been applied,
  // since shell data is less complete (no tools, no per-user model fields, etc.).
  const fullConfigAppliedRef = useRef(false)

  // Apply a config response object to state.
  // When isShell=true, tools/prompts/RAG fields are not present and are left unchanged.
  const applyConfig = useCallback((cfg, isShell = false) => {
    setAppName(cfg.app_name || 'Chat UI')
    setModels(cfg.models || [])
    setLlmGateways(cfg.llm_gateways || [])
    setUser(cfg.user || 'Unknown')
    userRef.current = cfg.user || 'Unknown'
    setFeatures(prev => ({ ...DEFAULT_FEATURES, ...(cfg.features || prev) }))
    setFileExtraction(prev => ({ ...DEFAULT_FILE_EXTRACTION, ...(cfg.file_extraction || prev) }))
    setFileUpload(prev => ({ ...DEFAULT_FILE_UPLOAD, ...(cfg.file_upload || prev) }))
    setAgentModeAvailable(!!cfg.agent_mode_available)
    // Only a response carrying a valid ceiling updates it: a mixed-version
    // pod that omits (or garbles) the field must not reset a confirmed
    // ceiling to the fallback of 10, nor un-confirm what a previous live
    // response established (#849 review). Before any confirmation the
    // initial fallback of 10 stands for display only.
    const maxSteps = Number(cfg.agent_max_steps)
    if (Number.isFinite(maxSteps) && maxSteps > 0) {
      setAgentMaxStepsLimit(maxSteps)
      setAgentCeilingConfirmed(true)
    }
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
      // Skip shell application if the full config has already been applied, to avoid
      // overwriting more-complete data with partial shell data in a race.
      if (!fullConfigAppliedRef.current) {
        applyConfig(cfg, true)
      }
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
      fullConfigAppliedRef.current = true
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
        setLlmGateways([])
        setTools([])
        setDataSources([])
        setUser('Unauthenticated')
        userRef.current = 'Unauthenticated'
        setFeatures(DEFAULT_FEATURES)
        setFileUpload(DEFAULT_FILE_UPLOAD)
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

  // Load config on mount (shell first, then the full response). The state it
  // applies -- the signed-in user and the model list -- drives the per-user
  // model restoration below.
  useEffect(() => {
    fetchConfig()
  }, [fetchConfig])

  // Once the config says who is signed in, start them on their own saved
  // model (or the default), never on a model another account picked on this
  // browser. Driven by identity rather than one fetch: a shell response is
  // enough (so a slow or failed full /api/config does not leave the model
  // unset), and a refresh that changes the signed-in user restores that
  // user's model too.
  const restoredForUserRef = useRef(null)
  useEffect(() => {
    if (!isKnownUser(user) || !models.length) return
    if (restoredForUserRef.current === user) return
    restoredForUserRef.current = user

    const saved = readSavedModel(user)
    const model = resolveStartingModel(saved, { models, llm_gateways: llmGateways })
    setCurrentModel(model)
    currentModelRef.current = model
    if (model) writeSavedModel(user, model)
  }, [user, models, llmGateways])

  // A saved team-gateway model stays only while the user is still in that
  // team; otherwise fall back to the default model. Keyed on the selected
  // model rather than the config fetch, so a config refresh (a new models or
  // gateways array for the same user) cannot cancel a membership check that
  // is still in flight and leave a stale team model selected.
  const membershipCheckRef = useRef(0)
  useEffect(() => {
    if (!isKnownUser(user)) return
    const parsed = parseGatewayModelKey(currentModel, llmGateways)
    if (!parsed) return
    const token = ++membershipCheckRef.current
    ;(async () => {
      const member = await gatewayTeamStillMember(parsed)
      if (token !== membershipCheckRef.current || member) return
      if (restoredForUserRef.current !== user || currentModelRef.current !== currentModel) return
      const fallback = defaultModelOf({ models })
      setCurrentModel(fallback)
      currentModelRef.current = fallback
      writeSavedModel(user, fallback)
    })()
  }, [user, currentModel, models, llmGateways])

  // The current gateway model joins the list so every "look up the current
  // model's capabilities" call site finds it like any configured model.
  const modelsWithGateway = useMemo(
    () => withGatewayModel(models, currentModel, llmGateways, user),
    [models, currentModel, llmGateways, user]
  )

  return {
    appName,
    user,
    models: modelsWithGateway,
    llmGateways,
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
      currentModelRef.current = model
      writeSavedModel(userRef.current, model)
    },
    agentModeAvailable,
    agentMaxStepsLimit,
    agentCeilingConfirmed,
    isInAdminGroup,
    fileExtraction,
    fileUpload,
    configReady,
    refreshConfig: fetchConfig, // Allow manual refresh of config (e.g., after MCP reload)
  }
}
