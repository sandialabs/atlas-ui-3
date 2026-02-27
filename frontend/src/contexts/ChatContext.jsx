// Slim ChatContext (clean refactor)
import { createContext, useContext, useEffect, useState, useCallback, useRef } from 'react'
import { useWS } from './WSContext'
import { useChatConfig } from '../hooks/chat/useChatConfig'
import { useSelections } from '../hooks/chat/useSelections'
import { useAgentMode } from '../hooks/chat/useAgentMode'
import { useMessages } from '../hooks/chat/useMessages'
import { useFiles } from '../hooks/chat/useFiles'
import { useSettings } from '../hooks/useSettings'
import { usePersistentState } from '../hooks/chat/usePersistentState'
import { createWebSocketHandler } from '../handlers/chat/websocketHandlers'
import { saveConversation as saveLocalConv } from '../utils/localConversationDB'

// Save mode constants: 'none' (incognito), 'local' (browser), 'server' (backend DB)
const SAVE_MODES = ['none', 'local', 'server']

// Generate cryptographically secure random string
const generateSecureRandomString = (length = 9) => {
  const array = new Uint8Array(length)
  crypto.getRandomValues(array)
  return Array.from(array, byte => byte.toString(36)).join('').slice(0, length)
}

const ChatContext = createContext(null)

// eslint-disable-next-line react-refresh/only-export-components
export const useChat = () => {
	const ctx = useContext(ChatContext)
	if (!ctx) throw new Error('useChat must be used within a ChatProvider')
	return ctx
}

