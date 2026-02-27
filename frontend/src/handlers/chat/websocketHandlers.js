// Handlers extracted from original ChatContext to keep provider lean

// Default sandbox permissions for iframes (restrictive by default)
const DEFAULT_IFRAME_SANDBOX = 'allow-scripts allow-same-origin';

// Module-level token stream state.  The handler closure is recreated by
// useMemo whenever React dependencies change (e.g. setIsThinking triggers a
// re-render).  Keeping the buffer / timer here ensures a stale timer from a
// previous handler incarnation won't create an orphaned streaming message.
let _tokenBuffer = ''
let _tokenFlushTimer = null
let _streamActive = false
const FLUSH_INTERVAL_MS = 30

/**
 * Clean up module-level streaming state. Call this when the WebSocket handler
 * is torn down (e.g. on component unmount) to prevent leaked timers.
 */
export function cleanupStreamState() {
  _streamActive = false
  _tokenBuffer = ''
  if (_tokenFlushTimer) {
    clearTimeout(_tokenFlushTimer)
    _tokenFlushTimer = null
  }
}

/**
 * Create a WebSocket message handler for chat events.
 *
 * @param {Object} deps - Handler dependencies injected from ChatContext.
 * @param {Function} deps.addMessage - Add a message to the messages list.
 * @param {Function} deps.mapMessages - Transform the messages list via a mapper function.
 * @param {Function} deps.setIsThinking - Set the "thinking" indicator state.
 * @param {Function} deps.setCurrentAgentStep - Set the current agent step number.
 * @param {Function} [deps.setAgentPendingQuestion] - Set pending agent question.
 * @param {Function} [deps.setCanvasContent] - Set canvas HTML/markdown content.
 * @param {Function} [deps.setCanvasFiles] - Set canvas file list.
 * @param {Function} [deps.setCurrentCanvasFileIndex] - Set active canvas file index.
 * @param {Function} [deps.setCustomUIContent] - Set custom UI injection content.
 * @param {Function} [deps.setIsCanvasOpen] - Toggle canvas panel visibility.
 * @param {Function} [deps.setSessionFiles] - Set session file metadata.
 * @param {Function} [deps.getFileType] - Get file type from filename extension.
 * @param {Function} [deps.triggerFileDownload] - Trigger browser file download.
 * @param {Function} [deps.addAttachment] - Add a file attachment to the session.
 * @param {Function} [deps.resolvePendingFileEvent] - Resolve a pending file event.
 * @param {Function} [deps.setPendingElicitation] - Set pending elicitation request.
 * @param {Function} [deps.setIsSynthesizing] - Set the "synthesizing" indicator state.
 * @param {Function} [deps.setActiveConversationId] - Set the active conversation ID for chat history tracking.
 * @param {Function} deps.streamToken - Dispatch a STREAM_TOKEN action with a text chunk.
 * @param {Function} deps.streamEnd - Dispatch a STREAM_END action to finalize streaming.
 * @returns {Function} A handler function that processes incoming WebSocket messages.
 */
