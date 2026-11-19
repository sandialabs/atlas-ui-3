import { useEffect, useState } from 'react'
import { Plus, Trash2, X, Check } from 'lucide-react'

/**
 * Sharing editor for a team-library entry (issue #1169). Shown only to users
 * with manage permission; the server enforces every rule regardless:
 *
 * - groups grant view/use only, and you can only add a group you belong to
 *   (the offered list is exactly those groups);
 * - named users may get use, edit, or manage -- manage only from the owner.
 */
const PERMISSIONS = [
  { value: 'use', label: 'Use' },
  { value: 'edit', label: 'Edit & publish' },
  { value: 'manage', label: 'Manage sharing' },
]

const PromptLibrarySharing = ({ prompt, library, onClose }) => {
  const [groups, setGroups] = useState([])
  const [users, setUsers] = useState([])
  // The sharing revision these grants were loaded at; a save names it, so a
  // change someone else made meanwhile is refused (409), never overwritten.
  const [revision, setRevision] = useState(null)
  const [available, setAvailable] = useState([])
  const [newUser, setNewUser] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [groupsIncomplete, setGroupsIncomplete] = useState(false)

  // Depend on the stable request functions, not the library object (which
  // changes whenever its state does): a refetch here would replace the
  // selections being edited with the stored ones.
  const { getSharing, listShareableGroups, setSharing } = library
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([getSharing(prompt.id), listShareableGroups()])
      .then(([sharing, shareable]) => {
        if (cancelled) return
        setGroups(sharing.groups || [])
        setUsers(sharing.users || [])
        setRevision(sharing.revision ?? 0)
        // Older servers return a bare list.
        setAvailable(Array.isArray(shareable) ? shareable : (shareable?.groups || []))
        setGroupsIncomplete(!Array.isArray(shareable) && shareable?.incomplete === true)
      })
      .catch(e => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [prompt.id, getSharing, listShareableGroups])

  // Already-shared groups stay listed even if you are not a member of them.
  const groupOptions = Array.from(new Set([...available, ...groups])).sort()
  const toggleGroup = g => setGroups(prev => (prev.includes(g) ? prev.filter(x => x !== g) : [...prev, g]))

  const addUser = () => {
    const email = newUser.trim().toLowerCase()
    if (!email || users.some(u => u.email === email)) return
    setUsers(prev => [...prev, { email, permission: 'use' }])
    setNewUser('')
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await setSharing(prompt.id, { groups, users, expected_revision: revision })
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="text-sm text-gray-400 p-3">Loading sharing...</div>

  return (
    <div className="bg-gray-700 rounded-lg p-4 space-y-4 border border-gray-600">
      <fieldset>
        <legend className="text-sm font-medium text-gray-100">Share with groups (view and use)</legend>
        {groupOptions.length === 0 ? (
          <p className="text-xs text-gray-400 mt-1">You are not a member of any group that can be shared with.</p>
        ) : (
          <div className="flex flex-wrap gap-2 mt-2">
            {groupOptions.map(g => (
              <label key={g} className="flex items-center gap-1 text-sm text-gray-200 bg-gray-800 px-2 py-1 rounded">
                <input type="checkbox" checked={groups.includes(g)} onChange={() => toggleGroup(g)} />
                {g}
              </label>
            ))}
          </div>
        )}
        {groupsIncomplete && (
          <p role="status" className="text-xs text-amber-300 mt-2">
            Some of your groups could not be checked right now, so they are not listed. Try again later to share with them.
          </p>
        )}
      </fieldset>

      <fieldset>
        <legend className="text-sm font-medium text-gray-100">Named people</legend>
        <div className="space-y-2 mt-2">
          {users.map(u => (
            <div key={u.email} className="flex items-center gap-2">
              <span className="flex-1 min-w-0 truncate text-sm text-gray-200">{u.email}</span>
              <select
                aria-label={`Permission for ${u.email}`}
                value={u.permission}
                disabled={u.permission === 'manage' && !prompt.is_owner}
                onChange={e => setUsers(prev => prev.map(x => (x.email === u.email ? { ...x, permission: e.target.value } : x)))}
                className="bg-gray-800 text-gray-100 text-sm rounded px-2 py-1 border border-gray-600"
              >
                {PERMISSIONS.filter(p => prompt.is_owner || p.value !== 'manage' || u.permission === 'manage').map(p => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
              <button
                type="button"
                aria-label={`Remove ${u.email}`}
                disabled={u.permission === 'manage' && !prompt.is_owner}
                onClick={() => setUsers(prev => prev.filter(x => x.email !== u.email))}
                className="p-1.5 rounded bg-gray-600 hover:bg-red-600 text-gray-200 disabled:opacity-40"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))}
          <div className="flex items-center gap-2">
            <input
              type="email"
              aria-label="Add a person by email"
              value={newUser}
              onChange={e => setNewUser(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addUser() } }}
              placeholder="person@example.com"
              className="flex-1 px-3 py-1.5 bg-gray-800 text-gray-50 rounded border border-gray-600 text-sm"
            />
            <button type="button" onClick={addUser} className="flex items-center gap-1 px-2 py-1.5 rounded bg-gray-600 hover:bg-gray-500 text-gray-100 text-sm">
              <Plus className="w-4 h-4" /> Add
            </button>
          </div>
        </div>
      </fieldset>

      {error && <div role="alert" className="p-2 bg-red-900/30 border border-red-700 rounded text-red-300 text-sm">{error}</div>}

      <div className="flex justify-end gap-2">
        <button type="button" onClick={onClose} className="flex items-center gap-1 px-3 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 text-sm">
          <X className="w-4 h-4" /> Cancel
        </button>
        <button type="button" disabled={saving} onClick={save} className="flex items-center gap-1 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium disabled:opacity-50">
          <Check className="w-4 h-4" /> Save sharing
        </button>
      </div>
    </div>
  )
}

export default PromptLibrarySharing
