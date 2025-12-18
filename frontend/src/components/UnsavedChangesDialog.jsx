import { AlertTriangle, Save, X } from 'lucide-react'

const UnsavedChangesDialog = ({ isOpen, onSave, onDiscard, onCancel }) => {
  if (!isOpen) return null

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[60]"
      onClick={(e) => {
        e.stopPropagation()
        onCancel?.()
      }}
    >
      <div
        className="bg-gray-800 rounded-lg shadow-xl max-w-md w-full mx-4 border border-gray-700"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-6 py-4 border-b border-gray-700">
          <div className="p-2 bg-orange-500/15 rounded-full border border-orange-500/20">
            <AlertTriangle className="w-5 h-5 text-orange-200" />
          </div>
          <h3 className="text-lg font-medium text-gray-100">Unsaved Changes</h3>
        </div>

        {/* Content */}
        <div className="px-6 py-4">
          <p className="text-gray-300/90 mb-4">
            You have unsaved changes to your tools and integrations. What would you like to do?
          </p>
        </div>

        {/* Actions */}
        <div className="grid grid-cols-3 gap-3 px-6 py-4 border-t border-gray-700">
          <button
            onClick={onCancel}
            className="w-full flex items-center justify-center px-4 py-2 rounded-lg bg-gray-700/60 hover:bg-gray-700 text-gray-200 transition-colors focus:outline-none focus:ring-2 focus:ring-gray-500 focus:ring-offset-2 focus:ring-offset-gray-800"
          >
            Cancel
          </button>
          <button
            onClick={onDiscard}
            className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg border border-red-500/30 bg-red-500/10 hover:bg-red-500/15 text-red-200 transition-colors focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 focus:ring-offset-gray-800"
          >
            <X className="w-4 h-4" />
            Discard Changes
          </button>
          <button
            onClick={onSave}
            className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-blue-600/80 hover:bg-blue-600 text-white transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-gray-800"
          >
            <Save className="w-4 h-4" />
            Save Changes
          </button>
        </div>
      </div>
    </div>
  )
}

export default UnsavedChangesDialog