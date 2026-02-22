import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { useChat } from '../contexts/ChatContext'
import { useState, memo, useEffect, useRef } from 'react'
import { Copy } from 'lucide-react'
import hljs from 'highlight.js'
import 'highlight.js/styles/github-dark.css'

// Register common languages for better syntax highlighting
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import python from 'highlight.js/lib/languages/python'
import rust from 'highlight.js/lib/languages/rust'
import go from 'highlight.js/lib/languages/go'
import java from 'highlight.js/lib/languages/java'
import cpp from 'highlight.js/lib/languages/cpp'
import css from 'highlight.js/lib/languages/css'
import html from 'highlight.js/lib/languages/xml'
import json from 'highlight.js/lib/languages/json'
import yaml from 'highlight.js/lib/languages/yaml'
import sql from 'highlight.js/lib/languages/sql'
import bash from 'highlight.js/lib/languages/bash'

hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('ts', typescript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('py', python)
hljs.registerLanguage('rust', rust)
hljs.registerLanguage('rs', rust)
hljs.registerLanguage('go', go)
hljs.registerLanguage('golang', go)
hljs.registerLanguage('java', java)
hljs.registerLanguage('cpp', cpp)
hljs.registerLanguage('c++', cpp)
hljs.registerLanguage('css', css)
hljs.registerLanguage('html', html)
hljs.registerLanguage('xml', html)
hljs.registerLanguage('json', json)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('yml', yaml)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('shell', bash)
hljs.registerLanguage('sh', bash)

// Helper function to highlight @file references in message content
const processFileReferences = (content) => {
  return content.replace(
    /@file\s+([^\s]+)/g,
    '<span class="inline-flex items-center px-2 py-1 rounded-md bg-green-900/30 border border-green-500/30 text-green-400 text-sm font-medium">@file $1</span>'
  )
}

// Helper function to convert bullet characters to proper markdown list syntax
const convertBulletListsToMarkdown = (content) => {
  // Convert lines starting with bullet characters (•, ◦, ▪, ▫, ‣) to markdown lists
  return content.replace(/^(\s*)[•◦▪▫‣]\s+(.+)$/gm, '$1- $2')
}

// Configure marked with custom renderer for code blocks
const renderer = new marked.Renderer()
renderer.code = function(code, language) {
  // Handle different code input types
  let codeString = ''
  let actualLanguage = language
  
  if (typeof code === 'string') {
    codeString = code
  } else if (code && typeof code === 'object') {
    // Check if this is a structured code block object
    if (code.text && typeof code.text === 'string') {
      // Use the text property for structured code blocks
      codeString = code.text
      // Use the lang property if available
      if (code.lang && !actualLanguage) {
        actualLanguage = code.lang
      }
    } else if (code.raw && typeof code.raw === 'string') {
      // Handle raw markdown code blocks - extract the content
      const rawMatch = code.raw.match(/```(\w*)\n([\s\S]*?)\n```/)
      if (rawMatch) {
        codeString = rawMatch[2] || ''
        if (rawMatch[1] && !actualLanguage) {
          actualLanguage = rawMatch[1]
        }
      } else {
        codeString = code.raw
      }
    } else {
      // Fallback to JSON for other objects
      try {
        codeString = JSON.stringify(code, null, 2)
        actualLanguage = 'json'
      } catch {
        codeString = String(code)
      }
    }
  } else {
    codeString = String(code || '')
  }
  
  // Apply syntax highlighting
  let highlightedCode = ''
  if (actualLanguage && hljs.getLanguage(actualLanguage)) {
    try {
      const result = hljs.highlight(codeString, { language: actualLanguage })
      highlightedCode = result.value
    } catch (e) {
      console.warn('Highlight.js error for language', actualLanguage, e)
      // Fallback to manual escaping
      highlightedCode = codeString.replace(/&/g, '&amp;')
                          .replace(/</g, '&lt;')
                          .replace(/>/g, '&gt;')
                          .replace(/"/g, '&quot;')
                          .replace(/'/g, '&#39;')
    }
  } else {
    // Auto-detect language if not specified or not supported
    try {
      const result = hljs.highlightAuto(codeString)
      highlightedCode = result.value
      // Update language for display
      if (result.language && !actualLanguage) {
        actualLanguage = result.language
      }
    } catch (e) {
      console.warn('Highlight.js auto-detection error', e)
      // Fallback to manual escaping
      highlightedCode = codeString.replace(/&/g, '&amp;')
                          .replace(/</g, '&lt;')
                          .replace(/>/g, '&gt;')
                          .replace(/"/g, '&quot;')
                          .replace(/'/g, '&#39;')
    }
  }
  
  return `<div class="code-block-container relative bg-gray-900 rounded-lg my-4 border border-gray-700">
    <div class="flex items-center justify-between px-4 py-2 bg-gray-800 border-b border-gray-700">
      <span class="text-xs text-gray-400 font-medium uppercase tracking-wider">${actualLanguage || 'text'}</span>
      <button 
        class="copy-button bg-gray-700 hover:bg-gray-600 border border-gray-600 text-gray-200 px-3 py-1 rounded text-xs transition-all duration-200 cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500" 
        data-action="copy-code"
        title="Copy code to clipboard"
        type="button"
      >Copy</button>
    </div>
    <pre class="p-4 overflow-x-auto bg-gray-900 m-0"><code class="hljs language-${actualLanguage || 'text'} text-sm leading-relaxed">${highlightedCode}</code></pre>
  </div>`
}

marked.setOptions({
  renderer: renderer,
  highlight: function(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return hljs.highlight(code, { language: lang }).value
      } catch (err) {
        console.warn('Highlight.js error:', err)
      }
    }
    try {
      return hljs.highlightAuto(code).value
    } catch (err) {
      console.warn('Highlight.js auto-detection error:', err)
      return code
    }
  },
  breaks: true,
  gfm: true
})

// Copy function for code blocks
const copyCodeBlock = (button) => {
  try {
    // Find the code block container
    const container = button.closest('.code-block-container')
    if (!container) {
      console.error('Could not find code block container')
      return
    }
    
    // Find the code element within the container
    const codeBlock = container.querySelector('code')
    if (!codeBlock) {
      console.error('Could not find code element')
      return
    }
    
    // Get the text content
    const text = codeBlock.textContent || codeBlock.innerText || ''
    
    // Copy to clipboard
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => {
        showCopySuccess(button)
      }).catch(err => {
        console.error('Failed to copy with Clipboard API: ', err)
        fallbackCopy(text, button)
      })
    } else {
      fallbackCopy(text, button)
    }
  } catch (err) {
    console.error('Error in copyCodeBlock: ', err)
  }
}

// Show copy success feedback
const showCopySuccess = (button) => {
  const originalHTML = button.innerHTML
  
  // Update button to show success state
  button.innerHTML = '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>'
  button.classList.add('bg-green-600', 'border-green-500')
  button.classList.remove('bg-gray-700', 'border-gray-600')
  
  setTimeout(() => {
    button.innerHTML = originalHTML
    button.classList.remove('bg-green-600', 'border-green-500')
    button.classList.add('bg-gray-700', 'border-gray-600')
  }, 2000)
}

// Fallback copy method for older browsers
const fallbackCopy = (text, button) => {
  try {
    const textArea = document.createElement('textarea')
    textArea.value = text
    textArea.style.position = 'fixed'
    textArea.style.left = '-999999px'
    textArea.style.top = '-999999px'
    document.body.appendChild(textArea)
    textArea.focus()
    textArea.select()
    
    const successful = document.execCommand('copy')
    document.body.removeChild(textArea)
    
    if (successful) {
      showCopySuccess(button)
    } else {
      console.error('Fallback copy failed')
    }
  } catch (err) {
    console.error('Fallback copy error: ', err)
  }
}

// Show copy success feedback for message copy button
const showMessageCopySuccess = (button) => {
  // Store original classes
  const originalClasses = button.className
  
  // Update button to show success state
  button.classList.remove('bg-gray-700', 'hover:bg-gray-600', 'border-gray-600', 'text-gray-200')
  button.classList.add('bg-green-600', 'hover:bg-green-700', 'border-green-500', 'text-white')
  
  setTimeout(() => {
    // Restore original classes
    button.className = originalClasses
  }, 2000)
}

// Fallback copy method for message content
const fallbackMessageCopy = (text, button) => {
  try {
    const textArea = document.createElement('textarea')
    textArea.value = text
    textArea.style.position = 'fixed'
    textArea.style.left = '-999999px'
    textArea.style.top = '-999999px'
    document.body.appendChild(textArea)
    textArea.focus()
    textArea.select()
    
    const successful = document.execCommand('copy')
    document.body.removeChild(textArea)
    
    if (successful) {
      showMessageCopySuccess(button)
    } else {
      console.error('Fallback message copy failed')
    }
  } catch (err) {
    console.error('Fallback message copy error: ', err)
  }
}

// Copy function for entire message content
const copyMessageContent = (content, button) => {
  try {
    // Get the raw text content, stripping any HTML/markdown formatting
    let textToCopy = ''
    
    if (typeof content === 'string') {
      textToCopy = content
    } else if (content && typeof content === 'object') {
      if (content.raw && typeof content.raw === 'string') {
        textToCopy = content.raw
      } else if (content.text && typeof content.text === 'string') {
        textToCopy = content.text
      } else {
        textToCopy = JSON.stringify(content, null, 2)
      }
    } else {
      textToCopy = String(content || '')
    }
    
    // Copy to clipboard
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(textToCopy).then(() => {
        showMessageCopySuccess(button)
      }).catch(err => {
        console.error('Failed to copy message with Clipboard API: ', err)
        fallbackMessageCopy(textToCopy, button)
      })
    } else {
      fallbackMessageCopy(textToCopy, button)
    }
  } catch (err) {
    console.error('Error in copyMessageContent: ', err)
  }
}

