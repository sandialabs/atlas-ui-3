import { useState, useRef, useEffect, useCallback } from 'react'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'
import { Send, Paperclip, X, Square, FileText, FileSearch, FileX, Search } from 'lucide-react'
import Message from './Message'
import WelcomeScreen from './WelcomeScreen'
import EnabledToolsIndicator from './EnabledToolsIndicator'
import PromptSelector from './PromptSelector'

const ChatArea = () => {
  const [inputValue, setInputValue] = useState('')
  const [isMobile, setIsMobile] = useState(false)
  // uploadedFiles: { filename: { content: base64, extractMode: "full"|"preview"|"none" } }
  const [uploadedFiles, setUploadedFiles] = useState({})
  const [globalExtractMode, setGlobalExtractMode] = useState('full')
  const [showToolAutocomplete, setShowToolAutocomplete] = useState(false)
  const [filteredTools, setFilteredTools] = useState([])
  const [selectedToolIndex, setSelectedToolIndex] = useState(0)
  const [showFileAutocomplete, setShowFileAutocomplete] = useState(false)
  const [filteredFiles, setFilteredFiles] = useState([])
  const [selectedFileIndex, setSelectedFileIndex] = useState(0)
  const [isDragOver, setIsDragOver] = useState(false)
  const textareaRef = useRef(null)
  const messagesRef = useRef(null)
  const endRef = useRef(null)
  const userScrolledRef = useRef(false)
  const prevMessageCountRef = useRef(0)
  const fileInputRef = useRef(null)
  const dragCounterRef = useRef(0)
  
  const {
    messages,
    isWelcomeVisible,
    isThinking,
    isSynthesizing,
    sendChatMessage,
    currentModel,
    tools,
    selectedTools,
    toggleTool,
    setToolChoiceRequired,
    sessionFiles,
    agentModeEnabled,
    agentPendingQuestion,
    setAgentPendingQuestion,
    stopAgent,
    answerAgentQuestion,
    fileExtraction,
    ragEnabled,
    toggleRagEnabled,
    features,
    appName
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
  }, [messages, isThinking, isSynthesizing, scrollToBottom])

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
    let message = inputValue.trim()
    if (!message || !currentModel || !isConnected) return

    // Check for /search command - strip prefix and force RAG
    let forceRag = false
    if (message.toLowerCase().startsWith('/search ')) {
      message = message.substring(8).trim() // Remove '/search ' prefix
      forceRag = true
      if (!message) return
    }

    try {
      // Process @file references in the message
      const processedFiles = await processFileReferences(message)
      const allFiles = { ...uploadedFiles, ...processedFiles }

      sendChatMessage(message, allFiles, forceRag)
      setInputValue('')

      // Reset textarea height
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto'
      }
    } catch (error) {
      console.error('Error in handleSubmit:', error)
      // Still try to send the message without file processing
      sendChatMessage(message, uploadedFiles, forceRag)
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
          } else {
            fileRefs[filename] = `[Error loading file: ${filename}]`
          }
        } catch (error) {
          console.error(`Error fetching @file ${filename}:`, error)
          fileRefs[filename] = `[Error loading file: ${filename}]`
        }
      } else {
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

  // Get all available tools as flat list (including special commands)
  const getAllAvailableTools = () => {
    const allTools = []

    // Add /search command if RAG is enabled
    if (features?.rag) {
      allTools.push({
        key: '_special_search',
        name: 'search',
        server: 'RAG',
        description: 'Search across all RAG data sources',
        isSpecialCommand: true
      })
    }

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

  // Check if input contains a slash command (but not /search)
  const hasSlashCommand = inputValue.startsWith('/') && inputValue.includes(' ') && !inputValue.toLowerCase().startsWith('/search ')

  // Check if input is a /search command
  const hasSearchCommand = inputValue.toLowerCase().startsWith('/search ')

  // Check if input contains @file references
  const hasFileReference = inputValue.includes('@file ')

  // Handle tool selection from autocomplete
  const selectTool = (tool) => {
    // Handle special commands differently
    if (tool.isSpecialCommand) {
      // For special commands like /search, just set the input value
      setInputValue(`/${tool.name} `)
      setShowToolAutocomplete(false)
      if (textareaRef.current) {
        textareaRef.current.focus()
      }
      return
    }

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

  // Check if a file extension supports extraction
  const canExtractFile = useCallback((filename) => {
    if (!fileExtraction?.enabled || !filename) return false
    const lastDotIndex = filename.lastIndexOf('.')
    // No extension, hidden file (dot at start), or trailing dot
    if (lastDotIndex <= 0 || lastDotIndex === filename.length - 1) {
      return false
    }
    const ext = filename.slice(lastDotIndex).toLowerCase()
    return fileExtraction.supported_extensions?.includes(ext) || false
  }, [fileExtraction])

  // 3-mode extraction cycle helpers
  const EXTRACT_MODES = ['full', 'preview', 'none']
  const nextExtractMode = (mode) => EXTRACT_MODES[(EXTRACT_MODES.indexOf(mode) + 1) % EXTRACT_MODES.length]
  const extractModeIcon = (mode) => {
    if (mode === 'full') return FileText
    if (mode === 'preview') return FileSearch
    return FileX
  }
  const extractModeLabel = (mode) => {
    if (mode === 'full') return 'Full Text'
    if (mode === 'preview') return 'Preview'
    return 'No Extract'
  }

  const sanitizeFilename = (name) => name.replace(/\s+/g, '_')

  const handleFileUpload = (e) => {
    const files = Array.from(e.target.files)
    files.forEach(file => {
      const reader = new FileReader()
      reader.onload = (e) => {
        const base64Data = e.target.result.split(',')[1] // Remove data URL prefix
        const safeName = sanitizeFilename(file.name)
        // Determine extraction mode for this file
        const mode = canExtractFile(safeName) ? globalExtractMode : 'none'
        setUploadedFiles(prev => ({
          ...prev,
          [safeName]: {
            content: base64Data,
            extractMode: mode
          }
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

  // Cycle extraction mode for a specific file
  const toggleFileExtraction = (filename) => {
    setUploadedFiles(prev => {
      if (!prev[filename]) return prev
      return {
        ...prev,
        [filename]: {
          ...prev[filename],
          extractMode: nextExtractMode(prev[filename].extractMode || 'none')
        }
      }
    })
  }

  const triggerFileUpload = () => {
    fileInputRef.current?.click()
  }

  const handleDragEnter = (e) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current++
    if (e.dataTransfer.items && e.dataTransfer.items.length > 0) {
      setIsDragOver(true)
    }
  }

  const handleDragLeave = (e) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current--
    if (dragCounterRef.current === 0) {
      setIsDragOver(false)
    }
  }

  const handleDragOver = (e) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleDrop = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(false)
    dragCounterRef.current = 0

    const files = Array.from(e.dataTransfer.files)
    if (files.length === 0) return

    files.forEach(file => {
      const reader = new FileReader()
      reader.onload = (event) => {
        const base64Data = event.target.result.split(',')[1]
        const safeName = sanitizeFilename(file.name)
        // Determine extraction mode for this file
        const mode = canExtractFile(safeName) ? globalExtractMode : 'none'
        setUploadedFiles(prev => ({
          ...prev,
          [safeName]: {
            content: base64Data,
            extractMode: mode
          }
        }))
      }
      reader.readAsDataURL(file)
    })
  }

  const canSend = inputValue.trim().length > 0 && currentModel && isConnected
  const [agentAnswer, setAgentAnswer] = useState('')

  const showPoweredByAtlas =
    import.meta.env.VITE_FEATURE_POWERED_BY_ATLAS === 'true'

  return (
    <div 
      className="flex flex-col flex-1 min-h-0 overflow-hidden relative"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* Drag and Drop Overlay */}
      {isDragOver && (
        <div 
          className="absolute inset-0 bg-blue-500/20 border-2 border-dashed border-blue-500 z-50 flex items-center justify-center"
          data-testid="drag-overlay"
        >
          <div className="bg-gray-800 rounded-lg p-6 text-center shadow-xl">
            <Paperclip className="w-12 h-12 text-blue-400 mx-auto mb-3" />
            <p className="text-lg font-medium text-gray-200">Drop files to attach</p>
            <p className="text-sm text-gray-400 mt-1">Files will be added to your message</p>
          </div>
        </div>
      )}
      
      {/* Welcome Screen */}
      {isWelcomeVisible && <WelcomeScreen />}
      
      {/* Powered by ATLAS logo - only shown on welcome screen */}
      {isWelcomeVisible && showPoweredByAtlas && (
        <div className="absolute bottom-32 left-0 right-0 sm:bottom-36 md:bottom-40 z-10 px-4">
          <div className="max-w-4xl mx-auto flex justify-end">
            <img
              src="/sandia-powered-by-atlas.png"
              alt="Powered By SNL ATLAS Logo"
              className="w-36 sm:w-44 md:w-52 lg:w-60 object-contain"
              onError={(e) => {
                e.target.style.display = 'none'
              }}
            />
          </div>
        </div>
      )}

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
              <div className="text-sm font-medium text-gray-300 mb-2">{appName}</div>
              <div className="flex items-center gap-2 text-gray-400">
                <svg className="w-4 h-4 spinner" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span>{(() => {
                  if (isSynthesizing) {
                    return 'Processing tool results...'
                  }
                  const lastMsg = messages[messages.length - 1]
                  if (lastMsg && lastMsg.type === 'tool_call' && (lastMsg.status === 'calling' || lastMsg.status === 'in_progress')) {
                    return 'Running tool...'
                  }
                  if (lastMsg && lastMsg.type === 'tool_call' && lastMsg.status === 'completed') {
                    return 'Processing tool results...'
                  }
                  return 'Thinking...'
                })()}</span>
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
              <div className="flex items-center justify-between mb-2">
                <div className="text-sm text-gray-300">Uploaded Files:</div>
                {/* Global extraction mode toggle - only show if feature is enabled */}
                {fileExtraction?.enabled && (() => {
                  const GlobalIcon = extractModeIcon(globalExtractMode)
                  const modeColors = {
                    full: 'bg-green-600/20 text-green-400 hover:bg-green-600/30',
                    preview: 'bg-blue-600/20 text-blue-400 hover:bg-blue-600/30',
                    none: 'bg-gray-700 text-gray-400 hover:bg-gray-600'
                  }
                  return (
                    <button
                      onClick={() => setGlobalExtractMode(nextExtractMode(globalExtractMode))}
                      className={`flex items-center gap-1 px-2 py-1 text-xs rounded transition-colors ${modeColors[globalExtractMode]}`}
                      title={`Extraction mode for new files: ${extractModeLabel(globalExtractMode)}`}
                    >
                      <GlobalIcon className="w-3 h-3" />
                      <span>{extractModeLabel(globalExtractMode)}</span>
                    </button>
                  )
                })()}
              </div>
              <div className="flex flex-wrap gap-2">
                {Object.entries(uploadedFiles).map(([filename, fileData]) => {
                  const supportsExtraction = canExtractFile(filename)
                  const mode = fileData.extractMode || 'none'
                  const borderColors = {
                    full: 'bg-green-900/30 border border-green-600/50',
                    preview: 'bg-blue-900/30 border border-blue-600/50',
                    none: 'bg-gray-700'
                  }
                  const iconColors = {
                    full: 'text-green-400 hover:text-green-300',
                    preview: 'text-blue-400 hover:text-blue-300',
                    none: 'text-gray-500 hover:text-gray-400'
                  }
                  const ModeIcon = extractModeIcon(mode)
                  return (
                    <div
                      key={filename}
                      className={`flex items-center gap-2 px-3 py-1 rounded-full text-sm ${
                        supportsExtraction ? borderColors[mode] : 'bg-gray-700'
                      }`}
                    >
                      {/* Extraction mode toggle for individual file */}
                      {fileExtraction?.enabled && supportsExtraction && (
                        <button
                          onClick={() => toggleFileExtraction(filename)}
                          className={`transition-colors ${iconColors[mode]}`}
                          title={`Extraction: ${extractModeLabel(mode)} - click to cycle`}
                        >
                          <ModeIcon className="w-3 h-3" />
                        </button>
                      )}
                      <span className="text-gray-200">{filename}</span>
                      <button
                        onClick={() => removeFile(filename)}
                        className="text-gray-400 hover:text-red-400 transition-colors"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
          
          <form onSubmit={handleSubmit} className="flex flex-col gap-2">
            <div className="flex gap-3">
              <button
                type="button"
                onClick={triggerFileUpload}
                className="px-3 py-3 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-lg flex items-center justify-center transition-colors flex-shrink-0"
                title="Upload files"
              >
                <Paperclip className="w-5 h-5" />
              </button>
              {/* RAG Toggle Button - only show if RAG feature is enabled */}
              {features?.rag && (
                <button
                  type="button"
                  onClick={toggleRagEnabled}
                  className={`px-3 py-3 rounded-lg flex items-center justify-center transition-colors flex-shrink-0 ${
                    ragEnabled || hasSearchCommand
                      ? 'bg-green-600 hover:bg-green-700 text-white'
                      : 'bg-gray-700 hover:bg-gray-600 text-gray-300'
                  }`}
                  title={ragEnabled ? 'RAG enabled - click to disable' : hasSearchCommand ? 'RAG active for this search' : 'RAG disabled - click to enable'}
                >
                  <Search className="w-5 h-5" />
                </button>
              )}
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
                placeholder={isMobile ? "Type a message..." : "Type a message... (/ or @ for help)"}
                rows={1}
                className={`w-full px-4 py-3 bg-gray-800 rounded-lg text-gray-200 placeholder-gray-400 resize-none focus:outline-none focus:ring-2 focus:border-transparent ${
                  hasSearchCommand
                    ? 'border-2 border-green-500 focus:ring-green-500 bg-green-900/10'
                    : hasSlashCommand
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
                          <span>{file.source === 'tool' ? 'generated' : 'uploaded'}</span>
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
          </div>
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
            <div className="flex items-center gap-3">
              <PromptSelector />
              <span>Press Shift + Enter for new line</span>
            </div>
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
