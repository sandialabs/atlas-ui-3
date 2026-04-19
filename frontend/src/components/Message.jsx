import DOMPurify from 'dompurify'
import 'katex/dist/katex.min.css'
import { preProcessLatex, restoreLatexPlaceholders } from '../utils/latexPreprocessor'
import { useChat } from '../contexts/ChatContext'
import { memo, useEffect, useId, useRef, useState } from 'react'
import { Copy } from 'lucide-react'
import { marked, DOMPURIFY_CONFIG } from '../utils/markdownRenderer'
import {
  extractSourceLabels,
  processCitationBadges,
  processReferencesSection,
} from '../utils/ragCitations'
import { processMessageContent } from '../utils/messageContent'
import { copyCodeBlock, copyMessageContent } from '../utils/clipboard'
import {
  filterArgumentsForDisplay,
  processToolResult,
  downloadReturnedFile,
} from '../utils/toolResultUtils'
import ToolApprovalMessage from './ToolApprovalMessage'
import ToolElapsedTime from './ToolElapsedTime'

// Feature flag: Perplexity-style inline citations & references for RAG.
// Off by default; enable at Vite build time with VITE_FEATURE_RAG_CITATIONS=true.
const ragCitationsEnabled =
  import.meta.env.VITE_FEATURE_RAG_CITATIONS === 'true'