// Helper function to process message content (strings and structured objects)
const processMessageContent = (content) => {
  let processedContent = ''
  
  if (typeof content === 'string') {
    processedContent = content
  } else if (content && typeof content === 'object') {
    // Handle structured content objects that might contain markdown
    if (content.raw && typeof content.raw === 'string') {
      // If there's a raw property, use it (likely contains markdown)
      processedContent = content.raw
    } else if (content.text && typeof content.text === 'string') {
      // If there's a text property, use it
      processedContent = content.text
    } else {
      // Fallback to JSON for other objects
      try {
        processedContent = JSON.stringify(content, null, 2)
      } catch {
        processedContent = String(content)
      }
    }
  } else {
    processedContent = String(content || '')
  }
  
  // Apply all content transformations in sequence
  processedContent = convertBulletListsToMarkdown(processedContent)
  processedContent = processFileReferences(processedContent)
  
  return processedContent
}

// Helper function to filter out base64 data from tool arguments for display
const filterArgumentsForDisplay = (args) => {
  if (!args || typeof args !== 'object') return args
  
  const filteredArgs = { ...args }
  
  // Hide base64 data but show filename for context
  if (filteredArgs.file_data_base64) {
    const dataSize = filteredArgs.file_data_base64.length
    filteredArgs.file_data_base64 = `[File data: ${dataSize} characters - hidden for display]`
  }
  
  return filteredArgs
}

