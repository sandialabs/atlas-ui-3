import { useCallback, useEffect, useRef, useState } from 'react'
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
const PANEL_LABELS = { edit: 'Edit', versions: 'Version history for', share: 'Sharing for' }
const panelDomId = id => `prompt-library-panel-${id}`
const FOCUS_RING = 'focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-1 focus-visible:ring-offset-gray-800'

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
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [incomplete, setIncomplete] = useState(false)
  // { type: 'create' } | { type: 'edit'|'share'|'versions', id }
  const [open, setOpen] = useState(null)
  const [detail, setDetail] = useState(null)
  // The Edit/History/Share button that opened the current panel, so closing
  // it returns keyboard focus there; and the open panel, which takes focus.
  const triggers = useRef({})
  const panelRef = useRef(null)
  const focusPanel = useRef(false)

  // `library` is a fresh object on every render; its functions are stable.
  // Depending on the function (not the object) keeps `load` stable, so the
  // debounce below fires once per filter/search change instead of re-arming
  // on every render.
  const listPrompts = library?.listPrompts
  const load = useCallback(async () => {
    if (!listPrompts) return
    setLoading(true)
    setError(null)
    try {
      const page = await listPrompts(scope, query, 0)
      setItems(page.prompts)
      setTotal(page.total)
      setIncomplete(Boolean(page.incomplete))
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [listPrompts, scope, query])

  // Next page, appended. The server orders newest first, so a page never
  // repeats an entry unless the list changed meanwhile (dropped as a duplicate).
  const loadMore = async () => {
    setLoading(true)
    setError(null)
    try {
      const page = await library.listPrompts(scope, query, items.length)
      setItems(prev => {
        const seen = new Set(prev.map(p => p.id))
        return [...prev, ...page.prompts.filter(p => !seen.has(p.id))]
      })
      setTotal(page.total)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  // Debounced so typing a search does not fire a request per keystroke.
  useEffect(() => {
    const t = setTimeout(load, 250)
    return () => clearTimeout(t)
  }, [load])

  // Move focus into a panel once it is on screen (keyboard and screen-reader
  // users land on what they opened).
  useEffect(() => {
    if (focusPanel.current && panelRef.current) {
      focusPanel.current = false
      panelRef.current.focus()
    }
  }, [open, detail, items])

  if (!library) return null

  const act = async fn => {
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(e.message)
    }
  }

  const isOpen = (type, id) => open?.type === type && open.id === id

  const close = () => {
    const trigger = open ? triggers.current[`${open.type}:${open.id}`] : null
    setOpen(null)
    setDetail(null)
    load()
    if (trigger && trigger.isConnected) trigger.focus()
  }

  const openPanel = async (type, prompt) => {
    // The same button again closes its panel (it is a disclosure toggle).
    if (isOpen(type, prompt.id)) {
      close()
      return
    }
    focusPanel.current = true
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

  // The open panel, as a labelled region that takes focus and closes on Escape.
  const renderPanelFor = prompt => {
    const body = renderPanelBody(prompt)
    if (!body) return null
    return (
      <div id={panelDomId(prompt.id)} ref={panelRef} role="region" tabIndex={-1}
        aria-label={`${PANEL_LABELS[open.type] || 'Details for'} ${prompt.title}`}
        onKeyDown={e => {
          if (e.key === 'Escape') {
            e.stopPropagation()
            close()
          }
        }}
        className={`rounded-lg ${FOCUS_RING}`}>
        {body}
      </div>
    )
  }

  const renderPanelBody = prompt => {
    if (!open || open.id !== prompt.id || !detail) return null
    if (open.type === 'edit') {
      return (
        <PromptLibraryEditor
          prompt={detail}
          onCancel={close}
          onSave={async (fields, revision) => {
            const saved = await library.updatePrompt(prompt.id, fields, revision)
            setDetail(saved)
            return saved
          }}
          onPublish={async (change, revision) => {
            await library.publishPrompt(prompt.id, change, revision)
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
      className={`p-1.5 rounded bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors ${FOCUS_RING} ${extra}`}>
      <Icon className="w-4 h-4" aria-hidden="true" />
    </button>
  )

  // Edit / History / Share open a panel below the entry: a disclosure button
  // that reports whether its panel is open and which element it controls.
  const panelButton = (type, prompt, label, icon) => {
    const Icon = icon
    const expanded = isOpen(type, prompt.id)
    return (
      <button type="button" onClick={() => openPanel(type, prompt)} title={label} aria-label={label}
        aria-expanded={expanded}
        aria-controls={expanded ? panelDomId(prompt.id) : undefined}
        ref={el => { triggers.current[`${type}:${prompt.id}`] = el }}
        className={`p-1.5 rounded transition-colors ${expanded ? 'bg-blue-700 text-white' : 'bg-gray-600 hover:bg-gray-500 text-gray-200'} ${FOCUS_RING}`}>
        <Icon className="w-4 h-4" aria-hidden="true" />
      </button>
    )
  }

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
              className={`px-3 py-1.5 text-sm ${scope === f.value ? 'bg-blue-700 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'} ${FOCUS_RING}`}>
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

      {incomplete && (
        <div role="status" className="p-2 bg-gray-700 border border-gray-600 rounded-lg text-gray-300 text-xs">
          Some team prompts could not be checked right now and may be missing from this list.
        </div>
      )}
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
                      className={`px-2 py-1 rounded text-xs font-medium ${isActive ? 'bg-emerald-700 text-white' : 'bg-gray-600 hover:bg-gray-500 text-gray-200'} disabled:opacity-40 ${FOCUS_RING}`}>
                      {isActive ? 'Active' : 'Use'}
                    </button>
                    {p.can_edit && panelButton('edit', p, `Edit ${p.title}`, Pencil)}
                    {panelButton('versions', p, `Version history for ${p.title}`, History)}
                    {p.can_manage && panelButton('share', p, `Share ${p.title}`, Share2)}
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

      {items.length < total && (
        <div className="flex items-center justify-center gap-3">
          <span className="text-xs text-gray-400">Showing {items.length} of {total}</span>
          <button type="button" onClick={loadMore} disabled={loading}
            className="px-3 py-1.5 rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-200 text-sm disabled:opacity-50">
            Load more
          </button>
        </div>
      )}
    </section>
  )
}

export default PromptLibraryPanel
