import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ExternalLink } from 'lucide-react'
import AdminModal from '../AdminModal'
import Notifications from '../Notifications'
import BannerMessagesCard from './BannerMessagesCard'
import MCPConfigurationCard from './MCPConfigurationCard'
import FeedbackViewerCard from './FeedbackViewerCard'
import useAdminConfigActions from '../../hooks/useAdminConfigActions'

/**
 * The most-used admin controls, embedded as the "Admin" tab of the combined
 * Tools and Settings panel (issue #836).
 *
 * These are the same fully-featured cards the admin dashboard renders -- MCP
 * configuration and controls, banner messages, and feedback -- not read-only
 * summaries. Everything else still lives on the full dashboard, linked below.
 */
const AdminQuickPanel = ({ isOpen, onNavigate }) => {
  const navigate = useNavigate()
  const {
    notifications,
    addNotification,
    removeNotification,
    systemStatus,
    loadSystemStatus,
    modalOpen,
    modalData,
    openModal,
    closeModal,
    saveConfig,
    downloadLogs,
  } = useAdminConfigActions()

  useEffect(() => {
    if (isOpen) loadSystemStatus()
  }, [isOpen, loadSystemStatus])

  const goToDashboard = () => {
    onNavigate?.()
    navigate('/admin')
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-gray-50 font-medium">Admin Quick Controls</h3>
          <p className="text-sm text-gray-400 mt-1">
            The most-used admin tools. Logs, telemetry, config viewer, and the rest
            live on the full dashboard.
          </p>
        </div>
        <button
          onClick={goToDashboard}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors text-sm font-medium flex-shrink-0"
        >
          <ExternalLink className="w-4 h-4" />
          Full Admin Page
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* The cards paint bg-gray-800, same as the panel, so ring them to keep
            each one readable as a distinct card inside the modal. */}
        <div className="rounded-lg ring-1 ring-gray-700">
          <MCPConfigurationCard
            openModal={openModal}
            addNotification={addNotification}
            systemStatus={systemStatus}
          />
        </div>
        <div className="rounded-lg ring-1 ring-gray-700">
          <BannerMessagesCard
            openModal={openModal}
            addNotification={addNotification}
          />
        </div>
        <div className="rounded-lg ring-1 ring-gray-700">
          <FeedbackViewerCard
            openModal={openModal}
            addNotification={addNotification}
          />
        </div>
      </div>

      {modalOpen && (
        <AdminModal
          data={modalData}
          onClose={closeModal}
          onSave={saveConfig}
          onDownload={downloadLogs}
          addNotification={addNotification}
        />
      )}

      <Notifications notifications={notifications} removeNotification={removeNotification} />
    </div>
  )
}

export default AdminQuickPanel