// Helper function to filter and enhance tool results for display
const processToolResult = (result) => {
  if (!result) return result
  
  if (typeof result === 'string') {
    try {
      // Try to parse as JSON to check for returned files
      const parsed = JSON.parse(result)
      return processToolResult(parsed)
    } catch {
      // Not JSON, return as is
      return result
    }
  }
  
  if (typeof result === 'object') {
    const processed = { ...result }
    
    // Handle artifacts array (new format)
    if (processed.artifacts && Array.isArray(processed.artifacts)) {
      processed.artifacts = processed.artifacts.map(artifact => ({
        name: artifact.name,
        mime: artifact.mime,
        b64: `[File data: ${artifact.b64.length} characters - hidden for display]`
      }))
      processed._artifacts_download_available = true
    }
    // Handle returned files (multiple files support - legacy format)
    else if (processed.returned_files && Array.isArray(processed.returned_files)) {
      // Multiple files case
      processed.returned_files = processed.returned_files.map(file => ({
        filename: file.filename,
        content_base64: `[File data: ${file.content_base64.length} characters - hidden for display]`
      }))
      processed._multiple_files_download_available = true
    } else if (processed.returned_file_base64 && processed.returned_file_name) {
      // Single file case (backward compatibility)
      const dataSize = processed.returned_file_base64.length
      processed.returned_file_base64 = `[File data: ${dataSize} characters - hidden for display]`
      processed._file_download_available = true
    }
    
    // Handle returned_file_contents for backward compatibility
    if (processed.returned_file_contents && Array.isArray(processed.returned_file_contents)) {
      processed.returned_file_contents = processed.returned_file_contents.map(content => 
        `[File data: ${content.length} characters - hidden for display]`
      )
    }
    
    // Hide any other base64 fields
    Object.keys(processed).forEach(key => {
      if (key.includes('base64') && key !== 'returned_file_base64') {
        if (typeof processed[key] === 'string' && processed[key].length > 100) {
          processed[key] = `[Base64 data: ${processed[key].length} characters - hidden for display]`
        }
      }
    })
    
    return processed
  }
  
  return result
}

