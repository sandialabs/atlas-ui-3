import { useState, useRef, useEffect, useCallback } from 'react'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'
import { Send, Paperclip, X, Square } from 'lucide-react'
import Message from './Message'
import WelcomeScreen from './WelcomeScreen'
import EnabledToolsIndicator from './EnabledToolsIndicator'

const ChatArea = () => {
  const [inputValue, setInputValue] = useState('')
  const [isMobile, setIsMobile] = useState(false)
  const [uploadedFiles, setUploadedFiles] = useState({})
  const [showToolAutocomplete, setShowToolAutocomplete] = useState(false)
  const [filteredTools, setFilteredTools] = useState([])
  const [selectedToolIndex, setSelectedToolIndex] = useState(0)
  const [showFileAutocomplete, setShowFileAutocomplete] = useState(false)
  const [filteredFiles, setFilteredFiles] = useState([])
  const [selectedFileIndex, setSelectedFileIndex] = useState(0)
  const textareaRef = useRef(null)
  const messagesRef = useRef(null)
  const endRef = useRef(null)
  const userScrolledRef = useRef(false)
  const prevMessageCountRef = useRef(0)
  const fileInputRef = useRef(null)
  
  const { 
    messages, 
    isWelcomeVisible, 
    isThinking, 
    sendChatMessage, 
    currentModel,
    tools,
    selectedTools,
    toggleTool,
    toolChoiceRequired,
    setToolChoiceRequired,
  sessionFiles,
  agentModeEnabled,
  agentPendingQuestion,
  setAgentPendingQuestion,
  stopAgent,
  answerAgentQuestion
  } = useChat()
  const { isConnected } = useWS()

  // Auto-resize textarea
  const autoResizeTextarea = () => {
    const textarea = textareaRef.current
    if (textarea) {
      textarea.style.height = 'auto'
      textarea.style.height = Math.min(textarea.scrollHeight, 128) + 'px'
    }
  }

  // Check for mobile screen size
  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768)
    }
    
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  // Focus message input on page load
  useEffect(() => {
    const focusInput = () => {
      if (textareaRef.current && !isMobile) { // Don't auto-focus on mobile to prevent keyboard popup
        textareaRef.current.focus()
      }
    }
    
    // Focus after a brief delay to ensure the component is fully rendered
    const timeoutId = setTimeout(focusInput, 200)
    return () => clearTimeout(timeoutId)
  }, [isMobile])

  // Track if user manually scrolled up (to disable auto-scroll until they return near bottom)
  useEffect(() => {
    const el = messagesRef.current
    if (!el) return
    const handleScroll = () => {
      const distanceFromBottom = el.scrollHeight - (el.scrollTop + el.clientHeight)
      userScrolledRef.current = distanceFromBottom > 140 // threshold
    }
    el.addEventListener('scroll', handleScroll, { passive: true })
    return () => el.removeEventListener('scroll', handleScroll)
  }, [])

  // Function to perform smooth scroll to bottom respecting user scroll state
  const scrollToBottom = useCallback((force = false) => {
    const el = messagesRef.current
    if (!el) return
    if (userScrolledRef.current && !force) return
    if (endRef.current && typeof endRef.current.scrollIntoView === 'function') {
      endRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' })
    } else {
      el.scrollTop = el.scrollHeight
    }
  }, [])

  // Scroll when messages list changes (initial render or new message)
  useEffect(() => {
    const newCount = messages.length
    const lastMsg = messages[messages.length - 1]
    const isNewMessage = newCount !== prevMessageCountRef.current
    const force = isNewMessage && lastMsg && (lastMsg.role !== 'user')
    prevMessageCountRef.current = newCount
    requestAnimationFrame(() => {
      scrollToBottom(force)
      setTimeout(() => scrollToBottom(force), 80)
      setTimeout(() => scrollToBottom(force), 250)
    })
  }, [messages, isThinking, scrollToBottom])

  // Observe DOM mutations inside messages container (handles content expansion post-render)
  useEffect(() => {
    const el = messagesRef.current
    if (!el) return
    const observer = new MutationObserver(() => {
      const lastMsg = messages[messages.length - 1]
      const force = lastMsg && lastMsg.role !== 'user'
      scrollToBottom(force)
    })
    observer.observe(el, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [scrollToBottom, messages])

  const handleSubmit = async (e) => {
    e.preventDefault()
    const message = inputValue.trim()
    if (!message || !currentModel || !isConnected) {
      console.debug('Submit blocked:', { message: !!message, currentModel: !!currentModel, isConnected })
      return
    }
    
    try {
      // Process @file references in the message
      const processedFiles = await processFileReferences(message)
      const allFiles = { ...uploadedFiles, ...processedFiles }
      
      sendChatMessage(message, allFiles)
      setInputValue('')
      
      // Reset textarea height
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto'
      }
    } catch (error) {
      console.error('Error in handleSubmit:', error)
      // Still try to send the message without file processing
      sendChatMessage(message, uploadedFiles)
      setInputValue('')
      
      // Reset textarea height
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto'
      }
    }
  }
  
  // Process @file references in the message and return file content
  const processFileReferences = async (message) => {
    const fileRefs = {}
    const fileRegex = /@file\s+([^\s]+)/g
    let match
    
    // Early return if sessionFiles is not properly initialized
    if (!sessionFiles || !sessionFiles.files || !Array.isArray(sessionFiles.files)) {
      console.debug('Session files not available for @file processing')
      return fileRefs
    }
    
    while ((match = fileRegex.exec(message)) !== null) {
      const filename = match[1]
      
      // Find the file in session files
      const file = sessionFiles.files.find(f => f.filename === filename)
      if (file && file.s3_key) {
        try {
          // Fetch file content from S3 via API
          const response = await fetch(`/api/files/${encodeURIComponent(file.s3_key)}`, {
            headers: {
              'Authorization': `Bearer ${localStorage.getItem('userEmail') || 'user@example.com'}`
            }
          })
          
          if (response.ok) {
            const fileData = await response.json()
            fileRefs[filename] = fileData.content_base64
            console.log(`📎 Loaded content for @file ${filename}`)
          } else {
            console.warn(`Failed to load @file ${filename}:`, response.status)
            fileRefs[filename] = `[Error loading file: ${filename}]`
          }
        } catch (error) {
          console.error(`Error fetching @file ${filename}:`, error)
          fileRefs[filename] = `[Error loading file: ${filename}]`
        }
      } else {
        console.warn(`@file ${filename} not found in session files`)
        fileRefs[filename] = `[File not found: ${filename}]`
      }
    }
    
    return fileRefs
  }

  const handleKeyDown = (e) => {
    // Handle autocomplete navigation for tools
    if (showToolAutocomplete) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedToolIndex(prev => 
          prev < filteredTools.length - 1 ? prev + 1 : 0
        )
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedToolIndex(prev => 
          prev > 0 ? prev - 1 : filteredTools.length - 1
        )
      } else if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        if (filteredTools[selectedToolIndex]) {
          selectTool(filteredTools[selectedToolIndex])
        }
        return
      } else if (e.key === 'Escape') {
        e.preventDefault()
        setShowToolAutocomplete(false)
        return
      }
    }
    
    // Handle autocomplete navigation for files
    if (showFileAutocomplete) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedFileIndex(prev => 
          prev < filteredFiles.length - 1 ? prev + 1 : 0
        )
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedFileIndex(prev => 
          prev > 0 ? prev - 1 : filteredFiles.length - 1
        )
      } else if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        if (filteredFiles[selectedFileIndex]) {
          selectFile(filteredFiles[selectedFileIndex])
        }
        return
      } else if (e.key === 'Escape') {
        e.preventDefault()
        setShowFileAutocomplete(false)
        return
      }
    }
    
    // Normal enter handling
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit(e)
    }
  }

  const handleInputChange = (e) => {
    const value = e.target.value
    setInputValue(value)
    autoResizeTextarea()
    
    // Handle autocomplete for different command types
    handleAutoComplete(value)
  }

  // Get all available tools as flat list
  const getAllAvailableTools = () => {
    const allTools = []
    tools.forEach(toolServer => {
      toolServer.tools.forEach(toolName => {
        allTools.push({
          key: `${toolServer.server}_${toolName}`,
          name: toolName,
          server: toolServer.server,
          description: toolServer.description
        })
      })
    })
    return allTools
  }

  // Get all available files as flat list
  const getAllAvailableFiles = () => {
    if (!sessionFiles || !sessionFiles.files) return []
    
    return sessionFiles.files.map(file => ({
      filename: file.filename,
      type: file.type || 'other',
      size: file.size || 0,
      source: file.source || 'unknown',
      extension: file.extension || ''
    }))
  }

  // Handle autocomplete for slash commands and @file commands
  const handleAutoComplete = (value) => {
    const cursorPosition = textareaRef.current?.selectionStart || value.length
    const textBeforeCursor = value.substring(0, cursorPosition)
    
    // Find the last occurrence of @ or / before cursor
    const lastAtIndex = textBeforeCursor.lastIndexOf('@')
    const lastSlashIndex = textBeforeCursor.lastIndexOf('/')
    
    // Determine which command type is active
    let commandType = null
    let commandStart = -1
    
    if (lastAtIndex > lastSlashIndex && lastAtIndex !== -1) {
      // Check if it's @file
      if (textBeforeCursor.substring(lastAtIndex).startsWith('@file')) {
        commandType = 'file'
        commandStart = lastAtIndex
      }
    } else if (lastSlashIndex !== -1) {
      // Check if it's a tool command (starts with /)
      const textAfterSlash = textBeforeCursor.substring(lastSlashIndex)
      if (!textAfterSlash.includes(' ') || textAfterSlash.endsWith(' ')) {
        commandType = 'tool'
        commandStart = lastSlashIndex
      }
    }
    
    if (commandType === 'file') {
      handleFileCommand(textBeforeCursor, commandStart)
    } else if (commandType === 'tool') {
      handleToolCommand(textBeforeCursor, commandStart)
    } else {
      // No active command, hide both autocompletes
      setShowToolAutocomplete(false)
      setShowFileAutocomplete(false)
    }
  }
  
  const handleToolCommand = (textBeforeCursor, commandStart) => {
    const commandText = textBeforeCursor.substring(commandStart + 1) // Remove the /
    const spaceIndex = commandText.indexOf(' ')
    const query = (spaceIndex === -1 ? commandText : commandText.substring(0, spaceIndex)).toLowerCase()
    const availableTools = getAllAvailableTools()
    
    // Show autocomplete if we're still typing the command
    const showAutocomplete = spaceIndex === -1 || commandText.length === spaceIndex + 1
    
    if (showAutocomplete) {
      if (query === '') {
        setFilteredTools(availableTools)
        setShowToolAutocomplete(true)
        setSelectedToolIndex(0)
      } else {
        const filtered = availableTools.filter(tool => 
          tool.name.toLowerCase().includes(query) ||
          tool.server.toLowerCase().includes(query)
        )
        
        if (filtered.length > 0) {
          setFilteredTools(filtered)
          setShowToolAutocomplete(true)
          setSelectedToolIndex(0)
        } else {
          setShowToolAutocomplete(false)
        }
      }
    } else {
      setShowToolAutocomplete(false)
    }
    
    // Hide file autocomplete
    setShowFileAutocomplete(false)
  }
  
  const handleFileCommand = (textBeforeCursor, commandStart) => {
    const commandText = textBeforeCursor.substring(commandStart) // Include the @
    
    // Check if we have @file followed by space or partial filename
    if (commandText.startsWith('@file')) {
      const afterFile = commandText.substring(5) // Remove '@file'
      const query = afterFile.trim().toLowerCase()
      const availableFiles = getAllAvailableFiles()
      
      if (afterFile.startsWith(' ') || afterFile === '') {
        // Show file autocomplete
        if (query === '') {
          setFilteredFiles(availableFiles)
          setShowFileAutocomplete(true)
          setSelectedFileIndex(0)
        } else {
          const filtered = availableFiles.filter(file => 
            file.filename.toLowerCase().includes(query)
          )
          
          if (filtered.length > 0) {
            setFilteredFiles(filtered)
            setShowFileAutocomplete(true)
            setSelectedFileIndex(0)
          } else {
            setShowFileAutocomplete(false)
          }
        }
      } else {
        setShowFileAutocomplete(false)
      }
    } else {
      setShowFileAutocomplete(false)
    }
    
    // Hide tool autocomplete
    setShowToolAutocomplete(false)
  }

  // Check if input contains a slash command
  const hasSlashCommand = inputValue.startsWith('/') && inputValue.includes(' ')
  
  // Check if input contains @file references
  const hasFileReference = inputValue.includes('@file ')

  // Handle tool selection from autocomplete
  const selectTool = (tool) => {
    // Enable the tool if not already selected
    if (!selectedTools.has(tool.key)) {
      toggleTool(tool.key)
    }
    
    // Enable required tool call
    setToolChoiceRequired(true)
    
    // Replace the slash command with the tool name and add a space
    setInputValue(`/${tool.name} `)
    setShowToolAutocomplete(false)
    
    // Focus back to textarea
    if (textareaRef.current) {
      textareaRef.current.focus()
    }
  }

  // Handle file selection from autocomplete
  const selectFile = (file) => {
    const cursorPosition = textareaRef.current?.selectionStart || inputValue.length
    const textBeforeCursor = inputValue.substring(0, cursorPosition)
    const textAfterCursor = inputValue.substring(cursorPosition)
    
    // Find the @file command position
    const lastAtIndex = textBeforeCursor.lastIndexOf('@file')
    if (lastAtIndex !== -1) {
      // Replace @file... with @file filename
      const beforeCommand = textBeforeCursor.substring(0, lastAtIndex)
      const newValue = `${beforeCommand}@file ${file.filename}${textAfterCursor}`
      setInputValue(newValue)
      
      // Position cursor after the inserted filename
      const newCursorPosition = beforeCommand.length + `@file ${file.filename}`.length
      setTimeout(() => {
        if (textareaRef.current) {
          textareaRef.current.setSelectionRange(newCursorPosition, newCursorPosition)
        }
      }, 0)
    }
    
    setShowFileAutocomplete(false)
    
    // Focus back to textarea
    if (textareaRef.current) {
      textareaRef.current.focus()
    }
  }

  const handleFileUpload = (e) => {
    const files = Array.from(e.target.files)
    files.forEach(file => {
      const reader = new FileReader()
      reader.onload = (e) => {
        const base64Data = e.target.result.split(',')[1] // Remove data URL prefix
        setUploadedFiles(prev => ({
          ...prev,
          [file.name]: base64Data
        }))
      }
      reader.readAsDataURL(file)
    })
    // Reset file input
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const removeFile = (filename) => {
    setUploadedFiles(prev => {
      const newFiles = { ...prev }
      delete newFiles[filename]
      return newFiles
    })
  }

  const triggerFileUpload = () => {
    fileInputRef.current?.click()
  }

  const canSend = inputValue.trim().length > 0 && currentModel && isConnected
  const [agentAnswer, setAgentAnswer] = useState('')

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
      {/* Welcome Screen */}
      {isWelcomeVisible && <WelcomeScreen />}

      {/* Messages */}
      <main
        ref={messagesRef}
        className="flex-1 overflow-y-auto custom-scrollbar p-4 space-y-4 min-h-0"
      >
        {messages.map((message, index) => (
          <Message
            key={`${index}-${message.role}-${message.content?.substring(0, 20)}`}
            message={message}
          />
        ))}
        {agentModeEnabled && agentPendingQuestion && (
          <div className="flex items-start gap-3 w-full">
            <div className="w-8 h-8 rounded-full bg-purple-600 flex items-center justify-center text-white text-sm font-medium flex-shrink-0">
              A
            </div>
            <div className="w-full bg-gray-800 rounded-lg p-4 border border-purple-700">
              <div className="text-sm font-medium text-purple-300 mb-2">Agent needs your input</div>
              <div className="text-gray-200 mb-3">{agentPendingQuestion}</div>
              <div className="flex gap-2">
                <input
                  value={agentAnswer}
                  onChange={(e) => setAgentAnswer(e.target.value)}
                  placeholder="Type your answer..."
                  className="flex-1 px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-gray-200"
                />
                <button
                  onClick={() => { if (agentAnswer.trim()) { answerAgentQuestion(agentAnswer.trim()); setAgentAnswer(''); setAgentPendingQuestion(null) } }}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg"
                >
                  Send
                </button>
              </div>
            </div>
          </div>
        )}
        {isThinking && (
          <div className="flex items-start gap-3 w-full">
            <div className="w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center text-white text-sm font-medium flex-shrink-0">
              A
            </div>
            <div className="w-full bg-gray-800 rounded-lg p-4">
              <div className="text-sm font-medium text-gray-300 mb-2">Chat UI</div>
              <div className="flex items-center gap-2 text-gray-400">
                <svg className="w-4 h-4 spinner" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span>Thinking...</span>
              </div>
            </div>
          </div>
        )}
        {/* Sentinel for auto-scroll */}
        <div ref={endRef} />
      </main>

      {/* Input Area */}
  <footer 
        className="p-4 border-t border-gray-700 flex-shrink-0"
      >
        <div className="max-w-4xl mx-auto">
          {/* Enabled Tools Indicator */}
          <EnabledToolsIndicator />
          {/* Uploaded Files Display */}
          {Object.keys(uploadedFiles).length > 0 && (
            <div className="mb-3 p-3 bg-gray-800 rounded-lg border border-gray-600">
              <div className="text-sm text-gray-300 mb-2">Uploaded Files:</div>
              <div className="flex flex-wrap gap-2">
                {Object.keys(uploadedFiles).map(filename => (
                  <div key={filename} className="flex items-center gap-2 bg-gray-700 px-3 py-1 rounded-full text-sm">
                    <span className="text-gray-200">{filename}</span>
                    <button
                      onClick={() => removeFile(filename)}
                      className="text-gray-400 hover:text-red-400 transition-colors"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
          
          <form onSubmit={handleSubmit} className="flex gap-3">
            <button
              type="button"
              onClick={triggerFileUpload}
              className="px-3 py-3 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-lg flex items-center justify-center transition-colors flex-shrink-0"
              title="Upload files"
            >
              <Paperclip className="w-5 h-5" />
            </button>
            {agentModeEnabled && (isThinking || agentPendingQuestion) && (
              <button
                type="button"
                onClick={stopAgent}
                className="px-3 py-3 bg-red-700 hover:bg-red-600 text-white rounded-lg flex items-center justify-center transition-colors flex-shrink-0"
                title="Stop agent"
              >
                <Square className="w-5 h-5" />
              </button>
            )}
            <div className="flex-1 relative">
              <textarea
                ref={textareaRef}
                value={inputValue}
                onChange={handleInputChange}
                onKeyDown={handleKeyDown}
                placeholder="Type your message... (Use /tool for tools, @file for files)"
                rows={1}
                className={`w-full px-4 py-3 bg-gray-800 rounded-lg text-gray-200 placeholder-gray-400 resize-none focus:outline-none focus:ring-2 focus:border-transparent ${
                  hasSlashCommand 
                    ? 'border-2 border-yellow-500 focus:ring-yellow-500 bg-yellow-900/10' 
                    : hasFileReference
                    ? 'border-2 border-green-500 focus:ring-green-500 bg-green-900/10'
                    : 'border border-gray-600 focus:ring-blue-500'
                }`}
                style={{ minHeight: '48px', maxHeight: '128px' }}
              />
              
              {/* Tool Autocomplete Dropdown */}
              {showToolAutocomplete && filteredTools.length > 0 && (
                <div className="absolute bottom-full left-0 right-0 mb-2 bg-gray-800 border border-gray-600 rounded-lg shadow-lg max-h-64 overflow-y-auto z-50">
                  <div className="p-2 text-xs text-gray-400 bg-gray-700 border-b border-gray-600">
                    Use ↑↓ to navigate, Enter to select, Esc to cancel
                  </div>
                  {filteredTools.map((tool, index) => (
                    <div
                      key={tool.key}
                      onClick={() => selectTool(tool)}
                      className={`px-3 py-2 cursor-pointer transition-colors border-b border-gray-700 last:border-b-0 ${
                        index === selectedToolIndex 
                          ? 'bg-blue-600 text-white' 
                          : 'hover:bg-gray-700 text-gray-200'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span className="font-black text-white">/{tool.name}</span>
                        <span className="text-xs text-gray-400">from {tool.server}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              
              {/* File Autocomplete Dropdown */}
              {showFileAutocomplete && filteredFiles.length > 0 && (
                <div className="absolute bottom-full left-0 right-0 mb-2 bg-gray-800 border border-gray-600 rounded-lg shadow-lg max-h-64 overflow-y-auto z-50">
                  <div className="p-2 text-xs text-gray-400 bg-gray-700 border-b border-gray-600">
                    Files available in your session - Use ↑↓ to navigate, Enter to select, Esc to cancel
                  </div>
                  {filteredFiles.map((file, index) => (
                    <div
                      key={file.filename}
                      onClick={() => selectFile(file)}
                      className={`px-3 py-2 cursor-pointer transition-colors border-b border-gray-700 last:border-b-0 ${
                        index === selectedFileIndex 
                          ? 'bg-green-600 text-white' 
                          : 'hover:bg-gray-700 text-gray-200'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-white">@file {file.filename}</span>
                          <span className="text-xs px-2 py-1 rounded bg-gray-600 text-gray-300">{file.type}</span>
                        </div>
                        <div className="flex items-center gap-2 text-xs text-gray-400">
                          <span>{file.source === 'tool' ? '🔧 generated' : '📤 uploaded'}</span>
                          <span>{(file.size / 1024).toFixed(1)}KB</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <button
              type="submit"
              disabled={!canSend}
              className={`px-4 py-3 rounded-lg flex items-center justify-center transition-colors flex-shrink-0 ${
                canSend 
                  ? 'bg-blue-600 hover:bg-blue-700 text-white' 
                  : 'bg-gray-700 text-gray-400 cursor-not-allowed'
              }`}
            >
              <Send className="w-5 h-5" />
            </button>
          </form>
          
          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            onChange={handleFileUpload}
            className="hidden"
            accept=".pdf,.txt,.doc,.docx,.jpg,.jpeg,.png,.gif,.csv,.xlsx,.xls,.json,.md,.log"
          />
          
          <div className="flex items-center justify-between mt-2 text-xs text-gray-400">
            <span>Press Shift + Enter for new line</span>
            {Object.keys(uploadedFiles).length > 0 && (
              <span>{Object.keys(uploadedFiles).length} file(s) uploaded</span>
            )}
          </div>
        </div>
      </footer>
    </div>
  )
}

export default ChatArea
