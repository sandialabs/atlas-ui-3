import { useEffect, useState } from 'react'
import { Trash2, RefreshCw } from 'lucide-react'
import { useCaptureConsent } from '../hooks/useCaptureConsent'

/**
 * CaptureConsentSection ("Help improve Atlas")
 *
 * Settings section for the opt-in fine-tune capture feature (issue #622).
 * Rendered only when the `finetune_capture` feature flag is on (the parent
 * SettingsPanel gates this). Consent is backend-stored, so this component
 * fetches it on open and POSTs toggle changes; it also offers a destructive
 * "delete my captured data" action behind a confirm.
 *
 * Props:
 *   isOpen - whether the settings panel is open (drives the initial fetch).
 */
const CaptureConsentSection = ({ isOpen }) => {
  const {
    consent,
    loading,
    saving,
    error,
    fetchConsent,
    setEnabled,
    deleteMyData,
    systemEnabled,
    userEnabled,
  } = useCaptureConsent()

  const [deleting, setDeleting] = useState(false)
  const [deleteResult, setDeleteResult] = useState(null)

  // Fetch consent state when the panel opens.
  useEffect(() => {
    if (isOpen) fetchConsent()
  }, [isOpen, fetchConsent])

  const handleToggle = () => {
    if (saving || !systemEnabled) return
    setEnabled(!userEnabled)
  }

  const handleDelete = async () => {
    const confirmed = window.confirm(
      'Permanently delete all of your captured chat data? This cannot be undone.'
    )
    if (!confirmed) return
    setDeleting(true)
    setDeleteResult(null)
    try {
      const result = await deleteMyData()
      setDeleteResult(result)
    } catch {
      // error state is set by the hook; nothing to add here.
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="bg-gray-700 rounded-lg p-4">
      <div className="flex items-center justify-between mb-2">
        <label className="text-gray-50 font-medium">Help improve Atlas</label>
        {systemEnabled ? (
          <button
            onClick={handleToggle}
            disabled={saving || loading}
            aria-pressed={userEnabled}
            aria-label="Toggle fine-tune data capture"
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors disabled:opacity-50 ${
              userEnabled ? 'bg-green-600' : 'bg-gray-600'
            }`}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                userEnabled ? 'translate-x-6' : 'translate-x-1'
              }`}
            />
          </button>
        ) : (
          <span className="text-xs text-gray-400">Disabled by administrator</span>
        )}
      </div>

      <p className="text-sm text-gray-400">
        When enabled, the full content of your chats &mdash; your prompts, the
        model&apos;s responses, and any tool calls and their results &mdash; is
        recorded to help fine-tune a local model. This is off by default and you
        can turn it off at any time.
      </p>

      {loading && (
        <p className="text-sm text-gray-500 mt-2 flex items-center gap-2">
          <RefreshCw className="w-3 h-3 animate-spin" />
          Loading your preference...
        </p>
      )}

      {consent?.needs_reconsent && systemEnabled && userEnabled && (
        <p className="text-sm text-yellow-300 mt-2">
          The data collection terms have changed. Re-confirm by toggling this off
          and on again.
        </p>
      )}

      {error && (
        <div className="p-3 bg-red-900/30 border border-red-700 rounded-lg text-red-300 text-sm mt-3">
          {error}
        </div>
      )}

      <div className="mt-3 flex items-center gap-3 flex-wrap">
        <button
          onClick={handleDelete}
          disabled={deleting}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-600/80 hover:bg-red-600 text-white transition-colors text-sm font-medium disabled:opacity-50"
        >
          {deleting ? (
            <RefreshCw className="w-4 h-4 animate-spin" />
          ) : (
            <Trash2 className="w-4 h-4" />
          )}
          Delete my captured data
        </button>
        {deleteResult && (
          <span className="text-sm text-green-400">
            Deleted {deleteResult.deleted_records} record(s) across{' '}
            {deleteResult.files_touched} file(s).
          </span>
        )}
      </div>
    </div>
  )
}

export default CaptureConsentSection