// Helper function to download returned files
const downloadReturnedFile = (filename, base64Data) => {
  try {
    // Convert base64 to blob
    const byteCharacters = atob(base64Data)
    const byteNumbers = new Array(byteCharacters.length)
    for (let i = 0; i < byteCharacters.length; i++) {
      byteNumbers[i] = byteCharacters.charCodeAt(i)
    }
    const byteArray = new Uint8Array(byteNumbers)
    
    // Determine MIME type based on file extension
    const extension = filename.split('.').pop()?.toLowerCase()
    let mimeType = 'application/octet-stream'
    
    const mimeTypes = {
      'pdf': 'application/pdf',
      'txt': 'text/plain',
      'json': 'application/json',
      'csv': 'text/csv',
      'png': 'image/png',
      'jpg': 'image/jpeg',
      'jpeg': 'image/jpeg',
      'gif': 'image/gif',
      'doc': 'application/msword',
      'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'py': 'text/x-python',
      'js': 'text/javascript',
      'html': 'text/html',
      'css': 'text/css',
      'xml': 'application/xml'
    }
    
    if (extension && mimeTypes[extension]) {
      mimeType = mimeTypes[extension]
    }
    
    const blob = new Blob([byteArray], { type: mimeType })
    
    // Create download link and trigger download
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    
    // Clean up
    setTimeout(() => URL.revokeObjectURL(url), 100)
    
  } catch (error) {
    console.error('Error downloading file:', error)
    alert('Failed to download file. Please try again.')
  }
}

