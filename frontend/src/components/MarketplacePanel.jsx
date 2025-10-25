import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Check, X, Search } from 'lucide-react'
import { useState } from 'react'
import { useChat } from '../contexts/ChatContext'
import { useMarketplace } from '../contexts/MarketplaceContext'

const MarketplacePanel = () => {
  const [searchTerm, setSearchTerm] = useState('')
  const navigate = useNavigate()
  const { tools, prompts } = useChat()
  const {
    selectedServers,
    toggleServer,
    isServerSelected,
    deselectAllServers
  } = useMarketplace()

  // Combine tools and prompts into a unified server list
  const allServers = {}
  
  // Add tools to the unified list
  tools.forEach(toolServer => {
    if (!allServers[toolServer.server]) {
      allServers[toolServer.server] = {
        server: toolServer.server,
        description: toolServer.description,
        is_exclusive: toolServer.is_exclusive,
        author: toolServer.author,
        short_description: toolServer.short_description,
        help_email: toolServer.help_email,
        tools: toolServer.tools || [],
        tool_count: toolServer.tool_count || 0,
        prompts: [],
        prompt_count: 0
      }
    }
  })
  
  // Add prompts to the unified list
  prompts.forEach(promptServer => {
    if (!allServers[promptServer.server]) {
      allServers[promptServer.server] = {
        server: promptServer.server,
        description: promptServer.description,
        is_exclusive: false,
        author: promptServer.author,
        short_description: promptServer.short_description,
        help_email: promptServer.help_email,
        tools: [],
        tool_count: 0,
        prompts: promptServer.prompts || [],
        prompt_count: promptServer.prompt_count || 0
      }
    } else {
      allServers[promptServer.server].prompts = promptServer.prompts || []
      allServers[promptServer.server].prompt_count = promptServer.prompt_count || 0
    }
  })
  
  const serverList = Object.values(allServers)
  
  // Filter servers based on search term
  const filteredServers = serverList.filter(server => {
    if (!searchTerm) return true
    
    const searchLower = searchTerm.toLowerCase()
    
    // Search in server name, description, and short description
    if (server.server.toLowerCase().includes(searchLower) || 
        server.description.toLowerCase().includes(searchLower) ||
        (server.short_description && server.short_description.toLowerCase().includes(searchLower))) {
      return true
    }
    
    // Search in author
    if (server.author && server.author.toLowerCase().includes(searchLower)) {
      return true
    }
    
    // Search in tool names
    if (server.tools.some(tool => tool.toLowerCase().includes(searchLower))) {
      return true
    }
    
    // Search in prompt names and descriptions
    if (server.prompts.some(prompt => 
      prompt.name.toLowerCase().includes(searchLower) || 
      prompt.description.toLowerCase().includes(searchLower)
    )) {
      return true
    }
    
    return false
  })
  
  const selectedCount = selectedServers.size
  const totalCount = serverList.length

  return (
    <div className="h-screen bg-gray-900 text-gray-200 flex flex-col">
      {/* Header */}
      <div className="bg-gray-800 border-b border-gray-700 p-4 flex-shrink-0">
        <div className="w-full px-6 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <button
              onClick={() => {
                // Set a flag to auto-open tools panel when returning to chat
                sessionStorage.setItem('openToolsPanel', 'true')
                navigate('/')
              }}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium transition-colors"
            >
              <ArrowLeft className="w-5 h-5" />
              Back to Tools
            </button>
            <div>
              <h1 className="text-2xl font-bold text-gray-100">MCP Marketplace</h1>
              <p className="text-sm text-gray-400">
                Select which MCP servers to use in your chat interface
              </p>
            </div>
          </div>
          <div className="text-sm text-gray-400">
            {selectedCount} of {totalCount} servers selected
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        <div className="w-full px-6 py-6">
        {/* Controls */}
        <div className="space-y-4 mb-6">
          <div className="flex gap-4">
            <button
              onClick={deselectAllServers}
              className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-200 rounded-lg transition-colors"
            >
              Deselect All
            </button>
          </div>
          
          {/* Search Bar */}
          <div className="relative max-w-md">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4" />
            <input
              type="text"
              placeholder="Search marketplace..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full pl-10 pr-4 py-2 bg-gray-800 border border-gray-600 rounded-lg text-gray-200 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
        </div>

        {/* Server Grid */}
        {filteredServers.length === 0 && searchTerm ? (
          <div className="text-center py-12">
            <Search className="w-16 h-16 text-gray-500 mx-auto mb-4" />
            <h3 className="text-lg font-medium text-gray-300 mb-2">
              No results found
            </h3>
            <p className="text-gray-500">
              Try adjusting your search terms or browse all available servers.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {filteredServers.map((server) => {
            const isSelected = isServerSelected(server.server)
            
            return (
              <div
                key={server.server}
                className={`
                  relative p-4 rounded-lg border-2 transition-all cursor-pointer
                  ${isSelected 
                    ? 'border-blue-500 bg-blue-500/10' 
                    : 'border-gray-600 bg-gray-800 hover:border-gray-500'
                  }
                `}
                onClick={() => toggleServer(server.server)}
              >
                {/* Selection Indicator */}
                <div className={`
                  absolute top-3 right-3 w-5 h-5 rounded-full border-2 flex items-center justify-center transition-colors
                  ${isSelected 
                    ? 'border-blue-500 bg-blue-500' 
                    : 'border-gray-500'
                  }
                `}>
                  {isSelected && <Check className="w-3 h-3 text-white" />}
                </div>

                {/* Server Info */}
                <div className="mb-3">
                  <h3 className="text-base font-semibold text-white capitalize mb-1">
                    {server.server}
                  </h3>
                  
                  {/* Short Description */}
                  {server.short_description && (
                    <p className="text-xs text-blue-300 mb-1 font-medium">
                      {server.short_description}
                    </p>
                  )}
                  
                  <p className="text-xs text-gray-400 mb-2 line-clamp-2">
                    {server.description}
                  </p>
                  
                  {/* Author and Help Email */}
                  <div className="flex flex-wrap items-center gap-2 mb-2 text-xs">
                    {server.author && (
                      <span className="text-gray-300">
                        <span className="text-gray-500">By:</span> {server.author}
                      </span>
                    )}
                    {server.help_email && (
                      <a 
                        href={`mailto:${server.help_email}`}
                        className="text-blue-400 hover:text-blue-300 underline"
                        onClick={(e) => e.stopPropagation()}
                      >
                        Help
                      </a>
                    )}
                  </div>
                  
                  {/* Server Stats */}
                  <div className="flex items-center gap-2 text-xs text-gray-400">
                    {server.tool_count > 0 && <span>{server.tool_count} tools</span>}
                    {server.prompt_count > 0 && <span>{server.prompt_count} prompts</span>}
                    {server.is_exclusive && (
                      <span className="px-1 py-0.5 bg-orange-600 text-white rounded text-xs">
                        Exclusive
                      </span>
                    )}
                  </div>
                </div>

                {/* Tools and Prompts Preview */}
                <div className="flex flex-wrap gap-1">
                  {/* Tools */}
                  {server.tools.slice(0, 3).map((tool) => (
                    <span
                      key={tool}
                      className="px-1.5 py-0.5 bg-gray-700 text-xs rounded text-gray-300"
                    >
                      {tool}
                    </span>
                  ))}
                  
                  {/* Prompts */}
                  {server.prompts.slice(0, 3).map((prompt) => (
                    <span
                      key={prompt.name}
                      className="px-1.5 py-0.5 bg-purple-700 text-xs rounded text-gray-300"
                      title={prompt.description}
                    >
                      {prompt.name}
                    </span>
                  ))}
                  
                  {/* Show "more" indicator */}
                  {(server.tools.length + server.prompts.length) > 3 && (
                    <span className="px-1.5 py-0.5 bg-gray-700 text-xs rounded text-gray-300">
                      +{(server.tools.length + server.prompts.length) - 3} more
                    </span>
                  )}
                </div>
              </div>
            )
          })}
          </div>
        )}

        {serverList.length === 0 && (
          <div className="text-center py-12">
            <X className="w-16 h-16 text-gray-500 mx-auto mb-4" />
            <h3 className="text-lg font-medium text-gray-300 mb-2">
              No MCP Servers Available
            </h3>
            <p className="text-gray-500">
              No MCP servers are currently authorized for your account.
            </p>
          </div>
        )}

        {/* Footer */}
        <div className="mt-8 p-4 bg-gray-800 rounded-lg">
          <h4 className="font-medium text-gray-200 mb-2">How it works:</h4>
          <ul className="text-sm text-gray-400 space-y-1">
            <li>• Select the MCP servers you want to use in your chat interface</li>
            <li>• Only selected servers will appear in the Tools & Integrations panel</li>
            <li>• <span className="text-purple-400">Purple tags</span> indicate custom prompts, <span className="text-gray-300">gray tags</span> indicate tools</li>
            <li>• Your selections are saved in your browser</li>
            <li>• You can change your selection anytime by returning to this marketplace</li>
          </ul>
        </div>
        </div>
      </div>
    </div>
  )
}

export default MarketplacePanel