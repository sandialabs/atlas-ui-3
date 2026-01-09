import { AlertTriangle, Save, X } from 'lucide-react'

const UnsavedChangesDialog = ({ isOpen, onSave, onDiscard, onCancel }) => {
  if (!isOpen) return null

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-[60]"
      onClick={(e) => {
        e.stopPropagation()
        onCancel?.()
      }}
    >
      <div
        className="bg-gray-800 rounded-lg shadow-xl max-w-md w-full mx-4 border border-gray-600"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-6 py-4 border-b border-gray-700">
          <div className="p-2 bg-orange-600 rounded-full">
            <AlertTriangle className="w-5 h-5 text-white" />
          </div>
          <h3 className="text-lg font-semibold text-white">Unsaved Changes</h3>
        </div>

        {/* Content */}
        <div className="px-6 py-4">
          <p className="text-gray-300 mb-4">
            You have unsaved changes to your tools and integrations. What would you like to do?
          </p>
        </div>

        {/* Actions */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-700">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors font-medium"
          >
            Cancel
          </button>
          <button
            onClick={onDiscard}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-red-600 hover:bg-red-700 text-white transition-colors font-medium"
          >
            <X className="w-4 h-4" />
            Discard Changes
          </button>
          <button
            onClick={onSave}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors font-medium"
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