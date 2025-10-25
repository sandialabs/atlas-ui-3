import { useCallback, useMemo } from 'react'
import { usePersistentState } from './usePersistentState'

const toSet = arr => new Set(arr)
const toArray = set => Array.from(set)

export function useSelections() {
  // Auto-select canvas tool if empty
  const [toolsRaw, setToolsRaw] = usePersistentState('chatui-selected-tools', ['canvas_canvas'])
  const [promptsRaw, setPromptsRaw] = usePersistentState('chatui-selected-prompts', [])
  const [dataSourcesRaw, setDataSourcesRaw] = usePersistentState('chatui-selected-data-sources', [])
  const [toolChoiceRequired, setToolChoiceRequired] = usePersistentState('chatui-tool-choice-required', false)

  const selectedTools = useMemo(() => toSet(toolsRaw), [toolsRaw])
  const selectedPrompts = useMemo(() => toSet(promptsRaw), [promptsRaw])
  const selectedDataSources = useMemo(() => toSet(dataSourcesRaw), [dataSourcesRaw])

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

  const removePrompts = useCallback(keys => {
    if (!Array.isArray(keys) || keys.length === 0) return
    setPromptsRaw(prev => {
      const next = new Set(prev)
      keys.forEach(k => next.delete(k))
      return toArray(next)
    })
  }, [setPromptsRaw])

  const clearToolsAndPrompts = useCallback(() => {
    setToolsRaw([])
    setPromptsRaw([])
    localStorage.removeItem('chatui-selected-tools')
    localStorage.removeItem('chatui-selected-prompts')
  }, [setToolsRaw, setPromptsRaw])

  return {
    selectedTools,
    selectedPrompts,
    selectedDataSources,
    toggleTool,
    togglePrompt,
    toggleDataSource,
  addTools,
  removeTools,
  setSinglePrompt,
  removePrompts,
    toolChoiceRequired,
    setToolChoiceRequired,
    clearToolsAndPrompts
  }
}
