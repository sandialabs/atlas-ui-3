import { useCallback, useMemo } from 'react'
import { usePersistentState } from './usePersistentState'

const toSet = arr => new Set(arr)
const toArray = set => Array.from(set)

// Prefix that marks an active-prompt key as a user-authored custom prompt
// (issue #153) rather than an MCP-server prompt. The colon cannot appear in an
// MCP key (format "server_promptname"), so the namespaces never collide. User
// prompts replace the system prompt client-side and are never sent as
// selected_prompts (which are MCP prompts prepended server-side).
export const USER_PROMPT_PREFIX = 'userprompt:'

export const isUserPromptKey = key => typeof key === 'string' && key.startsWith(USER_PROMPT_PREFIX)
export const userPromptKey = id => `${USER_PROMPT_PREFIX}${id}`
export const userPromptIdFromKey = key => (isUserPromptKey(key) ? key.slice(USER_PROMPT_PREFIX.length) : null)

export function useSelections() {
  // Auto-select canvas tool if empty
  const [toolsRaw, setToolsRaw] = usePersistentState('chatui-selected-tools', ['canvas_canvas'])
  const [promptsRaw, setPromptsRaw] = usePersistentState('chatui-selected-prompts', [])
  const [dataSourcesRaw, setDataSourcesRaw] = usePersistentState('chatui-selected-data-sources', [])
  const [complianceLevelFilter, setComplianceLevelFilter] = usePersistentState('chatui-compliance-level-filter', null)

  // RAG toggle: persistent boolean for enabling/disabling RAG
  const [ragEnabled, setRagEnabled] = usePersistentState('chatui-rag-enabled', false)

  // New state: activePromptKey stores which prompt is currently active (null = use default)
  const [activePromptKey, setActivePromptKey] = usePersistentState('chatui-active-prompt', null)

  const selectedTools = useMemo(() => toSet(toolsRaw), [toolsRaw])
  const selectedPrompts = useMemo(() => toSet(promptsRaw), [promptsRaw])
  const selectedDataSources = useMemo(() => toSet(dataSourcesRaw), [dataSourcesRaw])
  
  // activePrompts: array to send to backend (empty array for default, or array with active prompt)
  const activePrompts = useMemo(() => {
    if (!activePromptKey) return []
    return [activePromptKey]
  }, [activePromptKey])

  const toggleSetItem = (currentSet, setUpdater, key) => {
    const next = new Set(currentSet)
    next.has(key) ? next.delete(key) : next.add(key)
    setUpdater(toArray(next))
  }

  const toggleTool = useCallback(k => toggleSetItem(selectedTools, setToolsRaw, k), [selectedTools, setToolsRaw])
  const togglePrompt = useCallback(k => toggleSetItem(selectedPrompts, setPromptsRaw, k), [selectedPrompts, setPromptsRaw])
  const toggleDataSource = useCallback(k => toggleSetItem(selectedDataSources, setDataSourcesRaw, k), [selectedDataSources, setDataSourcesRaw])

  // Batch operations (avoid stale snapshot when toggling many items sequentially)
  const addTools = useCallback(keys => {
    if (!Array.isArray(keys) || keys.length === 0) return
    setToolsRaw(prev => {
      const next = new Set(prev)
      keys.forEach(k => next.add(k))
      return toArray(next)
    })
  }, [setToolsRaw])

  const removeTools = useCallback(keys => {
    if (!Array.isArray(keys) || keys.length === 0) return
    setToolsRaw(prev => {
      const next = new Set(prev)
      keys.forEach(k => next.delete(k))
      return toArray(next)
    })
  }, [setToolsRaw])

  const setSinglePrompt = useCallback(promptKey => {
    // Enforce only 0 or 1 prompt globally
    if (!promptKey) {
      setPromptsRaw([])
      return
    }
    setPromptsRaw([promptKey])
  }, [setPromptsRaw])

  const addPrompts = useCallback(keys => {
    if (!Array.isArray(keys) || keys.length === 0) return
    setPromptsRaw(prev => {
      const next = new Set(prev)
      keys.forEach(k => next.add(k))
      return toArray(next)
    })
  }, [setPromptsRaw])

  const removePrompts = useCallback(keys => {
    if (!Array.isArray(keys) || keys.length === 0) return
    setPromptsRaw(prev => {
      const next = new Set(prev)
      keys.forEach(k => next.delete(k))
      return toArray(next)
    })
  }, [setPromptsRaw])

  // Batch operations for data sources
  const addDataSources = useCallback(keys => {
    if (!Array.isArray(keys) || keys.length === 0) return
    setDataSourcesRaw(prev => {
      const next = new Set(prev)
      keys.forEach(k => next.add(k))
      return toArray(next)
    })
  }, [setDataSourcesRaw])

  const clearDataSources = useCallback(() => {
    setDataSourcesRaw([])
  }, [setDataSourcesRaw])

  const makePromptActive = useCallback(promptKey => {
    // Set the active prompt key (null for default)
    setActivePromptKey(promptKey)
    // Ensure MCP prompts are in the selectedPrompts set so they get loaded.
    // User-authored prompts (issue #153) are resolved client-side and must NOT
    // be added here, or they'd leak into the MCP selected_prompts payload.
    if (promptKey && !isUserPromptKey(promptKey) && !promptsRaw.includes(promptKey)) {
      setPromptsRaw(prev => [...prev, promptKey])
    }
  }, [setActivePromptKey, promptsRaw, setPromptsRaw])
  
  const clearActivePrompt = useCallback(() => {
    // Clear the active prompt to use default (but keep prompts loaded)
    setActivePromptKey(null)
  }, [setActivePromptKey])

  const toggleRagEnabled = useCallback(() => {
    setRagEnabled(prev => !prev)
  }, [setRagEnabled])

  const clearToolsAndPrompts = useCallback(() => {
    setToolsRaw([])
    setPromptsRaw([])
    localStorage.removeItem('chatui-selected-tools')
    localStorage.removeItem('chatui-selected-prompts')
  }, [setToolsRaw, setPromptsRaw])

  return {
    selectedTools,
    selectedPrompts,
    activePrompts,
    activePromptKey,
    selectedDataSources,
    toggleTool,
    togglePrompt,
    toggleDataSource,
    addTools,
    removeTools,
    addPrompts,
    setSinglePrompt,
    removePrompts,
    makePromptActive,
    clearActivePrompt,
    addDataSources,
    clearDataSources,
    clearToolsAndPrompts,
    complianceLevelFilter,
    setComplianceLevelFilter,
    ragEnabled,
    setRagEnabled,
    toggleRagEnabled
  }
}