// Tool Approval Message Component
const ToolApprovalMessage = ({ message }) => {
  const { sendApprovalResponse, settings, updateSettings } = useChat()
  const [isEditing, setIsEditing] = useState(false)
  const [editedArgs, setEditedArgs] = useState(message.arguments)
  const [reason, setReason] = useState('')
  const [isExpanded, setIsExpanded] = useState(true)

  // Auto-approve if user has the setting enabled and this is not an admin-required approval
  useEffect(() => {
    if (settings?.autoApproveTools && !message.admin_required && message.status === 'pending') {
      // Auto-approve after a brief delay to show the message
      const timer = setTimeout(() => {
        sendApprovalResponse({
          type: 'tool_approval_response',
          tool_call_id: message.tool_call_id,
          approved: true,
          arguments: message.arguments,
        })
      }, 100)
      return () => clearTimeout(timer)
    }
  }, [settings?.autoApproveTools, message.admin_required, message.status, message.tool_call_id, message.arguments, sendApprovalResponse])

  const handleApprove = () => {
    console.log('[ToolApproval] Approve button clicked', {
      tool_call_id: message.tool_call_id,
      tool_name: message.tool_name,
      isEditing,
      arguments: isEditing ? editedArgs : message.arguments
    })
    
    const response = {
      type: 'tool_approval_response',
      tool_call_id: message.tool_call_id,
      approved: true,
      arguments: isEditing ? editedArgs : message.arguments,
    }
    
    console.log('[ToolApproval] Sending approval response:', JSON.stringify(response, null, 2))
    sendApprovalResponse(response)
    console.log('[ToolApproval] Approval response sent')
  }

  const handleReject = () => {
    console.log('[ToolApproval] Reject button clicked', {
      tool_call_id: message.tool_call_id,
      tool_name: message.tool_name,
      reason
    })
    
    const response = {
      type: 'tool_approval_response',
      tool_call_id: message.tool_call_id,
      approved: false,
      reason: reason || 'User rejected the tool call',
    }
    
    console.log('[ToolApproval] Sending rejection response:', JSON.stringify(response, null, 2))
    sendApprovalResponse(response)
    console.log('[ToolApproval] Rejection response sent')
  }

  const handleArgumentChange = (key, value) => {
    setEditedArgs(prev => ({
      ...prev,
      [key]: value
    }))
  }

  // Don't show if already responded
  if (message.status === 'approved' || message.status === 'rejected') {
    return (
      <div className="text-gray-200">
        <div className="flex items-center gap-2 mb-3">
          <span className={`px-2 py-1 rounded text-xs font-medium ${
            message.status === 'approved' ? 'bg-green-600' : 'bg-red-600'
          }`}>
            {message.status === 'approved' ? 'APPROVED' : 'REJECTED'}
          </span>
          <span className="font-medium">{message.tool_name}</span>
        </div>
        {message.status === 'rejected' && message.rejection_reason && (
          <div className="text-sm text-gray-400">Reason: {message.rejection_reason}</div>
        )}
      </div>
    )
  }

  return (
    <div className="text-gray-200">
      <div className="flex items-center gap-2 mb-3">
        <span className={`px-2 py-1 rounded text-xs font-medium ${
          settings?.autoApproveTools && !message.admin_required ? 'bg-blue-600' : 'bg-yellow-600'
        }`}>
          {settings?.autoApproveTools && !message.admin_required ? 'AUTO-APPROVED' : 'APPROVAL REQUIRED'}
        </span>
        <span className="font-medium">{message.tool_name}</span>
        {!message.admin_required && (
          <button
            type="button"
            onClick={() => {
              try {
                updateSettings?.({ autoApproveTools: !settings?.autoApproveTools })
              } catch (e) {
                console.error('Failed to toggle auto-approve from inline control', e)
              }
            }}
            className={`ml-2 px-2 py-0.5 rounded text-xs font-medium border transition-colors cursor-pointer ${
              settings?.autoApproveTools
                ? 'bg-blue-600 text-white border-blue-500 hover:bg-blue-700'
                : 'bg-gray-700 text-gray-100 border-gray-600 hover:bg-gray-600'
            }`}
            title="Click to toggle auto-approve for non-admin tool calls. Admin-required calls will still prompt."
          >
            {settings?.autoApproveTools ? 'Auto-approve ON' : 'Auto-approve OFF'}
          </button>
        )}
      </div>

      {/* Arguments Section */}
      <div className="mb-4">
        <div className="border-l-4 border-yellow-500 pl-4">
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="w-full text-left text-sm font-semibold text-yellow-400 mb-2 flex items-center gap-2 hover:text-yellow-300 transition-colors"
          >
            <span className={`transform transition-transform duration-200 ${isExpanded ? 'rotate-90' : 'rotate-0'}`}>
              ▶
            </span>
            Tool Arguments {!isExpanded ? `(${Object.keys(message.arguments).length} params)` : ''}
          </button>
          
          {isExpanded && (
            <>
              <div className="mb-2 flex items-center gap-2">
                <button
                  onClick={() => setIsEditing(!isEditing)}
                  className="px-3 py-1 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors"
                >
                  {isEditing ? 'View Mode' : 'Edit Arguments'}
                </button>
              </div>

              {!isEditing ? (
                <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 max-h-96 overflow-y-auto">
                  <pre className="text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap">
                    {JSON.stringify(message.arguments, null, 2)}
                  </pre>
                </div>
              ) : (
                <div className="space-y-3 max-h-[60vh] overflow-y-auto">
                  {Object.entries(editedArgs).map(([key, value]) => (
                    <div key={key} className="bg-gray-900 border border-gray-700 rounded-lg p-3">
                      <label className="block text-sm font-medium text-gray-300 mb-1">
                        {key}
                      </label>
                      <textarea
                        value={typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}
                        onChange={(e) => {
                          const newValue = e.target.value
                          // Try to parse as JSON if it's a complete JSON structure
                          if ((newValue.trim().startsWith('{') && newValue.trim().endsWith('}')) ||
                              (newValue.trim().startsWith('[') && newValue.trim().endsWith(']'))) {
                            try {
                              const parsed = JSON.parse(newValue)
                              handleArgumentChange(key, parsed)
                              return
                            } catch {
                              // Not valid JSON yet, use string value
                            }
                          }
                          // Use string value for non-JSON or incomplete JSON
                          handleArgumentChange(key, newValue)
                        }}
                        className="w-full bg-gray-800 text-gray-200 border border-gray-600 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                        rows={Math.max(3, Math.min(20, (typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)).split('\n').length))}
                      />
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Action Buttons and Rejection Reason - Compact Layout */}
          {!(settings?.autoApproveTools && !message.admin_required) && (
            <div className="flex gap-2 items-center">
              <button
                onClick={handleApprove}
                className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors whitespace-nowrap"
              >
                Approve {isEditing ? '(with edits)' : ''}
              </button>
              <button
                onClick={handleReject}
                className="px-3 py-1.5 text-sm bg-gray-700 hover:bg-gray-600 text-gray-200 rounded border border-gray-600 transition-colors whitespace-nowrap"
              >
                Reject
              </button>
              <input
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Rejection reason (optional)..."
                className="flex-1 bg-gray-900 text-gray-200 border border-gray-700 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          )}
    </div>
  )
}

// Elapsed time indicator for active tool calls with timeout warning
const TOOL_SLOW_THRESHOLD_SEC = 30

const ToolElapsedTime = ({ timestamp }) => {
  const [elapsed, setElapsed] = useState(0)
  const startRef = useRef(timestamp ? new Date(timestamp).getTime() : Date.now())

  useEffect(() => {
    startRef.current = timestamp ? new Date(timestamp).getTime() : Date.now()
    setElapsed(0)
  }, [timestamp])

  useEffect(() => {
    const tick = () => {
      setElapsed(Math.floor((Date.now() - startRef.current) / 1000))
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  const minutes = Math.floor(elapsed / 60)
  const seconds = elapsed % 60
  const timeStr = minutes > 0
    ? `${minutes}m ${String(seconds).padStart(2, '0')}s`
    : `${seconds}s`
  const isSlow = elapsed >= TOOL_SLOW_THRESHOLD_SEC

  return (
    <span className="flex items-center gap-1 text-xs text-gray-400 ml-1">
      <span>{timeStr}</span>
      {isSlow && (
        <span className="text-yellow-400">- taking longer than expected</span>
      )}
    </span>
  )
}

const Message = ({ message }) => {
  const { appName, downloadFile, isSynthesizing } = useChat()
  
  // State for collapsible sections with localStorage persistence
  const [toolInputCollapsed, setToolInputCollapsed] = useState(() => {
    const saved = localStorage.getItem('toolInputCollapsed')
    return saved !== null ? JSON.parse(saved) : true // Start collapsed by default
  })
  
  const [toolOutputCollapsed, setToolOutputCollapsed] = useState(() => {
    const saved = localStorage.getItem('toolOutputCollapsed')
    return saved !== null ? JSON.parse(saved) : true // Start collapsed by default
  })
  
  // Save preferences to localStorage when they change
  useEffect(() => {
    localStorage.setItem('toolInputCollapsed', JSON.stringify(toolInputCollapsed))
  }, [toolInputCollapsed])
  
  useEffect(() => {
    localStorage.setItem('toolOutputCollapsed', JSON.stringify(toolOutputCollapsed))
  }, [toolOutputCollapsed])
  
  const isUser = message.role === 'user'
  const isSystem = message.role === 'system'

  // Handle copy message button click
  const handleCopyMessage = (event) => {
    event.preventDefault()
    copyMessageContent(message.content, event.currentTarget)
  }

  // Handle code block copy buttons using event delegation for this message
  useEffect(() => {
    const handleCodeCopyClick = (event) => {
      if (event.target.matches('[data-action="copy-code"]') || 
          event.target.closest('[data-action="copy-code"]')) {
        event.preventDefault()
        const button = event.target.matches('[data-action="copy-code"]') 
          ? event.target 
          : event.target.closest('[data-action="copy-code"]')
        copyCodeBlock(button)
      }
    }

    document.addEventListener('click', handleCodeCopyClick)
    return () => {
      document.removeEventListener('click', handleCodeCopyClick)
    }
  }, [])
  
  const avatarBg = isUser ? 'bg-green-600' : isSystem ? 'bg-yellow-600' : 'bg-blue-600'
  const avatarText = isUser ? 'Y' : isSystem ? 'S' : 'A'
  const authorName = isUser ? 'You' : isSystem ? 'System' : appName

  // Note: Tool auto-approval handled inside ToolApprovalMessage; we keep message visible so inline toggle remains accessible.
  
const renderContent = () => {
    // Handle tool approval request messages
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
                  <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 max-h-64 overflow-y-auto">
                    <pre className="text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap">
                      {JSON.stringify(filterArgumentsForDisplay(message.arguments), null, 2)}
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
                  <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 max-h-64 overflow-y-auto">
                    <pre className="text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap">
                      {(() => {
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
      // Handle tool log messages with badges and colors
      if (message.type === 'tool_log') {
        const logLevel = message.log_level || message.subtype || 'info'
        let badgeColor
        let textColor
        
        // Apply colors based on log level
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
            badgeColor = 'bg-gray-500 text-white'
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
      
      // Handle file attachment system events
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
    // Process content to handle both strings and structured objects
    const content = processMessageContent(message.content)

    try {
      const markdownHtml = marked.parse(content)
      const sanitizedHtml = DOMPurify.sanitize(markdownHtml)

      return (
        <div
          className="prose prose-invert max-w-none selectable-markdown"
          dangerouslySetInnerHTML={{ __html: sanitizedHtml }}
        />
      )
    } catch (error) {
      console.error('Error parsing markdown content:', error)
      // Fallback to plain text if markdown parsing fails
      return (
        <div className="text-gray-200">
          <pre className="whitespace-pre-wrap">{content}</pre>
        </div>
      )
    }
  }

  return (
    <div className={`flex items-start gap-3 ${isUser ? 'flex-row-reverse' : 'w-full'} group`}>
      {/* Avatar */}
      <div className={`w-8 h-8 rounded-full ${avatarBg} flex items-center justify-center text-white text-sm font-medium flex-shrink-0`}>
        {avatarText}
      </div>
      
      {/* Message Content */}
      <div className={`${isUser ? 'max-w-[70%] bg-blue-600' : 'w-full bg-gray-800'} rounded-lg p-4`}>
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
          {/* Copy button for user and assistant messages */}
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
        {/* Streaming cursor indicator */}
        {message._streaming && (
          <span className="inline-block w-2 h-4 bg-blue-400 animate-pulse ml-0.5 align-text-bottom" aria-label="Generating response..." />
        )}
      </div>
    </div>
  )
}

export default memo(Message)