export function createWebSocketHandler(deps) {
  const {
    addMessage,
    mapMessages,
    setIsThinking,
    setCurrentAgentStep,
  // Optional setters for extra UI state
  setAgentPendingQuestion,
    setCanvasContent,
    setCanvasFiles,
    setCurrentCanvasFileIndex,
    setCustomUIContent,
    setIsCanvasOpen,
    setSessionFiles,
    getFileType,
    triggerFileDownload,
    addAttachment,
    resolvePendingFileEvent,
    setPendingElicitation,
    setIsSynthesizing,
    setActiveConversationId,
    streamToken,
    streamEnd,
  } = deps

  function flushTokenBuffer() {
    if (_tokenBuffer && typeof streamToken === 'function') {
      streamToken(_tokenBuffer)
      _tokenBuffer = ''
    }
    _tokenFlushTimer = null
  }

  function endTokenStream() {
    _streamActive = false
    if (_tokenFlushTimer) {
      clearTimeout(_tokenFlushTimer)
      _tokenFlushTimer = null
    }
    flushTokenBuffer()
    if (typeof streamEnd === 'function') streamEnd()
  }

  const handleAgentUpdate = (data) => {
    try {
      const kind = data.update_type || data.type
      switch (kind) {
        case 'agent_start':
          addMessage({ role: 'system', content: `Agent Mode Started (strategy: ${data.strategy ?? 'unknown'}, max steps: ${data.max_steps ?? '?'})`, type: 'agent_status', timestamp: new Date().toISOString(), agent_mode: true })
          break
        case 'agent_turn_start': {
          const step = data.step || data.turn || 1
          setCurrentAgentStep(step)
          break
        }
        case 'agent_reason': {
          if (data.message) {
            addMessage({ role: 'system', content: `Agent thinking (Reason):\n\n${data.message}`, type: 'agent_reason', agent_mode: true, timestamp: new Date().toISOString() })
          }
          break
        }
        case 'agent_observe': {
          if (data.message) {
            addMessage({ role: 'system', content: `Observation (Observe):\n\n${data.message}`, type: 'agent_observe', agent_mode: true, timestamp: new Date().toISOString() })
          }
          break
        }
        case 'agent_request_input': {
          const q = data.question || 'The agent requests additional input.'
          addMessage({ role: 'system', content: `Agent needs your input:\n\n${q}`, type: 'agent_request_input', agent_mode: true, timestamp: new Date().toISOString() })
          if (typeof setAgentPendingQuestion === 'function') setAgentPendingQuestion(q)
          break
        }
        case 'agent_tool_call':
          addMessage({
            role: 'system',
            content: `**Agent Tool Call**: ${data.function_name}`,
            type: 'tool_call',
            tool_call_id: `agent_${data.step}_${data.tool_index}`,
            tool_name: data.function_name,
            server_name: 'agent_step',
            arguments: data.arguments,
            step: data.step,
            status: 'calling',
            timestamp: new Date().toISOString(),
            agent_mode: true
          })
          break
        case 'agent_completion':
          setCurrentAgentStep(0)
          setIsThinking(false)
          if (typeof setIsSynthesizing === 'function') setIsSynthesizing(false)
          if (typeof setAgentPendingQuestion === 'function') setAgentPendingQuestion(null)
          addMessage({ role: 'system', content: `Agent Completed in ${data.steps ?? '?'} step(s)`, type: 'agent_status', timestamp: new Date().toISOString(), agent_mode: true })
          break
        case 'agent_error':
          addMessage({ role: 'system', content: `Agent Error (Step ${data.turn}): ${data.message}`, type: 'agent_error', timestamp: new Date().toISOString() })
          setIsThinking(false)
          if (typeof setIsSynthesizing === 'function') setIsSynthesizing(false)
          setCurrentAgentStep(0)
          break
        case 'agent_max_steps':
          addMessage({ role: 'system', content: `Agent Max Steps Reached - ${data.message}`, type: 'agent_status', timestamp: new Date().toISOString() })
          setIsThinking(false)
          if (typeof setIsSynthesizing === 'function') setIsSynthesizing(false)
          setCurrentAgentStep(0)
          break
        default:
          // ignore unknown
          break
      }
    } catch (err) {
      console.error('Error handling agent update', err, data)
    }
  }

  const handleIntermediateUpdate = (data) => {
    try {
      const updateType = data.update_type
      const updateData = data.data
      switch (updateType) {
        case 'tool_call':
          addMessage({
            role: 'system',
            content: `**Tool Call: ${updateData.tool_name}** (${updateData.server_name})`,
            type: 'tool_call',
            tool_call_id: updateData.tool_call_id,
            tool_name: updateData.tool_name,
            server_name: updateData.server_name,
            arguments: updateData.parameters || updateData.arguments || {},
            status: 'calling',
            timestamp: new Date().toISOString(),
            agent_mode: updateData.agent_mode || false
          })
          break
        case 'tool_result':
          mapMessages(prev => prev.map(msg => msg.tool_call_id && msg.tool_call_id === updateData.tool_call_id ? { ...msg, content: `**Tool: ${updateData.tool_name}** - ${updateData.success ? 'Success' : 'Failed'}`, status: updateData.success ? 'completed' : 'failed', result: updateData.result || updateData.error || null } : msg))
          break
        case 'tool_log':
          // Log message from MCP server
          if (updateData && updateData.message) {
            const logLevel = updateData.level || 'info'
            const serverName = updateData.server_name || 'unknown'
            const toolName = updateData.tool_name
            
            // Create prefix based on context
            let prefix = `[${serverName}]`
            if (toolName) {
              prefix = `[${serverName}:${toolName}]`
            }
            
            addMessage({
              role: 'system',
              content: `${prefix} ${updateData.message}`,
              type: 'tool_log',
              subtype: logLevel,
              log_level: logLevel,
              server_name: serverName,
              tool_name: toolName,
              tool_call_id: updateData.tool_call_id,
              timestamp: new Date().toISOString()
            })
          }
          break
        case 'system_message':
          // Rich system message from MCP server during tool execution
          if (updateData && updateData.message) {
            addMessage({
              role: 'system',
              content: updateData.message,
              type: 'system',
              subtype: updateData.subtype || 'info',
              tool_call_id: updateData.tool_call_id,
              tool_name: updateData.tool_name,
              timestamp: new Date().toISOString()
            })
          }
          break
        case 'progress_artifacts':
          // Handle artifacts sent during tool execution as inline canvas content
          if (updateData && updateData.artifacts) {
            const artifacts = updateData.artifacts
            const display = updateData.display || {}

            const canvasFiles = artifacts
              .filter(art => art.b64 && art.mime && art.viewer)
              .map(art => ({
                filename: art.name,
                content_base64: art.b64,
                mime_type: art.mime,
                type: art.viewer,
                description: art.description || art.name,
                // Inline artifacts are rendered from base64; no download key
                isInline: true,
              }))

            if (canvasFiles.length > 0) {
              setCanvasFiles(canvasFiles)
              if (display.primary_file) {
                const idx = canvasFiles.findIndex(f => f.filename === display.primary_file)
                setCurrentCanvasFileIndex(idx >= 0 ? idx : 0)
              } else {
                setCurrentCanvasFileIndex(0)
              }
              if (display.open_canvas) {
                if (typeof setIsCanvasOpen === 'function') {
                  setIsCanvasOpen(true)
                }
                setCanvasContent('')
                setCustomUIContent(null)
              }
            }
          }
          break
        case 'canvas_content':
          if (updateData && updateData.content) {
            setCanvasContent(typeof updateData.content === 'string' ? updateData.content : String(updateData.content || ''))
          }
          break
        case 'canvas_files':
          if (updateData && Array.isArray(updateData.files)) {
            let canvasFiles = updateData.files

            // Check if display config specifies an iframe
            if (updateData.display && updateData.display.type === 'iframe' && updateData.display.url) {
              // Add iframe as a virtual canvas file
              const iframeFile = {
                filename: updateData.display.title || 'Embedded Content',
                type: 'iframe',
                url: updateData.display.url,
                sandbox: updateData.display.sandbox || DEFAULT_IFRAME_SANDBOX,
                isInline: true
              }
              canvasFiles = [iframeFile, ...canvasFiles]
            }

            setCanvasFiles(canvasFiles)
            // If backend provided display hints, respect them (e.g., primary_file)
            if (updateData.display && updateData.display.primary_file) {
              const idx = canvasFiles.findIndex(f => f.filename === updateData.display.primary_file)
              setCurrentCanvasFileIndex(idx >= 0 ? idx : 0)
            } else {
              setCurrentCanvasFileIndex(0)
            }

            // Open canvas panel if display.open_canvas is true
            if (updateData.display && updateData.display.open_canvas && typeof setIsCanvasOpen === 'function') {
              setIsCanvasOpen(true)
            }

            setCanvasContent('')
            setCustomUIContent(null)
          }
          break
        case 'custom_ui':
          if (updateData && updateData.type === 'html_injection' && updateData.content) {
            setCustomUIContent({ type: 'html_injection', content: updateData.content, toolName: updateData.tool_name, serverName: updateData.server_name, timestamp: Date.now() })
          }
          break
        case 'files_update':
          if (updateData) {
            setSessionFiles(() => {
              if (updateData.files && updateData.files.length > 0) {
                const viewableFiles = updateData.files.filter(f => ['png','jpg','jpeg','gif','svg','pdf','html'].includes(f.filename.toLowerCase().split('.').pop()))
                // Only auto-open canvas for tool-generated files or when explicitly requested
                // Skip auto-open for library attachments (source === 'user')
                const shouldAutoOpenCanvas = viewableFiles.length > 0 &&
                  !updateData.files.every(f => f.source === 'user')

                if (shouldAutoOpenCanvas) {
                  const cFiles = viewableFiles.map(f => ({ ...f, type: getFileType(f.filename) }))
                  setCanvasFiles(cFiles)
                  setCurrentCanvasFileIndex(0)
                  setCanvasContent('')
                  setCustomUIContent(null)
                }
              }
              return updateData
            })
          }
          break
        case 'file_download':
          if (updateData && updateData.filename && updateData.content_base64) {
            triggerFileDownload(updateData.filename, updateData.content_base64)
          }
          break
        default:
          break
      }
    } catch (e) {
      console.error('Error handling intermediate update', e, data)
    }
  }

  const handleWebSocketMessage = (data) => {
    try {
  switch (data.type) {
        // Direct tool lifecycle events (new simplified callback path)
        case 'tool_start': {
          if (data.tool_name === 'canvas_canvas') break; // Suppress any chat message for canvas
          addMessage({
            role: 'system',
            content: `**Tool Call: ${data.tool_name}**`,
            type: 'tool_call',
            tool_call_id: data.tool_call_id,
            tool_name: data.tool_name,
            server_name: data.server_name || 'tool',
            arguments: data.arguments || {},
            status: 'calling',
            timestamp: new Date().toISOString(),
            agent_mode: false
          })
          break
        }
        case 'tool_progress': {
          // Update the in-flight tool message with progress
          const { tool_call_id, progress, total, percentage, message } = data
          mapMessages(prev => prev.map(msg => msg.tool_call_id === tool_call_id ? {
            ...msg,
            status: 'in_progress',
            progress: typeof percentage === 'number' ? percentage : undefined,
            progressRaw: { progress, total },
            progressMessage: message || ''
          } : msg))
          break
        }
        case 'tool_complete': {
          if (data.tool_name === 'canvas_canvas') {
            // No update needed; canvas_content event handles display
            break
          }
          mapMessages(prev => prev.map(msg => msg.tool_call_id === data.tool_call_id ? {
              ...msg,
              status: data.success ? 'completed' : 'failed',
              result: data.result || null,
              content: `**Tool: ${data.tool_name}** - ${data.success ? 'Success' : 'Failed'}`
            } : msg))
          break
        }
        case 'tool_error': {
          if (data.tool_name === 'canvas_canvas') {
            addMessage({
              role: 'system',
              content: `Canvas render failed: ${data.error || 'Unknown error'}`,
              type: 'canvas_error',
              timestamp: new Date().toISOString()
            })
            break
          }
          mapMessages(prev => prev.map(msg => msg.tool_call_id === data.tool_call_id ? {
              ...msg,
              status: 'failed',
              result: data.error || 'Unknown error',
              content: `**Tool: ${data.tool_name}** - Failed`
            } : msg))
          break
        }
        case 'tool_synthesis_start': {
          if (typeof setIsSynthesizing === 'function') setIsSynthesizing(true)
          break
        }
        case 'canvas_content': {
          if (data.content) {
            setCanvasContent(typeof data.content === 'string' ? data.content : String(data.content))
          }
          break
        }
        case 'response_complete': {
          setIsThinking(false)
          if (typeof setIsSynthesizing === 'function') setIsSynthesizing(false)
          endTokenStream()
          break
        }
        case 'conversation_saved': {
          if (data.conversation_id && typeof setActiveConversationId === 'function') {
            setActiveConversationId(data.conversation_id)
          }
          break
        }
        case 'token_stream': {
          if (data.is_first) {
            // Reset stale state from any previous abnormal stream end
            _tokenBuffer = ''
            if (_tokenFlushTimer) {
              clearTimeout(_tokenFlushTimer)
              _tokenFlushTimer = null
            }
            _streamActive = true
            setIsThinking(false)
          }
          if (data.is_last) {
            endTokenStream()
            if (typeof setIsSynthesizing === 'function') setIsSynthesizing(false)
          } else if (data.token) {
            _tokenBuffer += data.token
            if (!_tokenFlushTimer) {
              _tokenFlushTimer = setTimeout(() => {
                if (_streamActive) {
                  flushTokenBuffer()
                } else {
                  _tokenBuffer = ''
                  _tokenFlushTimer = null
                }
              }, FLUSH_INTERVAL_MS)
            }
          }
          break
        }
        case 'chat_response':
          setIsThinking(false)
          if (typeof setIsSynthesizing === 'function') setIsSynthesizing(false)
          addMessage({ role: 'assistant', content: data.message, timestamp: new Date().toISOString() })
          break
        case 'error':
          setIsThinking(false)
          if (typeof setIsSynthesizing === 'function') setIsSynthesizing(false)
          endTokenStream()
          addMessage({ role: 'system', content: `Error: ${data.message}`, timestamp: new Date().toISOString() })
          break
        case 'agent_step_update':
          setCurrentAgentStep(data.current_step)
          break
        case 'agent_final_response':
          setIsThinking(false)
          if (typeof setIsSynthesizing === 'function') setIsSynthesizing(false)
            setCurrentAgentStep(0)
            addMessage({ role: 'assistant', content: `${data.message}\n\n*Agent completed in ${data.steps_taken} steps*`, timestamp: new Date().toISOString() })
          break
        case 'file_download':
          if (data.filename && data.content_base64) {
            triggerFileDownload(data.filename, data.content_base64)
          } else if (data.error) {
            console.error('File download error:', data.error)
            // Could show a toast notification here
          }
          break
        case 'file_attach':
          // Handle file attachment response
          if (data.success) {
            // File was successfully attached - add to attachments state
            if (typeof addAttachment === 'function' && data.s3_key) {
              addAttachment(data.s3_key)
            }

            // Try to update pending event in-place, fallback to adding new message
            if (typeof resolvePendingFileEvent === 'function' && data.s3_key) {
              resolvePendingFileEvent(data.s3_key, 'file-attached', `Added '${data.filename || 'file'}' to this session.`)
            } else {
              addMessage({
                role: 'system',
                type: 'system',
                subtype: 'file-attached',
                text: `Added '${data.filename || 'file'}' to this session.`,
                meta: { fileId: data.s3_key, fileName: data.filename, source: 'library' },
                timestamp: new Date().toISOString(),
                id: `file_attach_${Date.now()}`
              })
            }
          } else {
            // File attachment failed
            // Try to update pending event in-place, fallback to adding new message
            if (typeof resolvePendingFileEvent === 'function' && data.s3_key) {
              resolvePendingFileEvent(data.s3_key, 'file-attach-error', `Failed to add file to session: ${data.error || 'Unknown error'}`)
            } else {
              addMessage({
                role: 'system',
                type: 'system',
                subtype: 'file-attach-error',
                text: `Failed to add file to session: ${data.error || 'Unknown error'}`,
                meta: { fileId: data.s3_key, source: 'library' },
                timestamp: new Date().toISOString(),
                id: `file_attach_error_${Date.now()}`
              })
            }
          }
          break
        case 'tool_approval_request':
            // Handle tool approval request - stop thinking and add as a message in the chat
            try { setIsThinking(false) } catch { /* no-op */ }
          addMessage({
            role: 'system',
            content: `Tool Approval Required: ${data.tool_name}`,
            type: 'tool_approval_request',
            tool_call_id: data.tool_call_id,
            tool_name: data.tool_name,
            arguments: data.arguments || {},
            allow_edit: data.allow_edit !== false,
            admin_required: data.admin_required || false,
            status: 'pending',
            timestamp: new Date().toISOString()
          })
          break
        case 'elicitation_request':
          // Handle elicitation request - set pending elicitation state
          try { setIsThinking(false) } catch { /* no-op */ }
          if (typeof setPendingElicitation === 'function') {
            setPendingElicitation({
              elicitation_id: data.elicitation_id,
              tool_call_id: data.tool_call_id,
              tool_name: data.tool_name,
              message: data.message,
              response_schema: data.response_schema
            })
          } else {
            console.error('setPendingElicitation is not available', {
              toolName: data && data.tool_name,
              setPendingElicitationType: typeof setPendingElicitation
            })
          }
          break
        case 'intermediate_update':
          handleIntermediateUpdate(data)
          break
        default:
          // New backend sends { type: 'agent_update', update_type: '...' }
          if (data.type === 'agent_update' && data.update_type) {
            handleAgentUpdate(data)
          } else if (data.update_type === 'agent_update' && data.data) {
            // legacy wrapping
            handleAgentUpdate(data.data)
          } else {
            console.warn('Unknown WebSocket message type:', data.type, Object.keys(data || {}))
          }
          break
      }
    } catch (e) {
      console.error('Error handling WebSocket message', e, data)
    }
  }

  return handleWebSocketMessage
}
