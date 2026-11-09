import { useCallback, useEffect, useState } from 'react'
import { Plus, Search, Pencil, History, Share2, GitFork, Archive, Users, Lock } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'
import { libraryPromptKey, libraryDraftKey, parseLibraryPromptKey } from '../hooks/chat/useSelections'
import PromptLibraryEditor from './PromptLibraryEditor'
import PromptLibrarySharing from './PromptLibrarySharing'
import PromptLibraryVersions from './PromptLibraryVersions'

/**
 * Team-shared prompt library (issue #1169) -- the "Team Library" section of
 * the Settings "Prompts" tab, below the private custom prompts.
 *
 * Browse and search what you may see (Mine / Shared with me / Team), create
 * private entries, edit and publish versions, share with groups and people,
 * view history, fork, and pick an entry (latest or a specific version) for
 * chat. Buttons follow the permission the server reports for each entry; the
 * server enforces the same rules on every request regardless.
 */
const FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'mine', label: 'Mine' },
  { value: 'shared', label: 'Shared with me' },
  { value: 'team', label: 'Team' },
]

const PromptLibraryPanel = () => {
  const { promptLibrary: library, activePromptKey, makePromptActive } = useChat()
  const [scope, setScope] = useState('all')
  const [query, setQuery] = useState('')
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  // { type: 'create' } | { type: 'edit'|'share'|'versions', id }
  const [open, setOpen] = useState(null)
  const [detail, setDetail] = useState(null)

  const load = useCallback(async () => {
    if (!library) return
    setLoading(true)
    setError(null)
    try {
      setItems(await library.listPrompts(scope, query))
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [library, scope, query])

  // Debounced so typing a search does not fire a request per keystroke.
  useEffect(() => {
    const t = setTimeout(load, 250)
    return () => clearTimeout(t)
  }, [load])

  if (!library) return null

  const act = async fn => {
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(e.message)
    }
  }

  const openPanel = async (type, prompt) => {
    if (type === 'edit') {
      await act(async () => {
        setDetail(await library.getPrompt(prompt.id))
        setOpen({ type, id: prompt.id })
      })
      return
    }
    setDetail(prompt)
    setOpen({ type, id: prompt.id })
  }
  const close = () => {
    setOpen(null)
    setDetail(null)
    load()
  }

  const active = parseLibraryPromptKey(activePromptKey)
  const selectEntry = (id, versionId = null) => makePromptActive(libraryPromptKey(id, versionId))

  const fork = (prompt, versionId = null) => act(async () => {
    const created = await library.forkPrompt(prompt.id, versionId)
    setScope('mine')
    setDetail(created)
    setOpen({ type: 'edit', id: created.id })
  })

  const archive = prompt => act(async () => {
    if (!window.confirm(`Archive "${prompt.title}"? It disappears from the library and can no longer be used. Conversations that used it keep their history.`)) return
    await library.archivePrompt(prompt.id)
    await load()
  })

  const renderPanelFor = prompt => {
    if (!open || open.id !== prompt.id || !detail) return null
    if (open.type === 'edit') {
      return (
        <PromptLibraryEditor
          prompt={detail}
          onCancel={close}
          onSave={async fields => setDetail(await library.updatePrompt(prompt.id, fields))}
          onPublish={async change => {
            await library.publishPrompt(prompt.id, change)
            setDetail(await library.getPrompt(prompt.id))
          }}
          onTest={({ title, content }) => {
            library.setTestDraft({ promptId: prompt.id, title, content })
            makePromptActive(libraryDraftKey(prompt.id))
          }}
        />
      )
    }
    if (open.type === 'share') return <PromptLibrarySharing prompt={detail} library={library} onClose={close} />
    if (open.type === 'versions') {
      return (
        <PromptLibraryVersions
          prompt={detail}
          library={library}
          activeVersionId={active && active.promptId === prompt.id ? active.versionId : undefined}
          onUseVersion={versionId => selectEntry(prompt.id, versionId)}
          onFork={versionId => fork(prompt, versionId)}
          onClose={close}
        />
      )
    }
    return null
  }

  const iconButton = (label, Icon, onClick, extra = '') => (
    <button type="button" onClick={onClick} title={label} aria-label={label}
      className={`p-1.5 rounded bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors ${extra}`}>
      <Icon className="w-4 h-4" />
    </button>
  )

  return (
    <section className="space-y-4" aria-labelledby="team-library-heading">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h3 id="team-library-heading" className="text-gray-50 font-medium">Team Library</h3>
          <p className="text-sm text-gray-400 mt-1">
            Versioned prompts you can share with your groups. New prompts are private until you share them.
          </p>
        </div>
        {open?.type !== 'create' && (
          <button type="button" onClick={() => setOpen({ type: 'create' })}
            className="flex items-center gap-1 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium flex-shrink-0">
            <Plus className="w-4 h-4" /> New library prompt
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div role="group" aria-label="Filter library" className="flex rounded-lg overflow-hidden border border-gray-600">
          {FILTERS.map(f => (
            <button key={f.value} type="button" aria-pressed={scope === f.value} onClick={() => setScope(f.value)}
              className={`px-3 py-1.5 text-sm ${scope === f.value ? 'bg-blue-700 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}`}>
              {f.label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 flex-1 min-w-[12rem] bg-gray-700 rounded-lg px-2 border border-gray-600">
          <Search className="w-4 h-4 text-gray-400" />
          <span className="sr-only">Search library</span>
          <input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Search title or description"
            className="flex-1 bg-transparent py-1.5 text-sm text-gray-100 focus:outline-none" />
        </label>
      </div>

      {open?.type === 'create' && (
        <PromptLibraryEditor
          onCancel={() => setOpen(null)}
          onSave={async fields => {
            const created = await library.createPrompt(fields)
            setDetail(created)
            setOpen({ type: 'edit', id: created.id })
            setScope('mine')
          }}
        />
      )}

      {/* An entry just created or forked may not be in the current list yet
          (another filter, or the reload is still in flight): keep its editor
          on screen rather than letting it vanish. */}
      {open && open.type !== 'create' && detail && !items.some(p => p.id === open.id) && renderPanelFor(detail)}

      {error && <div role="alert" className="p-3 bg-red-900/30 border border-red-700 rounded-lg text-red-300 text-sm">{error}</div>}
      {loading && items.length === 0 && <div className="text-sm text-gray-400">Loading library...</div>}
      {!loading && items.length === 0 && !error && (
        <div className="p-6 bg-gray-700 rounded-lg text-center text-gray-400 text-sm">
          {query ? 'No matching prompts.' : 'Nothing here yet.'}
        </div>
      )}

      <ul className="space-y-3">
        {items.map(p => {
          const isActive = active && active.promptId === p.id
          const sharedGroups = p.sharing?.groups || (p.is_owner ? [] : p.access_via.filter(v => v !== 'user' && v !== 'owner'))
          return (
            <li key={p.id} className="space-y-2">
              <div className="bg-gray-700 rounded-lg p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-gray-100 truncate">{p.title}</span>
                      <span className="text-xs text-gray-400">
                        {p.latest_version ? `v${p.latest_version.version_number}` : 'unpublished'}
                      </span>
                      {p.has_unpublished_changes && p.latest_version && (
                        <span className="text-xs text-amber-400">draft changes</span>
                      )}
                      {isActive && <span className="text-xs text-emerald-400">active{active.versionId ? ' (pinned version)' : ''}</span>}
                    </div>
                    {p.description && <p className="text-sm text-gray-400 mt-1 line-clamp-2">{p.description}</p>}
                    <div className="text-xs text-gray-500 mt-1 flex items-center gap-2 flex-wrap">
                      <span>{p.is_owner ? 'Owned by you' : `Owner: ${p.owner_email}`}</span>
                      <span className="flex items-center gap-1">
                        {p.shared || sharedGroups.length ? <Users className="w-3 h-3" /> : <Lock className="w-3 h-3" />}
                        {sharedGroups.length ? sharedGroups.join(', ') : (p.shared ? 'shared' : 'private')}
                      </span>
                      {!p.is_owner && <span>your access: {p.permission}</span>}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button type="button" disabled={!p.latest_version} onClick={() => selectEntry(p.id)}
                      className={`px-2 py-1 rounded text-xs font-medium ${isActive ? 'bg-emerald-700 text-white' : 'bg-gray-600 hover:bg-gray-500 text-gray-200'} disabled:opacity-40`}>
                      {isActive ? 'Active' : 'Use'}
                    </button>
                    {p.can_edit && iconButton(`Edit ${p.title}`, Pencil, () => openPanel('edit', p))}
                    {iconButton(`Version history for ${p.title}`, History, () => openPanel('versions', p))}
                    {p.can_manage && iconButton(`Share ${p.title}`, Share2, () => openPanel('share', p))}
                    {p.latest_version && iconButton(`Fork ${p.title}`, GitFork, () => fork(p))}
                    {p.is_owner && iconButton(`Archive ${p.title}`, Archive, () => archive(p), 'hover:bg-red-600')}
                  </div>
                </div>
              </div>
              {renderPanelFor(p)}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

export default PromptLibraryPanel
