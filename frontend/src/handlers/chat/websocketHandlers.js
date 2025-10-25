// Handlers extracted from original ChatContext to keep provider lean

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
    setSessionFiles,
    getFileType,
    triggerFileDownload
  } = deps

  const handleAgentUpdate = (data) => {
    try {
      const kind = data.update_type || data.type
      switch (kind) {
        case 'agent_start':
          addMessage({ role: 'system', content: `Agent Mode Started (max steps: ${data.max_steps ?? '?'})`, type: 'agent_status', timestamp: new Date().toISOString(), agent_mode: true })
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
          if (typeof setAgentPendingQuestion === 'function') setAgentPendingQuestion(null)
          addMessage({ role: 'system', content: `Agent Completed in ${data.steps ?? '?'} step(s)`, type: 'agent_status', timestamp: new Date().toISOString(), agent_mode: true })
          break
        case 'agent_error':
          addMessage({ role: 'system', content: `Agent Error (Step ${data.turn}): ${data.message}`, type: 'agent_error', timestamp: new Date().toISOString() })
          setIsThinking(false)
          setCurrentAgentStep(0)
          break
        case 'agent_max_steps':
          addMessage({ role: 'system', content: `Agent Max Steps Reached - ${data.message}`, type: 'agent_status', timestamp: new Date().toISOString() })
          setIsThinking(false)
          setCurrentAgentStep(0)
          break
        default:
          // ignore unknown
          break
      }
    } catch (e) {
      console.error('Error handling agent update', e, data)
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
        case 'canvas_content':
          if (updateData && updateData.content) {
            setCanvasContent(typeof updateData.content === 'string' ? updateData.content : String(updateData.content || ''))
          }
          break
        case 'canvas_files':
          if (updateData && Array.isArray(updateData.files)) {
            setCanvasFiles(updateData.files)
            // If backend provided display hints, respect them (e.g., primary_file)
            if (updateData.display && updateData.display.primary_file) {
              const idx = updateData.files.findIndex(f => f.filename === updateData.display.primary_file)
              setCurrentCanvasFileIndex(idx >= 0 ? idx : 0)
            } else {
              setCurrentCanvasFileIndex(0)
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
                if (viewableFiles.length > 0) {
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
      console.log(`WebSocket message from backend: ${data.type || 'unknown'}`)
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
  // Removed: 'tool_synthesis' no longer rendered as an assistant message to prevent duplicates.
        case 'canvas_content': {
          if (data.content) {
            setCanvasContent(typeof data.content === 'string' ? data.content : String(data.content))
          }
          break
        }
        case 'response_complete': {
          setIsThinking(false)
          break
        }
        case 'chat_response':
          setIsThinking(false)
          addMessage({ role: 'assistant', content: data.message, timestamp: new Date().toISOString() })
          break
        case 'error':
          setIsThinking(false)
          addMessage({ role: 'system', content: `Error: ${data.message}`, timestamp: new Date().toISOString() })
          break
        case 'agent_step_update':
          setCurrentAgentStep(data.current_step)
          break
        case 'agent_final_response':
          setIsThinking(false)
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
            console.log('Unknown WebSocket message type:', data)
          }
          break
      }
    } catch (e) {
      console.error('Error handling WebSocket message', e, data)
    }
  }

  return handleWebSocketMessage
}
