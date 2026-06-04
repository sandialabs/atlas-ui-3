import { useState } from 'react'
import { Plus, Pencil, Trash2, Check, X, Sparkles } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'
import { userPromptKey, isUserPromptKey, userPromptIdFromKey } from '../hooks/chat/useSelections'

/**
 * Prompt library manager (issue #153) — lives in the Settings "Prompts" tab.
 *
 * Users create, edit, and delete reusable custom prompts here. Picking which
 * one is active for a chat happens in the prompt selector above the chat input;
 * this panel is the management surface and also lets you activate one directly.
 */
const emptyDraft = { title: '', content: '' }

const PromptManager = () => {
  const {
    userPrompts = [],
    userPromptsLoading,
    userPromptsError,
    createUserPrompt,
    updateUserPrompt,
    deleteUserPrompt,
    activePromptKey,
    makePromptActive,
    clearActivePrompt,
  } = useChat()

  // editingId: null = not editing; 'new' = creating; otherwise an existing id
  const [editingId, setEditingId] = useState(null)
  const [draft, setDraft] = useState(emptyDraft)
  const [saving, setSaving] = useState(false)

  const activeId = isUserPromptKey(activePromptKey) ? userPromptIdFromKey(activePromptKey) : null
  const canSave = draft.title.trim().length > 0 && draft.content.trim().length > 0

  const startCreate = () => {
    setEditingId('new')
    setDraft(emptyDraft)
  }

  const startEdit = (prompt) => {
    setEditingId(prompt.id)
    setDraft({ title: prompt.title, content: prompt.content })
  }

  const cancelEdit = () => {
    setEditingId(null)
    setDraft(emptyDraft)
  }

  const handleSave = async () => {
    if (!canSave || saving) return
    setSaving(true)
    let result
    if (editingId === 'new') {
      result = await createUserPrompt(draft.title.trim(), draft.content)
    } else {
      result = await updateUserPrompt(editingId, draft.title.trim(), draft.content)
    }
    setSaving(false)
    if (result) cancelEdit()
  }

  const handleDelete = async (prompt) => {
    if (!window.confirm(`Delete prompt "${prompt.title}"? This cannot be undone.`)) return
    const ok = await deleteUserPrompt(prompt.id)
    // Only fall back to the default prompt once the delete actually succeeds —
    // otherwise a failed request would silently drop the still-existing prompt.
    if (ok && activeId === prompt.id && clearActivePrompt) clearActivePrompt()
  }

  const toggleActive = (prompt) => {
    if (activeId === prompt.id) {
      clearActivePrompt?.()
    } else {
      makePromptActive?.(userPromptKey(prompt.id))
    }
  }

  const renderEditor = () => (
    <div className="bg-gray-700 rounded-lg p-4 space-y-3 border border-gray-600">
      <input
        type="text"
        value={draft.title}
        onChange={(e) => setDraft(d => ({ ...d, title: e.target.value }))}
        placeholder="Prompt title (e.g. Concise code reviewer)"
        maxLength={200}
        className="w-full px-3 py-2 bg-gray-800 text-gray-50 rounded-lg border border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
      <textarea
        value={draft.content}
        onChange={(e) => setDraft(d => ({ ...d, content: e.target.value }))}
        placeholder="System prompt text. This fully replaces the default system prompt when active."
        rows={8}
        className="w-full px-3 py-2 bg-gray-800 text-gray-50 rounded-lg border border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono text-sm resize-y"
      />
      <div className="flex items-center justify-end gap-2">
        <button
          onClick={cancelEdit}
          className="flex items-center gap-1 px-3 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors text-sm"
        >
          <X className="w-4 h-4" /> Cancel
        </button>
        <button
          onClick={handleSave}
          disabled={!canSave || saving}
          className={`flex items-center gap-1 px-3 py-2 rounded-lg transition-colors text-sm font-medium ${
            canSave && !saving ? 'bg-blue-600 hover:bg-blue-700 text-white' : 'bg-gray-600 text-gray-400 cursor-not-allowed'
          }`}
        >
          <Check className="w-4 h-4" /> {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-gray-50 font-medium">Custom Prompts</h3>
          <p className="text-sm text-gray-400 mt-1">
            Save reusable system prompts. When you select one in the prompt picker
            (above the chat box), it replaces the default system prompt.
          </p>
        </div>
        {editingId !== 'new' && (
          <button
            onClick={startCreate}
            className="flex items-center gap-1 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors text-sm font-medium flex-shrink-0"
          >
            <Plus className="w-4 h-4" /> New Prompt
          </button>
        )}
      </div>

      {userPromptsError && (
        <div className="p-3 bg-red-900/30 border border-red-700 rounded-lg text-red-300 text-sm">
          {userPromptsError}
        </div>
      )}

      {editingId === 'new' && renderEditor()}

      {userPromptsLoading && userPrompts.length === 0 && (
        <div className="text-sm text-gray-400">Loading prompts…</div>
      )}

      {!userPromptsLoading && userPrompts.length === 0 && editingId !== 'new' && (
        <div className="p-6 bg-gray-700 rounded-lg text-center text-gray-400 text-sm">
          No custom prompts yet. Create one to get started.
        </div>
      )}

      <div className="space-y-3">
        {userPrompts.map((prompt) => (
          editingId === prompt.id ? (
            <div key={prompt.id}>{renderEditor()}</div>
          ) : (
            <div key={prompt.id} className="bg-gray-700 rounded-lg p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-gray-100 truncate">{prompt.title}</span>
                    {activeId === prompt.id && (
                      <span className="text-xs text-emerald-400 flex items-center gap-1 flex-shrink-0">
                        <Sparkles className="w-3 h-3" /> active
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-gray-400 mt-1 whitespace-pre-wrap line-clamp-3">
                    {prompt.content}
                  </p>
                </div>
                <div className="flex items-center gap-1 flex-shrink-0">
                  <button
                    onClick={() => toggleActive(prompt)}
                    title={activeId === prompt.id ? 'Deactivate' : 'Use this prompt'}
                    className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                      activeId === prompt.id
                        ? 'bg-emerald-700 hover:bg-emerald-600 text-white'
                        : 'bg-gray-600 hover:bg-gray-500 text-gray-200'
                    }`}
                  >
                    {activeId === prompt.id ? 'Active' : 'Use'}
                  </button>
                  <button
                    onClick={() => startEdit(prompt)}
                    title="Edit"
                    className="p-1.5 rounded bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors"
                  >
                    <Pencil className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => handleDelete(prompt)}
                    title="Delete"
                    className="p-1.5 rounded bg-gray-600 hover:bg-red-600 text-gray-200 transition-colors"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          )
        ))}
      </div>
    </div>
  )
}

export default PromptManager