const Message = ({ message }) => {
  const { appName, downloadFile, isSynthesizing, settings } = useChat()
  const debugMode = settings?.debugMode || false
  // Stable per-message scope for citation anchor IDs — prevents collisions
  // when multiple RAG responses exist in the same conversation.
  const rawId = useId()
  const messageScope = rawId.replace(/:/g, '')

  // State for collapsible sections with localStorage persistence
  // In debug mode, default to expanded
  const [toolInputCollapsed, setToolInputCollapsed] = useState(() => {
    if (debugMode) return false
    const saved = localStorage.getItem('toolInputCollapsed')
    return saved !== null ? JSON.parse(saved) : true
  })

  const [toolOutputCollapsed, setToolOutputCollapsed] = useState(() => {
    if (debugMode) return false
    const saved = localStorage.getItem('toolOutputCollapsed')
    return saved !== null ? JSON.parse(saved) : true
  })

  useEffect(() => {
    localStorage.setItem('toolInputCollapsed', JSON.stringify(toolInputCollapsed))
  }, [toolInputCollapsed])

  useEffect(() => {
    localStorage.setItem('toolOutputCollapsed', JSON.stringify(toolOutputCollapsed))
  }, [toolOutputCollapsed])

  const isUser = message.role === 'user'
  const isSystem = message.role === 'system'

  const handleCopyMessage = (event) => {
    event.preventDefault()
    copyMessageContent(message.content, event.currentTarget)
  }

  // Delegate code-block copy clicks on this message's own container rather
  // than `document`. A document-level listener per Message multiplies the
  // handler count by the number of messages in the chat, so one click fired
  // `copyCodeBlock` N times — wasteful even when idempotent.
  const containerRef = useRef(null)
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const handleCodeCopyClick = (event) => {
      const button = event.target.closest('[data-action="copy-code"]')
      if (!button || !container.contains(button)) return
      event.preventDefault()
      copyCodeBlock(button)
    }
    container.addEventListener('click', handleCodeCopyClick)
    return () => {
      container.removeEventListener('click', handleCodeCopyClick)
    }
  }, [])

  const avatarBg = isUser ? 'bg-green-600' : isSystem ? 'bg-yellow-600' : 'bg-blue-600'
  const avatarText = isUser ? 'Y' : isSystem ? 'S' : 'A'
  const authorName = isUser ? 'You' : isSystem ? 'System' : appName

  // Note: Tool auto-approval handled inside ToolApprovalMessage; we keep message visible so inline toggle remains accessible.

  const renderContent = () => {
    if (message.type === 'tool_approval_request') {
      return <ToolApprovalMessage message={message} />
    }

    // Handle tool call messages (both regular and agent mode use same UI)
    if (message.type === 'tool_call') {
      const isToolActive = message.status === 'calling' || message.status === 'in_progress'
      return (
        <div className="text-gray-200 selectable-markdown">
          <div className="flex items-center gap-2 mb-3">
            {isToolActive && (
              <svg className="w-4 h-4 spinner text-blue-400 flex-shrink-0" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
            )}
            <span className={`px-2 py-1 rounded text-xs font-medium ${
              isToolActive ? 'bg-blue-600' :
              message.status === 'completed' ? 'bg-green-600' : 'bg-red-600'
            }`}>
              {message.status === 'calling' ? 'CALLING' :
               message.status === 'in_progress' ? 'IN PROGRESS' :
               message.status === 'completed' ? 'SUCCESS' : 'FAILED'}
            </span>
            <span className="font-medium">{message.tool_name}</span>
            <span className="text-gray-400 text-sm">({message.server_name})</span>
            {isToolActive && <ToolElapsedTime timestamp={message.timestamp} />}
          </div>

          {/* Progress Section (shows when in progress or progress data available) */}
          {(() => {
            const hasProgressData = (
              message.status === 'in_progress' ||
              typeof message.progress === 'number' ||
              (message.progressRaw && (typeof message.progressRaw.progress === 'number' || typeof message.progressRaw.total === 'number'))
            )
            if (!hasProgressData) return null

            let percent = null
            let label = ''
            const raw = message.progressRaw || {}

            if (typeof raw.progress === 'number' && typeof raw.total === 'number' && raw.total > 0) {
              percent = Math.round(Math.min(100, Math.max(0, (raw.progress / raw.total) * 100)))
              label = `${raw.progress}/${raw.total}`
            } else if (typeof message.progress === 'number') {
              const p = message.progress <= 1 ? Math.round(message.progress * 100) : Math.round(message.progress)
              percent = Math.min(100, Math.max(0, p))
              label = `${percent}%`
            }

            if (percent === null) return null

            return (
              <div className="mb-4">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm text-gray-300">Progress</span>
                  <span className="text-xs text-gray-400">{label}</span>
                </div>
                <div className="w-full bg-gray-700 rounded h-2 overflow-hidden">
                  <div className="bg-blue-600 h-2" style={{ width: `${percent}%` }} />
                </div>
              </div>
            )
          })()}

          {/* Input Arguments Section */}
          {message.arguments && Object.keys(message.arguments).length > 0 && (
            <div className="mb-4">
              <div className="border-l-4 border-blue-500 pl-4">
                <button
                  onClick={() => setToolInputCollapsed(!toolInputCollapsed)}
                  className="w-full text-left text-sm font-semibold text-blue-400 mb-2 flex items-center gap-2 hover:text-blue-300 transition-colors"
                >
                  <span className={`transform transition-transform duration-200 ${toolInputCollapsed ? 'rotate-0' : 'rotate-90'}`}>
                    ▶
                  </span>
                  Input Arguments {toolInputCollapsed ? `(${Object.keys(message.arguments).length} params)` : ''}
                </button>
                {!toolInputCollapsed && (
                  <div className={`bg-gray-900 border border-gray-700 rounded-lg p-3 overflow-y-auto ${debugMode ? 'max-h-96' : 'max-h-64'}`}>
                    {debugMode && (
                      <div className="text-xs text-yellow-500 mb-1 font-semibold">DEBUG: Raw Arguments</div>
                    )}
                    <pre className="text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap">
                      {JSON.stringify(debugMode ? message.arguments : filterArgumentsForDisplay(message.arguments), null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Separator Line */}
          {message.arguments && Object.keys(message.arguments).length > 0 && message.result && (
            <div className="my-4">
              <hr className="border-gray-600" />
            </div>
          )}

          {/* Result Section */}
          {message.result && (
            <div className="mb-2">
              <div className={`border-l-4 pl-4 ${
                message.status === 'failed' ? 'border-red-500' : 'border-green-500'
              }`}>
                <button
                  onClick={() => setToolOutputCollapsed(!toolOutputCollapsed)}
                  className={`w-full text-left text-sm font-semibold mb-2 flex items-center gap-2 transition-colors ${
                    message.status === 'failed'
                      ? 'text-red-400 hover:text-red-300'
                      : 'text-green-400 hover:text-green-300'
                  }`}
                >
                  <span className={`transform transition-transform duration-200 ${toolOutputCollapsed ? 'rotate-0' : 'rotate-90'}`}>
                    ▶
                  </span>
                  {message.status === 'failed' ? 'Error Details' : 'Output Result'} {toolOutputCollapsed ? '(click to expand)' : ''}
                </button>

                {/* File download buttons - always visible even when output is collapsed */}
                {(() => {
                  let parsedResult = message.result
                  if (typeof message.result === 'string') {
                    try {
                      parsedResult = JSON.parse(message.result)
                    } catch {
                      parsedResult = message.result
                    }
                  }

                  // Check for meta_data.output_files (tool generated files)
                  const hasOutputFiles = parsedResult &&
                    typeof parsedResult === 'object' &&
                    parsedResult.meta_data &&
                    parsedResult.meta_data.output_files &&
                    Array.isArray(parsedResult.meta_data.output_files) &&
                    parsedResult.meta_data.output_files.length > 0

                  // Check for multiple files (legacy format)
                  const hasMultipleFiles = parsedResult &&
                    typeof parsedResult === 'object' &&
                    parsedResult.returned_files &&
                    Array.isArray(parsedResult.returned_files) &&
                    parsedResult.returned_file_names &&
                    parsedResult.returned_file_contents

                  // Check for single file (backward compatibility)
                  const hasSingleFile = parsedResult &&
                    typeof parsedResult === 'object' &&
                    parsedResult.returned_file_name &&
                    parsedResult.returned_file_base64

                  if (hasOutputFiles) {
                    return (
                      <div className="mb-3">
                        <div className="text-sm text-gray-300 mb-2">
                          {parsedResult.meta_data.output_files.length} file(s) available for download:
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {parsedResult.meta_data.output_files.map((filename, index) => (
                            <button
                              key={index}
                              onClick={() => downloadFile(filename)}
                              className="bg-blue-600 hover:bg-blue-700 text-white px-2 py-1 rounded text-sm flex items-center gap-1 transition-colors"
                              title="Download file"
                            >
                              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                              </svg>
                              {filename}
                            </button>
                          ))}
                        </div>
                      </div>
                    )
                  } else if (hasMultipleFiles) {
                    return (
                      <div className="mb-3">
                        <div className="text-sm text-gray-300 mb-2">
                          {parsedResult.returned_files.length} file(s) available for download:
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {parsedResult.returned_file_names.map((filename, index) => (
                            <button
                              key={index}
                              onClick={() => downloadReturnedFile(filename, parsedResult.returned_file_contents[index])}
                              className="bg-blue-600 hover:bg-blue-700 text-white px-2 py-1 rounded text-sm flex items-center gap-1 transition-colors"
                              title="Download file"
                            >
                              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                              </svg>
                              {filename}
                            </button>
                          ))}
                        </div>
                      </div>
                    )
                  } else if (hasSingleFile) {
                    return (
                      <div className="mb-3">
                        <button
                          onClick={() => downloadReturnedFile(parsedResult.returned_file_name, parsedResult.returned_file_base64)}
                          className="bg-blue-600 hover:bg-blue-700 text-white px-2 py-1 rounded text-sm flex items-center gap-1 transition-colors"
                          title="Download file"
                        >
                          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                          </svg>
                          {parsedResult.returned_file_name}
                        </button>
                      </div>
                    )
                  }

                  return null
                })()}

                {/* Output content - collapsible */}
                {!toolOutputCollapsed && (
                  <div className={`bg-gray-900 border border-gray-700 rounded-lg p-3 overflow-y-auto ${debugMode ? 'max-h-96' : 'max-h-64'}`}>
                    {debugMode && (
                      <div className="text-xs text-yellow-500 mb-1 font-semibold">DEBUG: Raw Output</div>
                    )}
                    <pre className="text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap">
                      {(() => {
                        if (debugMode) {
                          return typeof message.result === 'string' ? message.result : JSON.stringify(message.result, null, 2)
                        }
                        const processedResult = processToolResult(message.result)
                        return typeof processedResult === 'string' ? processedResult : JSON.stringify(processedResult, null, 2)
                      })()}
                    </pre>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Synthesis indicator - shown on completed tool messages while LLM interprets results */}
          {message.status === 'completed' && isSynthesizing && (
            <div className="mt-3 flex items-center gap-2 text-blue-400 border-t border-gray-700 pt-3">
              <svg className="w-4 h-4 spinner flex-shrink-0" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              <span className="text-sm font-medium">Interpreting results...</span>
            </div>
          )}
        </div>
      )
    }

    if (isUser || isSystem) {
      if (message.type === 'agent_status') {
        return (
          <div className="flex items-center gap-2 text-sm">
            <span className="px-2 py-0.5 rounded text-xs font-medium bg-purple-600 text-white uppercase flex-shrink-0">
              Agent
            </span>
            <span className="text-purple-300">{message.content}</span>
          </div>
        )
      }

      if (message.type === 'agent_reason' || message.type === 'agent_observe') {
        return (
          <div className="text-sm text-gray-400 italic border-l-2 border-purple-500 pl-3">
            {message.content}
          </div>
        )
      }

      if (message.type === 'tool_log') {
        const logLevel = message.log_level || message.subtype || 'info'
        let badgeColor
        let textColor

        switch (logLevel.toLowerCase()) {
          case 'error':
          case 'critical':
          case 'emergency':
            badgeColor = 'bg-red-500 text-white'
            textColor = 'text-red-300'
            break
          case 'warning':
          case 'warn':
            badgeColor = 'bg-yellow-500 text-black'
            textColor = 'text-yellow-300'
            break
          case 'alert':
            badgeColor = 'bg-orange-500 text-white'
            textColor = 'text-orange-300'
            break
          case 'info':
          case 'notice':
            badgeColor = 'bg-blue-500 text-white'
            textColor = 'text-blue-300'
            break
          case 'debug':
            badgeColor = 'bg-gray-500 text-gray-50'
            textColor = 'text-gray-400'
            break
          default:
            badgeColor = 'bg-blue-500 text-white'
            textColor = 'text-gray-200'
        }

        return (
          <div className="flex items-start gap-2">
            <span className={`px-2 py-0.5 rounded text-xs font-medium ${badgeColor} uppercase flex-shrink-0`}>
              {logLevel}
            </span>
            <div className={`${textColor} text-sm font-mono`}>
              {message.content}
            </div>
          </div>
        )
      }

      if (message.type === 'system' && message.subtype) {
        switch (message.subtype) {
          case 'file-attaching':
            return (
              <div className="text-blue-300 italic">
                {message.text}
              </div>
            )
          case 'file-attached':
            return (
              <div className="text-green-300">
                {message.text}
              </div>
            )
          case 'file-attach-error':
            return (
              <div className="text-red-300">
                {message.text}
              </div>
            )
          default:
            return <div className="text-gray-200 whitespace-pre-wrap">{message.content}</div>
        }
      }
      return <div className="text-gray-200 whitespace-pre-wrap">{message.content}</div>
    }

    // Render markdown for assistant messages
    const content = processMessageContent(message.content)

    try {
      const { result: latexProcessed, placeholders } = preProcessLatex(content)
      const markdownHtml = marked.parse(latexProcessed)
      const latexRestoredHtml = restoreLatexPlaceholders(markdownHtml, placeholders)
      // Skip citation pipeline for non-RAG messages (no References section)
      // or when the RAG citations feature flag is disabled.
      const hasReferences = ragCitationsEnabled && latexRestoredHtml.includes('References')
      const sourceLabels = hasReferences ? extractSourceLabels(latexRestoredHtml) : new Map()
      const citationHtml = hasReferences ? processCitationBadges(latexRestoredHtml, messageScope) : latexRestoredHtml
      const referencesHtml = hasReferences ? processReferencesSection(citationHtml, messageScope, sourceLabels) : citationHtml
      const sanitizedHtml = DOMPurify.sanitize(referencesHtml, DOMPURIFY_CONFIG)

      return (
        <div
          className="prose prose-invert max-w-none selectable-markdown"
          dangerouslySetInnerHTML={{ __html: sanitizedHtml }}
          onClick={(e) => {
            // Citation badge clicks: scroll the referenced entry into view
            // within the chat container instead of using browser fragment nav.
            const badge = e.target.closest('[data-citation-target]')
            if (!badge) return
            e.preventDefault()
            const targetId = badge.getAttribute('data-citation-target')
            const target = document.getElementById(targetId)
            if (target) {
              target.scrollIntoView({ behavior: 'smooth', block: 'center' })
              target.classList.add('rag-ref-highlight')
              setTimeout(() => target.classList.remove('rag-ref-highlight'), 2000)
            }
          }}
        />
      )
    } catch (error) {
      console.error('Error parsing markdown content:', error)
      return (
        <div className="text-gray-200">
          <pre className="whitespace-pre-wrap">{content}</pre>
        </div>
      )
    }
  }

  return (
    <div className={`flex items-start gap-3 ${isUser ? 'flex-row-reverse' : 'w-full'} group`}>
      <div className={`w-8 h-8 rounded-full ${avatarBg} flex items-center justify-center text-white text-sm font-medium flex-shrink-0`}>
        {avatarText}
      </div>

      <div ref={containerRef} className={`${isUser ? 'max-w-[70%] user-message-bubble' : 'w-full bg-gray-800'} rounded-lg p-4`}>
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <div className="text-sm font-medium text-gray-300">
              {authorName}
            </div>
            {/* Agent mode indicator for system messages */}
            {isSystem && message.agent_mode && (
              <span className="px-2 py-1 rounded text-xs font-medium bg-purple-600 text-white">
                AGENT
              </span>
            )}
          </div>
          {!isSystem && (
            <button
              onClick={handleCopyMessage}
              className="copy-message-button opacity-0 group-hover:opacity-100 bg-gray-700 hover:bg-gray-600 border border-gray-600 text-gray-200 p-1.5 rounded text-xs transition-all duration-200 cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500 ml-2"
              title="Copy message to clipboard"
              type="button"
            >
              <Copy className="w-3 h-3" />
            </button>
          )}
        </div>
        {renderContent()}
        {message._streaming && (
          <span className="inline-block w-2 h-4 bg-blue-400 animate-pulse ml-0.5 align-text-bottom" aria-label="Generating response..." />
        )}
      </div>
    </div>
  )
}

export default memo(Message)
