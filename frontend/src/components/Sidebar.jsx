import { useState } from 'react'
import { useChat } from '../contexts/ChatContext'

const Sidebar = () => {
  const { clearChat } = useChat()
  const [isCollapsed, setIsCollapsed] = useState(false)

  if (isCollapsed) {
    return (
      <div className="w-12 bg-gray-800 border-r border-gray-700 p-2">
        <button
          onClick={() => setIsCollapsed(false)}
          className="w-full p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
        >
          &gt;
        </button>
      </div>
    )
  }

  return (
    <aside className="w-64 bg-gray-800 border-r border-gray-700 flex flex-col">
      <div className="p-4 border-b border-gray-700 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-100">Conversations</h2>
        <button
          onClick={() => setIsCollapsed(true)}
          className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
        >
          &lt;
        </button>
      </div>
      
      <div className="flex-1 p-4">
        <button
          onClick={clearChat}
          className="w-full px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors"
        >
          New Conversation
        </button>
      </div>
    </aside>
  )
}

export default Sidebar