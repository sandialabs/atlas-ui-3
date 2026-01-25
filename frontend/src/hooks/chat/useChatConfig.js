import { useEffect, useState, useCallback } from 'react'

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
  default_behavior: 'attach_only',
  supported_extensions: []
}

export function useChatConfig() {
  const [appName, setAppName] = useState('Chat UI')
  const [user, setUser] = useState('Unknown')
  const [models, setModels] = useState([])
  const [tools, setTools] = useState([])
  const [prompts, setPrompts] = useState([])
  const [dataSources, setDataSources] = useState([])
  const [ragServers, setRagServers] = useState([]) // New state for rich RAG server data
  const [features, setFeatures] = useState(DEFAULT_FEATURES)
  const [fileExtraction, setFileExtraction] = useState(DEFAULT_FILE_EXTRACTION)
  const [isCanvasOpen, setIsCanvasOpen] = useState(false)
  // Load saved model from localStorage
  const [currentModel, setCurrentModel] = useState(() => {
    try {
      return localStorage.getItem('chatui-current-model') || ''
    } catch {
      return ''
    }
  })
  const [agentModeAvailable, setAgentModeAvailable] = useState(false)
  const [isInAdminGroup, setIsInAdminGroup] = useState(false)

  // Fetch config from backend - extracted to allow refresh
  const fetchConfig = useCallback(async () => {
    try {
      const res = await fetch('/api/config')
      if (!res.ok) throw new Error(res.status)
      const cfg = await res.json()
      setAppName(cfg.app_name || 'Chat UI')
      setModels(cfg.models || [])
      const uniqueTools = (cfg.tools || []).map(server => ({
        ...server,
          tools: Array.from(new Set(server.tools))
      }))
      setTools(uniqueTools)
      setPrompts(cfg.prompts || [])
      setDataSources(cfg.data_sources || [])
      setRagServers(cfg.rag_servers || []) // Capture rich RAG server data
      setUser(cfg.user || 'Unknown')
      setFeatures({ ...DEFAULT_FEATURES, ...(cfg.features || {}) })
      setFileExtraction({ ...DEFAULT_FILE_EXTRACTION, ...(cfg.file_extraction || {}) })
      // Agent mode availability flag from backend
      setAgentModeAvailable(!!cfg.agent_mode_available)
      // Admin group membership flag from backend
      setIsInAdminGroup(!!cfg.is_in_admin_group)
      return cfg
    } catch (err) {
      // Config fetch failed - likely authentication issue
      console.error('Failed to fetch /api/config:', err)
      setAppName('Chat UI (Unauthenticated)')
      setModels([])
      setTools([])
      setDataSources([])
      setUser('Unauthenticated')
      setFeatures(DEFAULT_FEATURES)
      setAgentModeAvailable(false)
      return null
    }
  }, [])

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
    ragServers, // Expose new state
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
    refreshConfig: fetchConfig, // Allow manual refresh of config (e.g., after MCP reload)
  }
}
