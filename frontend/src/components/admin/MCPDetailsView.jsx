import React from 'react'
import hljs from 'highlight.js/lib/core'
import json from 'highlight.js/lib/languages/json'

hljs.registerLanguage('json', json)

const MCPDetailsView = ({ content }) => {
  const servers = content.servers || {}
  const status = content.status || {}
  const configPath = content.configPath || 'config/mcp.json'
  const connectedSet = new Set(status.connected_servers || [])
  const failedServers = status.failed_servers || {}
  const toolCounts = status.tool_counts || {}
  const promptCounts = status.prompt_counts || {}
  const serverNames = Object.keys(servers)
  const stripeColors = ['bg-gray-800/50', 'bg-gray-750/30']

  return (
    <div className="flex gap-4" style={{ height: 'calc(85vh - 140px)' }}>
      {/* Left panel: per-server config with syntax highlighting */}
      <div className="flex-1 overflow-y-auto rounded-lg border border-gray-600">
        <div className="px-3 py-2 bg-gray-700 border-b border-gray-600 sticky top-0 z-10">
          <span className="text-xs text-gray-400 font-mono">{configPath}</span>
        </div>
        {serverNames.length === 0 ? (
          <div className="p-4 text-gray-500 text-sm">No servers configured.</div>
        ) : (
          serverNames.map((name, idx) => {
            const highlighted = hljs.highlight(
              JSON.stringify(servers[name], null, 2),
              { language: 'json' }
            ).value
            return (
              <div key={name} className={`${stripeColors[idx % 2]} border-b border-gray-700 last:border-b-0`}>
                <div className="px-3 py-1.5 flex items-center gap-2 border-b border-gray-700/50">
                  <span className={`inline-block w-2 h-2 rounded-full ${
                    connectedSet.has(name) ? 'bg-green-400' :
                    failedServers[name] ? 'bg-red-400' : 'bg-gray-500'
                  }`} />
                  <span className="font-mono text-sm font-semibold text-gray-200">{name}</span>
                </div>
                <pre className="p-3 overflow-x-auto m-0 text-xs leading-relaxed">
                  <code className="hljs language-json" dangerouslySetInnerHTML={{ __html: highlighted }} />
                </pre>
              </div>
            )
          })
        )}
      </div>

      {/* Right panel: status per server */}
      <div className="w-80 flex-shrink-0 overflow-y-auto space-y-2">
        <div className="px-3 py-2 bg-gray-700 rounded-lg text-xs text-gray-400">
          {connectedSet.size} connected, {Object.keys(failedServers).length} failed
        </div>
        {serverNames.map((name) => {
          const isConnected = connectedSet.has(name)
          const failure = failedServers[name]
          const tools = toolCounts[name] || 0
          const prompts = promptCounts[name] || 0
          return (
            <div key={name} className={`p-3 rounded-lg border ${
              isConnected ? 'border-green-700/60 bg-green-900/10' :
              failure ? 'border-red-700/60 bg-red-900/10' :
              'border-gray-600 bg-gray-800/50'
            }`}>
              <div className="flex items-center justify-between mb-1">
                <span className="font-mono text-sm font-semibold text-gray-200">{name}</span>
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                  isConnected ? 'bg-green-900/60 text-green-300' :
                  failure ? 'bg-red-900/60 text-red-300' :
                  'bg-gray-700 text-gray-400'
                }`}>
                  {isConnected ? 'Connected' : failure ? 'Failed' : 'Unknown'}
                </span>
              </div>
              {isConnected && (
                <div className="text-xs text-gray-400 mt-1">
                  {tools} tool{tools !== 1 ? 's' : ''}, {prompts} prompt{prompts !== 1 ? 's' : ''}
                </div>
              )}
              {failure && (
                <div className="mt-2 space-y-1">
                  {failure.error && (
                    <div className="text-xs text-red-300 bg-red-900/30 px-2 py-1 rounded font-mono break-all">
                      {failure.error}
                    </div>
                  )}
                  {failure.attempt_count > 1 && (
                    <div className="text-xs text-gray-500">
                      {failure.attempt_count} attempts
                    </div>
                  )}
                </div>
              )}
              <div className="mt-1 text-xs text-gray-500">
                {servers[name]?.type || servers[name]?.transport || 'stdio'}
                {servers[name]?.url ? ` - ${servers[name].url}` : ''}
              </div>
            </div>
          )
        })}
        {/* Auto-reconnect info */}
        {status.auto_reconnect && (
          <div className="p-3 rounded-lg border border-gray-600 bg-gray-800/50 text-xs text-gray-400">
            <div className="font-medium text-gray-300 mb-1">Auto-reconnect</div>
            <div>{status.auto_reconnect.enabled ? 'Enabled' : 'Disabled'}</div>
            {status.auto_reconnect.enabled && (
              <div>Interval: {status.auto_reconnect.base_interval}-{status.auto_reconnect.max_interval}s</div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default MCPDetailsView
