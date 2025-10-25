import { useEffect, useState } from 'react'

const DEFAULT_FEATURES = {
  workspaces: false,
  rag: false,
  tools: false,
  marketplace: false,
  files_panel: false,
  chat_history: false
}

export function useChatConfig() {
  const [appName, setAppName] = useState('Chat UI')
  const [user, setUser] = useState('Unknown')
  const [models, setModels] = useState([])
  const [tools, setTools] = useState([])
  const [prompts, setPrompts] = useState([])
  const [dataSources, setDataSources] = useState([])
  const [features, setFeatures] = useState(DEFAULT_FEATURES)
  // Load saved model from localStorage
  const [currentModel, setCurrentModel] = useState(() => {
    try {
      return localStorage.getItem('chatui-current-model') || ''
    } catch {
      return ''
    }
  })
  const [onlyRag, setOnlyRag] = useState(false)
  const [agentModeAvailable, setAgentModeAvailable] = useState(false)
  const [isInAdminGroup, setIsInAdminGroup] = useState(false)

  useEffect(() => {
    (async () => {
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
        setUser(cfg.user || 'Unknown')
  setFeatures({ ...DEFAULT_FEATURES, ...(cfg.features || {}) })
  // Agent mode availability flag from backend
  setAgentModeAvailable(!!cfg.agent_mode_available)
        // Admin group membership flag from backend
        setIsInAdminGroup(!!cfg.is_in_admin_group)
        // Set default model if none saved and models available
        if (!currentModel && cfg.models?.length) {
          const defaultModel = cfg.models[0]
          setCurrentModel(defaultModel)
          localStorage.setItem('chatui-current-model', defaultModel)
        }
        // Validate saved model is still available
        else if (currentModel && cfg.models?.length && !cfg.models.includes(currentModel)) {
          const defaultModel = cfg.models[0]
          setCurrentModel(defaultModel)
          localStorage.setItem('chatui-current-model', defaultModel)
        }
      } catch (e) {
        // Fallback demo data
        setAppName('Chat UI (Demo)')
        setModels(['gpt-4o', 'gpt-4o-mini'])
        setTools([{ server: 'canvas', tools: ['canvas'], description: 'Create and display visual content', tool_count: 1, is_exclusive: false }])
        setDataSources(['demo_documents'])
        setUser('Demo User')
        // Set demo model if no saved model
        if (!currentModel) {
          setCurrentModel('gpt-4o')
          localStorage.setItem('chatui-current-model', 'gpt-4o')
        }
  setAgentModeAvailable(true)
      }
    })()
  }, [currentModel])

  return {
    appName,
    user,
    models,
    tools,
    prompts,
    dataSources,
    features,
    setFeatures,
    currentModel,
    setCurrentModel: (model) => {
      setCurrentModel(model)
      try {
        localStorage.setItem('chatui-current-model', model)
      } catch (e) {
        console.warn('Failed to save current model to localStorage:', e)
      }
    },
    onlyRag,
  setOnlyRag,
  agentModeAvailable,
  isInAdminGroup
  }
}
