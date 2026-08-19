import { useState, useRef, useEffect } from 'react'
import { Layers, ChevronDown, Plus, Save, Trash2, Pencil } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'
import { isUserPromptKey, userPromptIdFromKey } from '../hooks/chat/useSelections'
import { getMcpNameFromKey } from '../utils/mcpKeys'
import { useDialog } from './ui/toastContext'
import { useToast } from './ui/toastContext'

/**
 * Workspace switcher.
 *
 * A workspace bundles the active prompt, RAG data sources, and MCP tool
 * selections under a name, so switching context (work / home / project) is one
 * click instead of re-picking every selection. Saving captures whatever is
 * currently selected; switching replaces the current selections wholesale.
 */
const WorkspaceSelector = () => {
  const {
    workspaces = [],
    workspacesLoading,
    activeWorkspaceId,
    switchWorkspace,
    saveCurrentAsWorkspace,
    updateActiveWorkspace,
    renameWorkspace,
    deleteWorkspace,
    clearActiveWorkspace,
    userPrompts = [],
    prompts = [],
  } = useChat()

  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef(null)
  const dialog = useDialog()
  const toast = useToast()

  useEffect(() => {
    if (!isOpen) return
    const handleClickOutside = event => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [isOpen])

  const active = workspaces.find(w => w.id === activeWorkspaceId) || null

  // Name the workspace's prompt rather than just noting that it has one -- the
  // prompt is as much a part of the context as the tools, and "custom prompt"
  // does not tell you which one you are about to switch into.
  const promptLabel = key => {
    if (!key) return null
    if (isUserPromptKey(key)) {
      const match = userPrompts.find(p => p.id === userPromptIdFromKey(key))
      // A prompt deleted out from under the workspace still deserves a label.
      return match ? match.title : 'custom prompt'
    }
    return getMcpNameFromKey(key, prompts) || 'custom prompt'
  }

  const summarize = ws => {
    const c = ws.config || {}
    const parts = []
    const tools = c.selected_tools?.length || 0
    const sources = c.selected_data_sources?.length || 0
    if (tools) parts.push(`${tools} tool${tools === 1 ? '' : 's'}`)
    if (sources) parts.push(`${sources} source${sources === 1 ? '' : 's'}`)
    const prompt = promptLabel(c.active_prompt_key)
    if (prompt) parts.push(prompt)
    return parts.length ? parts.join(' · ') : 'no selections'
  }

  const handleSaveNew = async () => {
    const answer = await dialog.prompt({
      title: 'Save current context as workspace',
      label: 'Name',
      placeholder: 'e.g. Project A',
      secondaryLabel: 'Description (optional)',
      secondaryPlaceholder: 'What is this workspace for?',
      okText: 'Save',
      required: true,
    })
    if (!answer) return
    const name = (answer.value || '').trim()
    if (!name) return
    const created = await saveCurrentAsWorkspace(name, answer.secondary || null)
    if (created) {
      toast.success(`Workspace "${created.name}" saved`)
      setIsOpen(false)
    } else {
      toast.error('Could not save workspace')
    }
  }

  const handleUpdate = async () => {
    if (!active) return
    const updated = await updateActiveWorkspace()
    if (updated) {
      toast.success(`"${updated.name}" updated with the current context`)
      setIsOpen(false)
    } else {
      toast.error('Could not update workspace')
    }
  }

  const handleRename = async (event, ws) => {
    event.stopPropagation()
    const answer = await dialog.prompt({
      title: 'Rename workspace',
      label: 'Name',
      defaultValue: ws.name,
      okText: 'Rename',
      required: true,
    })
    if (!answer) return
    const name = (answer.value || '').trim()
    if (!name || name === ws.name) return
    const updated = await renameWorkspace(ws.id, name)
    if (!updated) toast.error('Could not rename workspace')
  }

  const handleDelete = async (event, ws) => {
    event.stopPropagation()
    const ok = await dialog.confirm({
      title: 'Delete workspace',
      message: `Delete "${ws.name}"? Your current selections stay as they are.`,
      okText: 'Delete',
      destructive: true,
    })
    if (!ok) return
    const deleted = await deleteWorkspace(ws.id)
    if (deleted) toast.success(`Workspace "${ws.name}" deleted`)
    else toast.error('Could not delete workspace')
  }

  return (
    <div ref={dropdownRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className={`flex items-center gap-2 px-2 sm:px-3 py-2 rounded-lg transition-colors ${
          active
            ? 'bg-purple-600 hover:bg-purple-700 text-white'
            : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
        }`}
        title={active ? `Workspace: ${active.name}` : 'Workspaces -- saved prompt, source, and tool bundles'}
        aria-label={active ? `Workspaces: ${active.name}` : 'Workspaces'}
      >
        <Layers className="w-4 h-4 sm:w-5 sm:h-5" />
        <span className="text-sm font-medium hidden lg:inline max-w-[10rem] truncate">
          {active ? active.name : 'Workspace'}
        </span>
        <ChevronDown className="w-3 h-3 flex-shrink-0" />
      </button>

      {isOpen && (
        <div className="absolute left-0 top-full mt-1 w-80 bg-gray-800 border border-gray-600 rounded-lg shadow-lg z-50 max-h-[28rem] overflow-y-auto">
          <div className="p-2 border-b border-gray-700">
            <div className="text-xs font-semibold text-gray-300 flex items-center gap-2">
              <Layers className="w-3 h-3 text-purple-400" />
              Workspaces
            </div>
            <div className="text-xs text-gray-400 mt-1">
              Switch prompt, data sources, and tools together
            </div>
          </div>

          <button
            onClick={() => {
              clearActiveWorkspace()
              setIsOpen(false)
            }}
            className={`w-full px-3 py-2 text-left hover:bg-gray-700 transition-colors border-b border-gray-700 ${
              !activeWorkspaceId ? 'bg-purple-900/30' : ''
            }`}
          >
            <div className="font-medium text-gray-200 flex items-center gap-2">
              {!activeWorkspaceId && <span className="text-purple-400">✓</span>}
              <span>No workspace</span>
            </div>
            <div className="text-xs text-gray-400 mt-1">
              Keep the current selections without tracking a workspace
            </div>
          </button>

          {workspacesLoading && (
            <div className="px-3 py-2 text-sm text-gray-400">Loading workspaces...</div>
          )}

          {!workspacesLoading && workspaces.length === 0 && (
            <div className="px-3 py-3 text-sm text-gray-400">
              No workspaces yet. Select the prompt, sources, and tools you want,
              then save them below.
            </div>
          )}

          {workspaces.map(ws => {
            const isActive = ws.id === activeWorkspaceId
            return (
              <div
                key={ws.id}
                className={`flex items-start gap-1 border-b border-gray-700 ${
                  isActive ? 'bg-purple-900/30' : ''
                }`}
              >
                <button
                  onClick={() => {
                    switchWorkspace(ws.id)
                    setIsOpen(false)
                  }}
                  className="flex-1 min-w-0 px-3 py-2 text-left hover:bg-gray-700 transition-colors"
                >
                  <div className="font-medium text-gray-200 flex items-center gap-2">
                    {isActive && <span className="text-purple-400">✓</span>}
                    <span className="truncate">{ws.name}</span>
                  </div>
                  {ws.description && (
                    <div className="text-xs text-gray-400 mt-1 line-clamp-2">{ws.description}</div>
                  )}
                  <div className="text-xs text-gray-500 mt-1">{summarize(ws)}</div>
                </button>
                <div className="flex items-center gap-1 pr-2 pt-2">
                  <button
                    onClick={e => handleRename(e, ws)}
                    className="p-1 rounded text-gray-400 hover:text-gray-100 hover:bg-gray-600"
                    title={`Rename ${ws.name}`}
                    aria-label={`Rename ${ws.name}`}
                  >
                    <Pencil className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={e => handleDelete(e, ws)}
                    className="p-1 rounded text-gray-400 hover:text-red-400 hover:bg-gray-600"
                    title={`Delete ${ws.name}`}
                    aria-label={`Delete ${ws.name}`}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            )
          })}

          <button
            onClick={handleSaveNew}
            className="w-full px-3 py-2 text-left hover:bg-gray-700 transition-colors flex items-center gap-2 text-sm text-gray-200"
          >
            <Plus className="w-4 h-4 text-purple-400" />
            Save current context as workspace
          </button>

          {active && (
            <button
              onClick={handleUpdate}
              className="w-full px-3 py-2 text-left hover:bg-gray-700 transition-colors flex items-center gap-2 text-sm text-gray-200 border-t border-gray-700"
            >
              <Save className="w-4 h-4 text-purple-400" />
              <span className="truncate">Update "{active.name}" with current context</span>
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export default WorkspaceSelector