export const ChatProvider = ({ children }) => {
	// State slices
	const config = useChatConfig()
	const selections = useSelections()
	// Pass through dynamic availability from backend config
		const agent = useAgentMode(config.agentModeAvailable)
	const files = useFiles()
	const { messages, addMessage, bulkAdd, mapMessages, resetMessages, streamToken, streamEnd } = useMessages()
	const { settings, updateSettings } = useSettings()

	const [isWelcomeVisible, setIsWelcomeVisible] = useState(true)
	const [isThinking, setIsThinking] = useState(false)
	const [isSynthesizing, setIsSynthesizing] = useState(false)
	const [sessionId, setSessionId] = useState(null)
	const [attachments, setAttachments] = useState(new Set())
	const [, setPendingFileEvents] = useState(new Map())
	const [pendingElicitation, setPendingElicitation] = useState(null)

	// Chat history: 3-state save mode persists across refreshes via localStorage
	// 'none' = incognito (nothing saved), 'local' = browser IndexedDB, 'server' = backend DB
	const [saveMode, setSaveMode] = usePersistentState('chatui-save-mode', 'server')
	const [activeConversationId, setActiveConversationId] = useState(null)
	const localSaveTimerRef = useRef(null)

	// Method to add a file to attachments
	const addAttachment = useCallback((fileId) => {
		setAttachments(prev => new Set([...prev, fileId]))
	}, [])

	// Methods to manage pending file events
	const addPendingFileEvent = useCallback((fileKey, eventId) => {
		setPendingFileEvents(prev => new Map(prev.set(fileKey, eventId)))
	}, [])

	const resolvePendingFileEvent = useCallback((fileKey, newSubtype, newText) => {
		setPendingFileEvents(prev => {
			const eventId = prev.get(fileKey)
			if (eventId) {
				// Update the message in-place
				mapMessages(messages => messages.map(msg =>
					msg.id === eventId
						? { ...msg, subtype: newSubtype, text: newText }
						: msg
				))
				// Remove from pending
				const next = new Map(prev)
				next.delete(fileKey)
				return next
			}
			return prev
		})
	}, [mapMessages])

		const { sendMessage, addMessageHandler } = useWS()
	const { currentModel } = config
	const { selectedTools, selectedPrompts, activePrompts, selectedDataSources, ragEnabled, toggleRagEnabled } = selections

	const triggerFileDownload = useCallback((filename, base64Content) => {
		try {
			const bytes = atob(base64Content).split('').map(c => c.charCodeAt(0))
			const blob = new Blob([new Uint8Array(bytes)], { type: 'application/octet-stream' })
			const url = URL.createObjectURL(blob)
			const a = document.createElement('a')
			a.href = url; a.download = filename
			document.body.appendChild(a); a.click(); document.body.removeChild(a)
			setTimeout(() => URL.revokeObjectURL(url), 100)
		} catch (e) { console.error('File download error', e) }
	}, [])

	useEffect(() => {
				const handler = createWebSocketHandler({
			addMessage,
			mapMessages,
			setIsThinking,
			setIsSynthesizing,
				setCurrentAgentStep: agent.setCurrentAgentStep,
					setAgentPendingQuestion: agent.setAgentPendingQuestion,
			setCanvasContent: files.setCanvasContent,
			setCanvasFiles: files.setCanvasFiles,
			setCurrentCanvasFileIndex: files.setCurrentCanvasFileIndex,
			setCustomUIContent: files.setCustomUIContent,
			setIsCanvasOpen: config.setIsCanvasOpen,
			setSessionFiles: files.setSessionFiles,
			getFileType: files.getFileType,
			triggerFileDownload,
			addAttachment,
			resolvePendingFileEvent,
			setPendingElicitation,
			setActiveConversationId,
			streamToken,
			streamEnd,
		})
		return addMessageHandler(handler)
	// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [addMessageHandler, addMessage, mapMessages, agent.setCurrentAgentStep, files, triggerFileDownload, addAttachment, addPendingFileEvent, resolvePendingFileEvent, setActiveConversationId, streamToken, streamEnd])

	// Validate persisted data sources against current config and remove stale ones
	useEffect(() => {
		if (!config.ragServers || config.ragServers.length === 0) return

		// Build set of valid data source IDs from current config
		const validSourceIds = new Set(
			config.ragServers.flatMap(server =>
				server.sources.map(source => `${server.server}:${source.id}`)
			)
		)

		// Find any selected sources that no longer exist in config
		const staleSourceIds = [...selectedDataSources].filter(id => !validSourceIds.has(id))

		if (staleSourceIds.length > 0) {
			// Remove stale data sources that no longer exist in config
			// Remove stale sources by keeping only valid ones
			const validSelections = [...selectedDataSources].filter(id => validSourceIds.has(id))
			selections.clearDataSources()
			if (validSelections.length > 0) {
				selections.addDataSources(validSelections)
			}
		}
	// Only run when ragServers config changes, not on every selectedDataSources change
	// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [config.ragServers])

	// Validate persisted tool selections against current config and remove stale ones
	useEffect(() => {
		if (!config.tools || config.tools.length === 0) return

		// Build set of valid tool keys from current config
		const validToolKeys = new Set(
			config.tools.flatMap(server =>
				server.tools.map(tool => `${server.server}_${tool}`)
			)
		)

		// Find any selected tools that no longer exist in config
		const staleToolKeys = [...selectedTools].filter(key => !validToolKeys.has(key))

		if (staleToolKeys.length > 0) {
			// Remove stale tools that no longer exist in config
			selections.removeTools(staleToolKeys)
		}
	// Only run when tools config changes, not on every selectedTools change
	// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [config.tools])

	// Validate persisted prompt selections against current config and remove stale ones
	useEffect(() => {
		if (!config.prompts || config.prompts.length === 0) return

		// Build set of valid prompt keys from current config
		const validPromptKeys = new Set(
			config.prompts.flatMap(server =>
				server.prompts.map(p => `${server.server}_${p.name}`)
			)
		)

		// Find any selected prompts that no longer exist in config
		const stalePromptKeys = [...selectedPrompts].filter(key => !validPromptKeys.has(key))

		if (stalePromptKeys.length > 0) {
			// Remove stale prompts that no longer exist in config
			selections.removePrompts(stalePromptKeys)
		}

		// Clear active prompt if it no longer exists in config
		if (selections.activePromptKey && !validPromptKeys.has(selections.activePromptKey)) {
			// Clear stale active prompt that no longer exists in config
			selections.clearActivePrompt()
		}
	// Only run when prompts config changes, not on every selectedPrompts change
	// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [config.prompts])

	const selectAllServerTools = useCallback((server) => {
		const group = config.tools.find(t => t.server === server); if (!group) return
		group.tools.forEach(tool => { const key = `${server}_${tool}`; if (!selectedTools.has(key)) selections.toggleTool(key) })
	}, [config.tools, selectedTools, selections])

	const deselectAllServerTools = useCallback((server) => {
		const group = config.tools.find(t => t.server === server); if (!group) return
		group.tools.forEach(tool => { const key = `${server}_${tool}`; if (selectedTools.has(key)) selections.toggleTool(key) })
	}, [config.tools, selectedTools, selections])

	const selectAllServerPrompts = useCallback((server) => {
		const group = config.prompts.find(p => p.server === server); if (!group) return
		group.prompts.forEach(p => { const key = `${server}_${p.name}`; if (!selectedPrompts.has(key)) selections.togglePrompt(key) })
	}, [config.prompts, selectedPrompts, selections])

	const deselectAllServerPrompts = useCallback((server) => {
		const group = config.prompts.find(p => p.server === server); if (!group) return
		group.prompts.forEach(p => { const key = `${server}_${p.name}`; if (selectedPrompts.has(key)) selections.togglePrompt(key) })
	}, [config.prompts, selectedPrompts, selections])

	// Flatten ragServers into a list of all available data source IDs (qualified with server name)
	const getAllRagSourceIds = useCallback(() => {
		return config.ragServers.flatMap(server =>
			server.sources.map(source => `${server.server}:${source.id}`)
		)
	}, [config.ragServers])

	const sendChatMessage = useCallback((content, extraFiles = {}, forceRag = false) => {
		if (!content.trim() || !currentModel) return
		if (isWelcomeVisible) setIsWelcomeVisible(false)
		addMessage({ role: 'user', content, timestamp: new Date().toISOString() })
		setIsThinking(true)
		setIsSynthesizing(false)
		const tagged = files.getTaggedFilesContent()

		// Determine data sources to send:
		// RAG is only invoked when explicitly activated via the search button (ragEnabled)
		// or the /search command (forceRag). Data source selection alone just marks
		// availability -- it does not trigger RAG.
		const ragActivated = forceRag || ragEnabled
		const hasSelectedSources = selectedDataSources.size > 0
		const dataSourcesToSend = ragActivated
			? (hasSelectedSources ? [...selectedDataSources] : getAllRagSourceIds())
			: []

		sendMessage({
			type: 'chat',
			content,
			model: currentModel,
			selected_tools: [...selectedTools],
			selected_prompts: activePrompts,
			selected_data_sources: dataSourcesToSend,
			tool_choice_required: selections.toolChoiceRequired,
			user: config.user,
			files: { ...extraFiles, ...tagged },
			agent_mode: agent.agentModeEnabled,
			agent_max_steps: settings.maxIterations || agent.agentMaxSteps,
			temperature: settings.llmTemperature || 0.7,
			agent_loop_strategy: undefined,
			compliance_level_filter: selections.complianceLevelFilter,
			save_mode: saveMode,
			// Backward compat: backend still checks incognito for older clients
			incognito: saveMode !== 'server',
			conversation_id: activeConversationId || undefined,
		})
	}, [addMessage, currentModel, selectedTools, activePrompts, selectedDataSources, ragEnabled, config, selections, agent, files, isWelcomeVisible, sendMessage, settings, getAllRagSourceIds, saveMode, activeConversationId])

	const clearChat = useCallback(() => {
		resetMessages()
		setIsWelcomeVisible(true)
		setActiveConversationId(null)
		files.setCanvasContent('')
		files.setCustomUIContent(null)
		files.setSessionFiles({ total_files: 0, files: [], categories: { code: [], image: [], data: [], document: [], other: [] } })

		// Notify backend to create a new session
		if (sendMessage) {
			sendMessage({ type: 'reset_session' })
		}
	}, [resetMessages, files, sendMessage])

	// Load a saved conversation from history into the chat view
	const loadSavedConversation = useCallback(async (conversationData) => {
		if (!conversationData || !conversationData.messages) return

		// Clear current state
		resetMessages()
		files.setCanvasContent('')
		files.setCustomUIContent(null)
		files.setSessionFiles({ total_files: 0, files: [], categories: { code: [], image: [], data: [], document: [], other: [] } })

		// Track the loaded conversation
		setActiveConversationId(conversationData.id)
		setIsWelcomeVisible(false)

		// Load messages into the chat view
		const loadedMessages = conversationData.messages.map(msg => ({
			role: msg.role,
			content: msg.content || '',
			timestamp: msg.timestamp,
			type: msg.message_type || 'chat',
			...(msg.metadata || {}),
		}))
		if (loadedMessages.length > 0) {
			bulkAdd(loadedMessages)
		}

		// Notify backend to restore this conversation's context
		// Sends the conversation_id and messages so the LLM has prior context
		if (sendMessage) {
			sendMessage({
				type: 'restore_conversation',
				conversation_id: conversationData.id,
				messages: conversationData.messages.map(msg => ({
					role: msg.role,
					content: msg.content || '',
				})),
			})
		}
	}, [resetMessages, files, sendMessage, bulkAdd])

	const downloadFile = useCallback((filename) => {
		if (!files.sessionFiles.files.find(f => f.filename === filename)) return
		sendMessage({ type: 'download_file', filename, user: config.user })
	}, [files.sessionFiles.files, sendMessage, config.user])

		// Agent controls
		const stopAgent = useCallback(() => {
			if (sendMessage) sendMessage({ type: 'agent_control', action: 'stop' })
		}, [sendMessage])

			const answerAgentQuestion = useCallback((content) => {
			if (!content || !content.trim()) return
				// Show immediately in UI
				addMessage({ role: 'user', content, timestamp: new Date().toISOString() })
				if (sendMessage) sendMessage({ type: 'agent_user_input', content })
			}, [sendMessage, addMessage])

	const deleteFile = useCallback((filename) => {
		if (!confirm(`Delete ${filename}?`)) return
		files.setSessionFiles(prev => {
			const newFiles = prev.files.filter(f => f.filename !== filename)
			const categories = {}
			Object.keys(prev.categories).forEach(cat => { categories[cat] = newFiles.filter(f => f.type === cat) })
			return { total_files: newFiles.length, files: newFiles, categories }
		})
	}, [files])

	const exportData = useCallback((asText) => {
		if (!messages.length) { alert('No chat history to download'); return }
		const ragEnabled = config.features?.rag
		const ragSourcesDisplay = ragEnabled
			? ([...selectedDataSources].join(', ') || 'None selected')
			: 'None (RAG disabled)'
		if (asText) {
			let text = `Chat Export - ${config.appName}\nDate: ${new Date().toLocaleString()}\nUser: ${config.user}\nModel: ${currentModel}\nSelected Tools: ${[...selectedTools].join(', ') || 'None'}\nSelected RAG Sources: ${ragSourcesDisplay}\nAgent Mode: ${agent.agentModeEnabled ? 'Enabled' : 'Disabled'}\n\n${'='.repeat(50)}\n\n`
			messages.forEach(m => { text += `${m.role.toUpperCase()}:\n${m.content}\n\n` })
			if (files.canvasContent) text += `${'='.repeat(50)}\nCANVAS CONTENT:\n${files.canvasContent}\n`
			const blob = new Blob([text], { type: 'text/plain' })
			const url = URL.createObjectURL(blob)
			const a = document.createElement('a')
			a.href = url
			const ts = new Date().toISOString().replace(/[:.]/g,'-').slice(0,19)
			a.download = `chat-export-${ts}.txt`
			document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url)
		} else {
			const data = {
				metadata: {
					exportDate: new Date().toISOString(),
					appName: config.appName,
					user: config.user,
					model: currentModel,
					selectedTools: [...selectedTools],
					ragEnabled: ragEnabled,
					selectedRagSources: ragEnabled ? [...selectedDataSources] : null,
					toolChoiceRequired: selections.toolChoiceRequired,
					agentModeEnabled: agent.agentModeEnabled,
					agentMaxSteps: agent.agentMaxSteps,
					messageCount: messages.length,
					exportVersion: '1.1'
				},
				conversation: messages,
				canvasContent: files.canvasContent || null
			}
			const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
			const url = URL.createObjectURL(blob)
			const a = document.createElement('a')
			a.href = url
			const ts = new Date().toISOString().replace(/[:.]/g,'-').slice(0,19)
			a.download = `chat-export-${ts}.json`
			document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url)
		}
	}, [messages, config.appName, config.user, config.features, currentModel, selectedTools, selectedDataSources, agent.agentModeEnabled, agent.agentMaxSteps, selections.toolChoiceRequired, files.canvasContent])

	const downloadChat = useCallback(() => exportData(false), [exportData])
	const downloadChatAsText = useCallback(() => exportData(true), [exportData])

	// Wrapper for setComplianceLevelFilter that clears incompatible selections
	const setComplianceLevelFilterWithCleanup = useCallback((newLevel) => {
		// If changing to a new compliance level (not clearing or setting to same)
		if (newLevel && newLevel !== selections.complianceLevelFilter) {
			// Clear tools that don't match the new compliance level
			const toolsToRemove = []
			selectedTools.forEach(toolKey => {
				const serverName = toolKey.split('_')[0]
				const server = config.tools.find(t => t.server === serverName)
				if (server && server.compliance_level && server.compliance_level !== newLevel) {
					toolsToRemove.push(toolKey)
				}
			})
			if (toolsToRemove.length > 0) {
				selections.removeTools(toolsToRemove)
			}

			// Clear prompts that don't match the new compliance level
			const promptsToRemove = []
			selectedPrompts.forEach(promptKey => {
				const serverName = promptKey.split('_')[0]
				const server = config.prompts.find(p => p.server === serverName)
				if (server && server.compliance_level && server.compliance_level !== newLevel) {
					promptsToRemove.push(promptKey)
				}
			})
			if (promptsToRemove.length > 0) {
				selections.removePrompts(promptsToRemove)
			}
		}
		
		// Set the new compliance level
		selections.setComplianceLevelFilter(newLevel)
	}, [selections, selectedTools, selectedPrompts, config.tools, config.prompts])

	// Flatten ragServers into a single list of data source objects for easier consumption
	const ragSources = config.ragServers.flatMap(server =>
		server.sources.map(source => ({
			...source,
			serverName: server.server,
			serverDisplayName: server.displayName,
			serverComplianceLevel: server.complianceLevel,
		}))
	)

	// ensureSession: ensures a session exists, returns sessionId once ready
	const ensureSession = useCallback(() => {
		return new Promise((resolve) => {
			if (sessionId) {
				resolve(sessionId)
				return
			}

			// Create a temporary session ID for frontend tracking
			const tempSessionId = `session_${Date.now()}_${generateSecureRandomString()}`
			setSessionId(tempSessionId)

			// Send reset_session to create a new session on backend
			sendMessage({ type: 'reset_session', user: config.user })

			// For now, resolve immediately since backend handles session creation
			// In a more robust implementation, we'd wait for session confirmation
			resolve(tempSessionId)
		})
	}, [sessionId, sendMessage, config.user])

	// Auto-save to browser IndexedDB when saveMode is 'local'
	useEffect(() => {
		if (saveMode !== 'local') return
		const userMessages = messages.filter(m => m.role === 'user')
		if (userMessages.length === 0) return

		if (localSaveTimerRef.current) clearTimeout(localSaveTimerRef.current)
		localSaveTimerRef.current = setTimeout(() => {
			const convId = activeConversationId || `local_${Date.now()}_${generateSecureRandomString()}`
			if (!activeConversationId) setActiveConversationId(convId)
			const firstUserMsg = userMessages[0]?.content || ''
			saveLocalConv({
				id: convId,
				title: firstUserMsg.substring(0, 200) || 'Untitled',
				model: currentModel,
				created_at: messages[0]?.timestamp || new Date().toISOString(),
				messages: messages.map(m => ({
					role: m.role,
					content: m.content || '',
					timestamp: m.timestamp,
					message_type: m.type || 'chat',
				})),
				tags: [],
			}).catch(e => console.error('Failed to save conversation locally:', e))
		}, 1000)

		return () => {
			if (localSaveTimerRef.current) clearTimeout(localSaveTimerRef.current)
		}
	// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [messages?.length, saveMode, activeConversationId, currentModel])

	// addSystemEvent: adds a system event message to the chat timeline
	const addSystemEvent = useCallback((subtype, text, meta = {}) => {
		const eventId = `system_${Date.now()}_${generateSecureRandomString()}`
		addMessage({
			role: 'system',
			type: 'system',
			subtype,
			text,
			meta,
			timestamp: new Date().toISOString(),
			id: eventId
		})
		return eventId
	}, [addMessage])

	const value = {
		appName: config.appName,
		user: config.user,
		models: config.models,
		tools: config.tools,
		prompts: config.prompts,
		dataSources: config.dataSources,
		ragServers: config.ragServers, // Expose rich server structure
		ragSources, // Expose flattened list of sources
		features: config.features,
		setFeatures: config.setFeatures,
		currentModel: config.currentModel,
		setCurrentModel: config.setCurrentModel,
		selectedTools: selections.selectedTools,
		toggleTool: selections.toggleTool,
		selectAllServerTools,
		deselectAllServerTools,
		selectedPrompts: selections.selectedPrompts,
		togglePrompt: selections.togglePrompt,
		addTools: selections.addTools,
		removeTools: selections.removeTools,
		addPrompts: selections.addPrompts,
		setSinglePrompt: selections.setSinglePrompt,
		removePrompts: selections.removePrompts,
		makePromptActive: selections.makePromptActive,
		clearActivePrompt: selections.clearActivePrompt,
		activePromptKey: selections.activePromptKey,
		selectAllServerPrompts,
		deselectAllServerPrompts,
		selectedDataSources: selections.selectedDataSources,
		toggleDataSource: selections.toggleDataSource,
		addDataSources: selections.addDataSources,
		clearDataSources: selections.clearDataSources,
		ragEnabled,
		toggleRagEnabled,
		toolChoiceRequired: selections.toolChoiceRequired,
		setToolChoiceRequired: selections.setToolChoiceRequired,
		clearToolsAndPrompts: selections.clearToolsAndPrompts,
		complianceLevelFilter: selections.complianceLevelFilter,
		setComplianceLevelFilter: setComplianceLevelFilterWithCleanup,
		agentModeEnabled: agent.agentModeEnabled,
		setAgentModeEnabled: agent.setAgentModeEnabled,
		agentMaxSteps: agent.agentMaxSteps,
		setAgentMaxSteps: agent.setAgentMaxSteps,
		agentModeAvailable: agent.agentModeAvailable,
		currentAgentStep: agent.currentAgentStep,
		agentPendingQuestion: agent.agentPendingQuestion,
		setAgentPendingQuestion: agent.setAgentPendingQuestion,
		isInAdminGroup: config.isInAdminGroup,
		fileExtraction: config.fileExtraction,
		messages,
		isWelcomeVisible,
		isThinking,
		isSynthesizing,
		sendChatMessage,
		clearChat,
		stopAgent,
		answerAgentQuestion,
		downloadChat,
		downloadChatAsText,
		canvasContent: files.canvasContent,
		setCanvasContent: files.setCanvasContent,
		canvasFiles: files.canvasFiles,
		setCanvasFiles: files.setCanvasFiles,
		currentCanvasFileIndex: files.currentCanvasFileIndex,
		setCurrentCanvasFileIndex: files.setCurrentCanvasFileIndex,
		customUIContent: files.customUIContent,
		setCustomUIContent: files.setCustomUIContent,
		sessionFiles: files.sessionFiles,
		downloadFile,
		deleteFile,
		taggedFiles: files.taggedFiles,
		toggleFileTag: files.toggleFileTag,
		clearTaggedFiles: files.clearTaggedFiles,
		sessionId,
		attachments,
		addAttachment,
		addPendingFileEvent,
		resolvePendingFileEvent,
		ensureSession,
		addSystemEvent,
		settings,
		updateSettings,
		sendMessage,
		sendApprovalResponse: sendMessage,
		pendingElicitation,
		setPendingElicitation,
		refreshConfig: config.refreshConfig,
		saveMode,
		setSaveMode,
		activeConversationId,
		loadSavedConversation,
	}

	return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>
}

export default ChatContext
