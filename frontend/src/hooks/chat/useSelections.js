import { useCallback, useMemo } from 'react'
import { usePersistentState } from './usePersistentState'
import { CANVAS_TOOL, migrateToolName, migrateToolNames } from '../../constants/atlasTools'

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
  const [toolsRaw, setToolsRaw] = usePersistentState('chatui-selected-tools', [CANVAS_TOOL])
  const [promptsRaw, setPromptsRaw] = usePersistentState('chatui-selected-prompts', [])
  const [dataSourcesRaw, setDataSourcesRaw] = usePersistentState('chatui-selected-data-sources', [])
  const [complianceLevelFilter, setComplianceLevelFilter] = usePersistentState('chatui-compliance-level-filter', null)

  // RAG toggle: persistent boolean for enabling/disabling RAG
  const [ragEnabled, setRagEnabled] = usePersistentState('chatui-rag-enabled', false)

  // New state: activePromptKey stores which prompt is currently active (null = use default)
  const [activePromptKey, setActivePromptKey] = usePersistentState('chatui-active-prompt', null)

  // Browsers still hold pre-#855 built-in names ('canvas_canvas' and friends);
  // migrate on read so an old selection keeps working without a reset.
  const selectedTools = useMemo(() => toSet(migrateToolNames(toolsRaw)), [toolsRaw])
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

  const toggleTool = useCallback(k => toggleSetItem(selectedTools, setToolsRaw, migrateToolName(k)), [selectedTools, setToolsRaw])
  const togglePrompt = useCallback(k => toggleSetItem(selectedPrompts, setPromptsRaw, k), [selectedPrompts, setPromptsRaw])
  const toggleDataSource = useCallback(k => toggleSetItem(selectedDataSources, setDataSourcesRaw, k), [selectedDataSources, setDataSourcesRaw])

  // Batch operations (avoid stale snapshot when toggling many items sequentially)
  // Both operate on the migrated form of what is persisted, so a write also
  // converges storage onto the post-#855 names instead of leaving a legacy
  // name sitting beside its replacement forever.
  const addTools = useCallback(keys => {
    if (!Array.isArray(keys) || keys.length === 0) return
    setToolsRaw(prev => {
      const next = new Set(migrateToolNames(prev))
      migrateToolNames(keys).forEach(k => next.add(k))
      return toArray(next)
    })
  }, [setToolsRaw])

  const removeTools = useCallback(keys => {
    if (!Array.isArray(keys) || keys.length === 0) return
    setToolsRaw(prev => {
      const next = new Set(migrateToolNames(prev))
      migrateToolNames(keys).forEach(k => next.delete(k))
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

  // --- Workspaces -----------------------------------------------------------
  // A workspace is a saved snapshot of the selections below. Snapshot/apply
  // live here (rather than in the workspace hook) because this hook owns the
  // canonical shape of a selection; keeping them together means adding a new
  // selection dimension is a single-file change.

  const snapshotSelections = useCallback(() => ({
    active_prompt_key: activePromptKey || null,
    selected_tools: toArray(selectedTools),
    selected_prompts: toArray(selectedPrompts),
    selected_data_sources: toArray(selectedDataSources),
    rag_enabled: !!ragEnabled,
  }), [activePromptKey, selectedTools, selectedPrompts, selectedDataSources, ragEnabled])

  const applyWorkspace = useCallback(config => {
    if (!config || typeof config !== 'object') return
    const list = v => (Array.isArray(v) ? v.filter(k => typeof k === 'string') : [])
    // Replace rather than merge: a workspace describes the whole context, so
    // leftovers from the previous one would silently widen tool/RAG access.
    setToolsRaw(list(config.selected_tools))
    setPromptsRaw(list(config.selected_prompts))
    setDataSourcesRaw(list(config.selected_data_sources))
    setRagEnabled(!!config.rag_enabled)
    const promptKey = typeof config.active_prompt_key === 'string' && config.active_prompt_key
      ? config.active_prompt_key
      : null
    setActivePromptKey(promptKey)
    // An MCP active prompt must also be loaded, mirroring makePromptActive.
    if (promptKey && !isUserPromptKey(promptKey)) {
      setPromptsRaw(prev => (prev.includes(promptKey) ? prev : [...prev, promptKey]))
    }
  }, [setToolsRaw, setPromptsRaw, setDataSourcesRaw, setRagEnabled, setActivePromptKey])

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
    snapshotSelections,
    applyWorkspace,
    clearToolsAndPrompts,
    complianceLevelFilter,
    setComplianceLevelFilter,
    ragEnabled,
    setRagEnabled,
    toggleRagEnabled
  }
}
