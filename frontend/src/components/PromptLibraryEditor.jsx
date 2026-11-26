import { useRef, useState } from 'react'
import { Check, X, Eye, Pencil, Upload, FlaskConical } from 'lucide-react'

/**
 * Draft editor for a team-library entry (issue #1169).
 *
 * Saving changes only the draft; "Publish" snapshots the saved draft as a new
 * immutable version with an optional change description. "Test in chat"
 * selects the draft text for the next turns without publishing it -- the text
 * is the editor's own, sent inline like a private custom prompt, so nobody
 * else's selection is affected.
 *
 * `prompt` is null when creating. `onSave(fields, draftRevision)` must resolve
 * to the saved entry (or throw); `onPublish(changeDescription, draftRevision)`
 * and `onTest(fields)` are only offered for an existing entry.
 *
 * The editor keeps the draft revision it loaded. Saving names it, and
 * publishing names the revision of the text on screen, so the server refuses
 * (409) instead of overwriting someone else's save or publishing text this
 * editor never showed.
 */
const PromptLibraryEditor = ({ prompt = null, onSave, onPublish, onTest, onCancel, onRefresh }) => {
  const [title, setTitle] = useState(prompt?.title || '')
  const [description, setDescription] = useState(prompt?.description || '')
  const [content, setContent] = useState(prompt?.draft_content || '')
  const [revision, setRevision] = useState(prompt?.draft_revision ?? 1)
  // What was last saved, so "dirty" compares against the server's copy.
  const [saved, setSaved] = useState({
    title: prompt?.title || '',
    description: prompt?.description || '',
    content: prompt?.draft_content || '',
  })
  const savedRef = useRef(saved)
  const [changeDescription, setChangeDescription] = useState('')
  const [preview, setPreview] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [conflict, setConflict] = useState(null)

  const dirty = !prompt || title !== saved.title || (description || '') !== saved.description ||
    content !== saved.content
  const valid = title.trim().length > 0 && content.trim().length > 0
  const run = async fn => {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(e.message)
      if (e.status === 409 && onRefresh) {
        try {
          const latest = await onRefresh()
          setConflict({ ...latest, base: savedRef.current })
        } catch {
          setError(`${e.message} Could not refresh the draft. Try again.`)
        }
      }
    } finally {
      setBusy(false)
    }
  }

  // Saves the draft and returns the revision now on the server.
  const saveDraft = async () => {
    const result = await onSave({ title: title.trim(), description, content }, revision)
    if (result && typeof result.draft_revision === 'number') {
      setRevision(result.draft_revision)
      const confirmed = {
        title: result.title,
        description: result.description || '',
        content: result.draft_content,
      }
      savedRef.current = confirmed
      setSaved(confirmed)
      setTitle(confirmed.title)
      setDescription(confirmed.description)
      setContent(confirmed.content)
      return result.draft_revision
    }
    return revision
  }
  const save = () => run(saveDraft)
  const publish = () => run(async () => {
    const reviewed = dirty ? await saveDraft() : revision
    await onPublish(changeDescription, reviewed)
    setChangeDescription('')
  })

  const fieldId = prompt ? `lib-${prompt.id}` : 'lib-new'
  return (
    <div className="bg-gray-700 rounded-lg p-4 space-y-3 border border-gray-600">
      <div>
        <label htmlFor={`${fieldId}-title`} className="block text-xs text-gray-400 mb-1">Title</label>
        <input
          id={`${fieldId}-title`}
          type="text"
          value={title}
          onChange={e => setTitle(e.target.value)}
          disabled={busy || !!conflict}
          maxLength={200}
          className="w-full px-3 py-2 bg-gray-800 text-gray-50 rounded-lg border border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>
      <div>
        <label htmlFor={`${fieldId}-description`} className="block text-xs text-gray-400 mb-1">Description</label>
        <input
          id={`${fieldId}-description`}
          type="text"
          value={description}
          onChange={e => setDescription(e.target.value)}
          disabled={busy || !!conflict}
          maxLength={1000}
          placeholder="What this prompt is for"
          className="w-full px-3 py-2 bg-gray-800 text-gray-50 rounded-lg border border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>
      <div>
        <div className="flex items-center justify-between mb-1">
          <label htmlFor={`${fieldId}-content`} className="block text-xs text-gray-400">
            Draft system prompt {content ? `(${content.length} characters)` : ''}
          </label>
          <button
            type="button"
            onClick={() => setPreview(p => !p)}
            aria-pressed={preview}
            className="flex items-center gap-1 text-xs text-gray-300 hover:text-white"
          >
            {preview ? <Pencil className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
            {preview ? 'Edit' : 'Preview'}
          </button>
        </div>
        {preview ? (
          <pre className="w-full max-h-72 overflow-auto px-3 py-2 bg-gray-900 text-gray-200 rounded-lg border border-gray-600 text-sm whitespace-pre-wrap">
            {content || 'Nothing to preview yet.'}
          </pre>
        ) : (
          <textarea
            id={`${fieldId}-content`}
            value={content}
            onChange={e => setContent(e.target.value)}
            disabled={busy || !!conflict}
            rows={8}
            placeholder="System prompt text. Replaces the default system prompt when selected."
            className="w-full px-3 py-2 bg-gray-800 text-gray-50 rounded-lg border border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono text-sm resize-y"
          />
        )}
      </div>

      {prompt && (
        <div>
          <label htmlFor={`${fieldId}-change`} className="block text-xs text-gray-400 mb-1">
            Change description for the next published version
          </label>
          <input
            id={`${fieldId}-change`}
            type="text"
            value={changeDescription}
            onChange={e => setChangeDescription(e.target.value)}
            maxLength={1000}
            placeholder="e.g. Tightened the tone guidance"
            className="w-full px-3 py-2 bg-gray-800 text-gray-50 rounded-lg border border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      )}

      {error && (
        <div role="alert" className="p-2 bg-red-900/30 border border-red-700 rounded text-red-300 text-sm">{error}</div>
      )}

      {conflict && (
        <div className="p-3 border border-amber-500 rounded text-sm">
          <p>The draft changed on the server. Review the latest version before retrying.</p>
          <p>Current title: {conflict.title}</p>
          <p>Current description: {conflict.description}</p>
          <pre className="whitespace-pre-wrap max-h-48 overflow-auto">
            {conflict.draft_content}
          </pre>
          <button type="button" onClick={() => {
            setRevision(conflict.draft_revision)
            const latest = {
              title: conflict.title,
              description: conflict.description || '',
              content: conflict.draft_content,
            }
            const base = conflict.base
            setTitle(current => current.trim() === base.title ? latest.title : current)
            setDescription(current => current === base.description ? latest.description : current)
            setContent(current => current === base.content ? latest.content : current)
            savedRef.current = latest
            setSaved(latest)
            setConflict(null)
            setError(null)
          }}>Keep my edits and use latest revision</button>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="flex items-center gap-1 px-3 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 text-sm"
        >
          <X className="w-4 h-4" /> Close
        </button>
        {prompt && onTest && (
          <button
            type="button"
            disabled={!valid || busy || !!conflict}
            onClick={() => onTest({ title: title.trim(), content })}
            title="Use this draft for your next messages without publishing it"
            className="flex items-center gap-1 px-3 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 text-sm disabled:opacity-50"
          >
            <FlaskConical className="w-4 h-4" /> Test in chat
          </button>
        )}
        <button
          type="button"
          disabled={!valid || busy || !dirty || !!conflict}
          onClick={save}
          className="flex items-center gap-1 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium disabled:bg-gray-600 disabled:text-gray-400"
        >
          <Check className="w-4 h-4" /> {prompt ? 'Save draft' : 'Create private prompt'}
        </button>
        {prompt && onPublish && (
          <button
            type="button"
            disabled={!valid || busy || !!conflict}
            onClick={publish}
            className="flex items-center gap-1 px-3 py-2 rounded-lg bg-emerald-700 hover:bg-emerald-600 text-white text-sm font-medium disabled:opacity-50"
          >
            <Upload className="w-4 h-4" /> Publish version
          </button>
        )}
      </div>
    </div>
  )
}

export default PromptLibraryEditor
