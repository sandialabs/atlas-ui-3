// Slim ChatContext (clean refactor)
import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { useWS } from './WSContext'
import { useChatConfig } from '../hooks/chat/useChatConfig'
import { useSelections } from '../hooks/chat/useSelections'
import { useAgentMode } from '../hooks/chat/useAgentMode'
import { useMessages } from '../hooks/chat/useMessages'
import { useFiles } from '../hooks/chat/useFiles'
import { useSettings } from '../hooks/useSettings'
import { createWebSocketHandler } from '../handlers/chat/websocketHandlers'

const ChatContext = createContext(null)

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
	const { messages, addMessage, mapMessages, resetMessages } = useMessages()
	const { settings } = useSettings()

	const [isWelcomeVisible, setIsWelcomeVisible] = useState(true)
	const [isThinking, setIsThinking] = useState(false)

		const { sendMessage, addMessageHandler } = useWS()
	const { currentModel } = config
	const { selectedTools, selectedPrompts, selectedDataSources } = selections

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
				setCurrentAgentStep: agent.setCurrentAgentStep,
					setAgentPendingQuestion: agent.setAgentPendingQuestion,
			setCanvasContent: files.setCanvasContent,
			setCanvasFiles: files.setCanvasFiles,
			setCurrentCanvasFileIndex: files.setCurrentCanvasFileIndex,
			setCustomUIContent: files.setCustomUIContent,
			setSessionFiles: files.setSessionFiles,
			getFileType: files.getFileType,
				triggerFileDownload
		})
		return addMessageHandler(handler)
	}, [addMessageHandler, addMessage, mapMessages, agent.setCurrentAgentStep, files, triggerFileDownload])

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

	const sendChatMessage = useCallback((content, extraFiles = {}) => {
		if (!content.trim() || !currentModel) return
		if (isWelcomeVisible) setIsWelcomeVisible(false)
		addMessage({ role: 'user', content, timestamp: new Date().toISOString() })
		setIsThinking(true)
		const tagged = files.getTaggedFilesContent()
		sendMessage({
			type: 'chat',
			content,
			model: currentModel,
			selected_tools: [...selectedTools],
			selected_prompts: [...selectedPrompts],
			selected_data_sources: [...selectedDataSources],
			only_rag: config.onlyRag,
			tool_choice_required: selections.toolChoiceRequired,
			user: config.user,
			files: { ...extraFiles, ...tagged },
			agent_mode: agent.agentModeEnabled,
			agent_max_steps: settings.maxIterations || agent.agentMaxSteps,
			temperature: settings.llmTemperature || 0.7,
		})
	}, [addMessage, currentModel, selectedTools, selectedPrompts, selectedDataSources, config, selections.toolChoiceRequired, selections, agent, files, isWelcomeVisible, sendMessage, settings])

	const clearChat = useCallback(() => {
		resetMessages()
		setIsWelcomeVisible(true)
		files.setCanvasContent('')
		files.setCustomUIContent(null)
		files.setSessionFiles({ total_files: 0, files: [], categories: { code: [], image: [], data: [], document: [], other: [] } })
		
		// Notify backend to create a new session
		if (sendMessage) {
			sendMessage({ type: 'reset_session' })
		}
	}, [resetMessages, files, sendMessage])

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
		if (asText) {
			let text = `Chat Export - ${config.appName}\nDate: ${new Date().toLocaleString()}\nUser: ${config.user}\nModel: ${currentModel}\nSelected Tools: ${[...selectedTools].join(', ') || 'None'}\nSelected Data Sources: ${[...selectedDataSources].join(', ') || 'None'}\nAgent Mode: ${agent.agentModeEnabled ? 'Enabled' : 'Disabled'}\n\n${'='.repeat(50)}\n\n`
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
					selectedDataSources: [...selectedDataSources],
					onlyRag: config.onlyRag,
					toolChoiceRequired: selections.toolChoiceRequired,
					agentModeEnabled: agent.agentModeEnabled,
					agentMaxSteps: agent.agentMaxSteps,
					messageCount: messages.length,
					exportVersion: '1.0'
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
	}, [messages, config.appName, config.user, currentModel, selectedTools, selectedDataSources, agent.agentModeEnabled, agent.agentMaxSteps, config.onlyRag, selections.toolChoiceRequired, files.canvasContent])

	const downloadChat = useCallback(() => exportData(false), [exportData])
	const downloadChatAsText = useCallback(() => exportData(true), [exportData])

	const value = {
		appName: config.appName,
		user: config.user,
		models: config.models,
		tools: config.tools,
		prompts: config.prompts,
		dataSources: config.dataSources,
		features: config.features,
		setFeatures: config.setFeatures,
		currentModel: config.currentModel,
		setCurrentModel: config.setCurrentModel,
		onlyRag: config.onlyRag,
		setOnlyRag: config.setOnlyRag,
		selectedTools: selections.selectedTools,
		toggleTool: selections.toggleTool,
		selectAllServerTools,
		deselectAllServerTools,
		selectedPrompts: selections.selectedPrompts,
		togglePrompt: selections.togglePrompt,
		addTools: selections.addTools,
		removeTools: selections.removeTools,
		setSinglePrompt: selections.setSinglePrompt,
		removePrompts: selections.removePrompts,
		selectAllServerPrompts,
		deselectAllServerPrompts,
		selectedDataSources: selections.selectedDataSources,
		toggleDataSource: selections.toggleDataSource,
		toolChoiceRequired: selections.toolChoiceRequired,
		setToolChoiceRequired: selections.setToolChoiceRequired,
		clearToolsAndPrompts: selections.clearToolsAndPrompts,
		agentModeEnabled: agent.agentModeEnabled,
		setAgentModeEnabled: agent.setAgentModeEnabled,
		agentMaxSteps: agent.agentMaxSteps,
		setAgentMaxSteps: agent.setAgentMaxSteps,
		agentModeAvailable: agent.agentModeAvailable,
		currentAgentStep: agent.currentAgentStep,
		agentPendingQuestion: agent.agentPendingQuestion,
		setAgentPendingQuestion: agent.setAgentPendingQuestion,
		isInAdminGroup: config.isInAdminGroup,
		messages,
		isWelcomeVisible,
		isThinking,
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
		settings,
	}

	return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>
}

export default ChatContext
