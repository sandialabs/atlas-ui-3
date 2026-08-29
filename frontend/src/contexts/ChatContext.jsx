// Slim ChatContext (clean refactor)
import { createContext, useContext, useEffect, useState, useCallback, useMemo, useRef } from 'react'
import { useWS } from './WSContext'
import { useToast } from '../components/ui/toastContext'
import { useChatConfig } from '../hooks/chat/useChatConfig'
import { useSelections, isUserPromptKey, userPromptIdFromKey } from '../hooks/chat/useSelections'
import { useUserPrompts } from '../hooks/useUserPrompts'
import { useWorkspaces, isStaleWorkspacePointer } from '../hooks/useWorkspaces'
import { useAgentMode } from '../hooks/chat/useAgentMode'
import { useMessages } from '../hooks/chat/useMessages'
import { useFiles } from '../hooks/chat/useFiles'
import { useSettings } from '../hooks/useSettings'
import { usePersistentState } from '../hooks/chat/usePersistentState'
import { createWebSocketHandler, cleanupStreamState } from '../handlers/chat/websocketHandlers'
import { saveConversation as saveLocalConv } from '../utils/localConversationDB'
import { buildPromptInfoByKey, resolvePromptInfo, buildExportConversation, buildPersistedMessage, formatToolCallForText } from '../utils/chatExport'
import { findServerConfigForMcpKey } from '../utils/mcpKeys'
import { userMessageSliceIndex } from '../utils/userMessageOrdinal'
import { SEARCH_TOOL, migrateToolName } from '../constants/atlasTools'

// Safety timeout for stuck thinking state (no backend response)
const THINKING_TIMEOUT_MS = 5 * 60 * 1000 // 5 minutes

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

// Every selection action that changes what the user has chosen. A queued
// workspace restore must lose to any of them (issue #829); see `guarded` below.
const MUTATING_SELECTION_ACTIONS = [
	'toggleTool', 'addTools', 'removeTools',
	'togglePrompt', 'addPrompts', 'removePrompts', 'setSinglePrompt',
	'makePromptActive', 'clearActivePrompt', 'clearToolsAndPrompts',
	'toggleDataSource', 'addDataSources', 'clearDataSources',
	'setRagEnabled', 'toggleRagEnabled',
]

export const ChatProvider = ({ children }) => {
	// State slices
	const config = useChatConfig()
	const selections = useSelections()
	const customPromptsEnabled = !!config.features?.custom_prompts
	// User-authored custom prompt library (issue #153)
	const userPrompts = useUserPrompts(customPromptsEnabled)
	// Workspaces: saved bundles of prompt + RAG source + tool selections
	const workspacesEnabled = !!config.features?.workspaces
	const workspaces = useWorkspaces(workspacesEnabled)
	// Pass through dynamic availability from backend config
		const agent = useAgentMode(config.agentModeAvailable)
	const files = useFiles()
	const { messages, addMessage, bulkAdd, mapMessages, updateToolResult, resetMessages, streamToken, streamEnd } = useMessages()
	const { settings, updateSettings } = useSettings()

	const isStreaming = messages.some(m => m._streaming === true)

	const [isWelcomeVisible, setIsWelcomeVisible] = useState(true)
	const [isThinking, setIsThinking] = useState(false)
	// Tracks an in-flight agent-mode run end to end. Unlike isThinking (which the
	// native agentic loop clears the moment token streaming begins) this stays
	// true for the whole run -- tool calls, streamed segments, and the final
	// answer -- so the agent Stop button remains visible until the run actually
	// ends. Cleared on the terminal agent events (see websocketHandlers) and on
	// an explicit stop.
	const [isAgentRunning, setIsAgentRunning] = useState(false)
	const [isSynthesizing, setIsSynthesizing] = useState(false)
	const [sessionId, setSessionId] = useState(null)
	const [attachments, setAttachments] = useState(new Set())
	const [, setPendingFileEvents] = useState(new Map())
	const [pendingElicitation, setPendingElicitation] = useState(null)
	const [followUpSuggestions, setFollowUpSuggestions] = useState([])

	// Chat history: 3-state save mode persists across refreshes via localStorage
	// 'none' = incognito (nothing saved), 'local' = browser IndexedDB, 'server' = backend DB
	const [saveMode, setSaveMode] = usePersistentState('chatui-save-mode', 'none')
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

		const { sendMessage, addMessageHandler, isConnected } = useWS()
	const toast = useToast()
	const { currentModel } = config
	const { selectedTools, selectedPrompts, activePrompts, activePromptKey, clearActivePrompt, selectedDataSources, ragEnabled } = selections

	useEffect(() => {
		if (!config.configReady || customPromptsEnabled) return
		if (isUserPromptKey(activePromptKey)) {
			clearActivePrompt()
		}
	}, [config.configReady, customPromptsEnabled, activePromptKey, clearActivePrompt])

	// Which workspace the current selections came from. Persisted so a refresh
	// keeps showing the workspace whose selections are still loaded.
	const [activeWorkspaceId, setActiveWorkspaceId] = usePersistentState('chatui-active-workspace', null)
	const { applyWorkspace, snapshotSelections } = selections
	const { workspaces: workspaceList, updateWorkspace: updateWorkspaceApi, deleteWorkspace: deleteWorkspaceApi } = workspaces

	// Drop a stale pointer: the workspace may have been deleted in another tab,
	// or the feature turned off, and a dangling id would light up the switcher
	// with a name that no longer exists. Gated on `configReady` and `loaded` so a
	// page refresh does not clear the pointer against defaults that have not been
	// replaced by the real config and workspace list yet.
	const workspacesLoaded = workspaces.loaded
	useEffect(() => {
		if (isStaleWorkspacePointer({
			activeWorkspaceId,
			configReady: config.configReady,
			enabled: workspacesEnabled,
			loaded: workspacesLoaded,
			workspaces: workspaceList,
		})) {
			setActiveWorkspaceId(null)
		}
	}, [activeWorkspaceId, config.configReady, workspacesEnabled, workspacesLoaded, workspaceList, setActiveWorkspaceId])

	// A conversation load can ask for a workspace restore before the workspace
	// list -- or the config that gates the feature -- has arrived. The request is
	// parked here and applied once both are ready. Every explicit user action
	// (switching, clearing or deleting a workspace, starting a new chat) cancels
	// it, so a late restore can never overwrite a deliberate choice.
	const pendingWorkspaceRestoreRef = useRef(null)

	// The workspace this conversation is bound to, as opposed to the one that
	// happens to be active right now. Only a load (which reads it from the saved
	// metadata) or the user actually sending a turn updates it, so the local
	// autosave cannot silently re-bind a conversation just because it was opened
	// while a different workspace was active.
	const conversationWorkspaceIdRef = useRef(null)

	const switchWorkspace = useCallback(workspaceId => {
		const ws = workspaceList.find(w => w.id === workspaceId)
		if (!ws) return false
		// An explicit switch supersedes any queued restore.
		pendingWorkspaceRestoreRef.current = null
		applyWorkspace(ws.config)
		setActiveWorkspaceId(ws.id)
		return true
	}, [workspaceList, applyWorkspace, setActiveWorkspaceId])

	const saveCurrentAsWorkspace = useCallback(async (name, description = null) => {
		const created = await workspaces.createWorkspace(name, snapshotSelections(), description)
		if (created) setActiveWorkspaceId(created.id)
		return created
	}, [workspaces, snapshotSelections, setActiveWorkspaceId])

	const updateActiveWorkspace = useCallback(async () => {
		if (!activeWorkspaceId) return null
		return updateWorkspaceApi(activeWorkspaceId, { config: snapshotSelections() })
	}, [activeWorkspaceId, updateWorkspaceApi, snapshotSelections])

	const renameWorkspace = useCallback(
		(workspaceId, name) => updateWorkspaceApi(workspaceId, { name }),
		[updateWorkspaceApi]
	)

	const deleteWorkspace = useCallback(async workspaceId => {
		const deleted = await deleteWorkspaceApi(workspaceId)
		// Deleting the tracked workspace only drops the pointer; the selections it
		// applied stay put so the user does not lose their context mid-chat.
		if (deleted && workspaceId === activeWorkspaceId) setActiveWorkspaceId(null)
		// Never restore into a workspace that has just been deleted.
		if (deleted && pendingWorkspaceRestoreRef.current === workspaceId) {
			pendingWorkspaceRestoreRef.current = null
		}
		return deleted
	}, [deleteWorkspaceApi, activeWorkspaceId, setActiveWorkspaceId])

	const clearActiveWorkspace = useCallback(() => {
		// Explicitly dropping the workspace also cancels a restore that has not
		// fired yet, which would otherwise re-apply it moments later.
		pendingWorkspaceRestoreRef.current = null
		setActiveWorkspaceId(null)
	}, [setActiveWorkspaceId])

	// Restoring a conversation (issue #829) re-enables the workspace it was tied
	// to, and says so: the switch replaces the tools, prompt and RAG sources the
	// user may have hand-picked, and a workspace that has since been deleted
	// would otherwise leave the header asserting an unrelated one.
	const applyWorkspaceRestore = useCallback(workspaceId => {
		// Resolve first, *before* the already-active check: a workspace can be
		// deleted while its id is still the active pointer, and short-circuiting
		// on the pointer alone would report success and never tell the user.
		const ws = workspaceList.find(w => w.id === workspaceId)
		if (!ws) {
			// Safe to say it is gone: both callers gate on `workspacesLoaded`, which
			// only a *successful* list fetch sets, so reaching here means we hold an
			// authoritative list rather than one that failed to arrive. (Checking
			// `workspaces.error` instead would be wrong -- it is shared with the CRUD
			// calls and sticky, so an unrelated earlier failure would silence this
			// notification for the rest of the session.)
			toast.info('This conversation\'s workspace is no longer available. Your current selections were kept.')
			return false
		}
		// Already on it: deliberately do not re-apply. Re-applying would discard
		// selection edits the user made on top of this workspace, and the
		// conversation is already bound to it, so nothing is lost by skipping.
		if (workspaceId === activeWorkspaceId) return true
		switchWorkspace(workspaceId)
		toast.info(`Switched to the "${ws.name}" workspace this conversation was saved with.`)
		return true
	}, [activeWorkspaceId, workspaceList, switchWorkspace, toast])

	const restoreWorkspace = useCallback(workspaceId => {
		if (!workspaceId) {
			// A conversation with no workspace cancels any deferred restore queued
			// by an earlier load: without this, opening conversation A before the
			// list loaded, then conversation B (no workspace), would still apply
			// A's workspace to B once the list arrived.
			pendingWorkspaceRestoreRef.current = null
			return
		}
		// `workspacesEnabled` reads a config that is fetched asynchronously and is
		// false for everyone until it lands, so "config not ready" means "not known
		// yet" -- defer, or an early open would throw the id away permanently.
		if (!config.configReady) {
			pendingWorkspaceRestoreRef.current = workspaceId
			return
		}
		if (!workspacesEnabled) {
			pendingWorkspaceRestoreRef.current = null
			return
		}
		if (!workspacesLoaded) {
			pendingWorkspaceRestoreRef.current = workspaceId
			return
		}
		pendingWorkspaceRestoreRef.current = null
		applyWorkspaceRestore(workspaceId)
	}, [config.configReady, workspacesEnabled, workspacesLoaded, applyWorkspaceRestore])

	// Apply a deferred restore once the config and the workspace list are both in.
	useEffect(() => {
		const pending = pendingWorkspaceRestoreRef.current
		if (!pending || !config.configReady) return
		if (!workspacesEnabled) {
			pendingWorkspaceRestoreRef.current = null
			return
		}
		if (!workspacesLoaded) return
		pendingWorkspaceRestoreRef.current = null
		applyWorkspaceRestore(pending)
	}, [config.configReady, workspacesEnabled, workspacesLoaded, applyWorkspaceRestore])

	// A queued restore must lose to any deliberate selection the user makes while
	// it waits. Workspace actions already cancel it; so must editing the tools,
	// prompt, or RAG sources directly, or a slow config/workspace fetch would
	// silently overwrite those picks seconds later.
	const cancelPendingWorkspaceRestore = useCallback(() => {
		pendingWorkspaceRestoreRef.current = null
	}, [])

	const withRestoreCancelled = useCallback(fn => (...args) => {
		cancelPendingWorkspaceRestore()
		return fn(...args)
	}, [cancelPendingWorkspaceRestore])

	// Guarded once, at the boundary, rather than action by action: every mutating
	// selection action is wrapped by name, so a new one added to this list is
	// covered by default instead of silently becoming another way for a queued
	// restore to overwrite the user. `applyWorkspace` is deliberately absent --
	// it is how a restore applies, and wrapping it would cancel the restore
	// mid-flight.
	const guarded = useMemo(
		() => Object.fromEntries(
			MUTATING_SELECTION_ACTIONS
				.filter(name => typeof selections[name] === 'function')
				.map(name => [name, withRestoreCancelled(selections[name])])
		),
		[selections, withRestoreCancelled]
	)

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
			setIsAgentRunning,
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

	// Safety timeout: if isThinking stays true for too long without any response
	// from the backend, reset it and show an error so the user is not stuck forever.
	const thinkingTimeoutRef = useRef(null)

	useEffect(() => {
		if (isThinking) {
			thinkingTimeoutRef.current = setTimeout(() => {
				setIsThinking(false)
				setIsAgentRunning(false)
				setIsSynthesizing(false)
				agent.setCurrentAgentStep(0)
				addMessage({
					role: 'system',
					content: 'Error: The request timed out without a response from the server. Please try again or select a different model.',
					timestamp: new Date().toISOString()
				})
			}, THINKING_TIMEOUT_MS)
		} else {
			if (thinkingTimeoutRef.current) {
				clearTimeout(thinkingTimeoutRef.current)
				thinkingTimeoutRef.current = null
			}
		}
		return () => {
			if (thinkingTimeoutRef.current) {
				clearTimeout(thinkingTimeoutRef.current)
				thinkingTimeoutRef.current = null
			}
		}
	// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [isThinking])

	// Fetch follow-up suggestions after a response completes
	const prevIsThinkingRef = useRef(false)
	const prevIsStreamingRef = useRef(false)

	useEffect(() => {
		const wasThinking = prevIsThinkingRef.current
		const wasStreaming = prevIsStreamingRef.current

		// Detect when the response has fully completed:
		// - streaming mode: streaming transitions from true to false
		// - non-streaming mode: thinking transitions from true to false (with no streaming)
		const responseCompleted =
			(wasStreaming && !isStreaming && !isThinking) ||
			(wasThinking && !isThinking && !isStreaming && !wasStreaming)

		if (responseCompleted && config.features?.followup_suggestions) {
			const convMessages = messages
				.filter(m => (m.role === 'user' || m.role === 'assistant') && m.content)
				.map(m => ({ role: m.role, content: m.content }))

			const lastAssistant = convMessages.findLast(m => m.role === 'assistant')
			if (lastAssistant && config.currentModel) {
				fetch('/api/suggest_followups', {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ messages: convMessages, model: config.currentModel }),
				})
					.then(r => (r.ok ? r.json() : null))
					.then(data => {
						if (data?.questions?.length > 0) {
							setFollowUpSuggestions(data.questions)
						}
					})
					.catch(e => console.debug('Follow-up suggestions unavailable:', e))
			}
		}

		prevIsThinkingRef.current = isThinking
		prevIsStreamingRef.current = isStreaming
	// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [isThinking, isStreaming])

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

		// Clear active prompt if it no longer exists in config. User-authored
		// prompts (issue #153) live outside config.prompts (they're fetched
		// separately), so they must be exempt here or a persisted active user
		// prompt would be cleared on every config load — reverting to Default
		// after a refresh.
		if (
			selections.activePromptKey &&
			!isUserPromptKey(selections.activePromptKey) &&
			!validPromptKeys.has(selections.activePromptKey)
		) {
			// Clear stale active prompt that no longer exists in config
			selections.clearActivePrompt()
		}
	// Only run when prompts config changes, not on every selectedPrompts change
	// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [config.prompts])

	// A bulk select/deselect cancels a queued restore just like a single toggle.
	// The cancel is on the *action*, not on whether any key actually changed:
	// "Deselect All" with nothing selected is still the user saying what they
	// want, and it should not be quietly undone by a restore a second later.
	const selectAllServerTools = useCallback((server) => {
		cancelPendingWorkspaceRestore()
		const group = config.tools.find(t => t.server === server); if (!group) return
		group.tools.forEach(tool => { const key = `${server}_${tool}`; if (!selectedTools.has(key)) guarded.toggleTool(key) })
	}, [config.tools, selectedTools, guarded, cancelPendingWorkspaceRestore])

	const deselectAllServerTools = useCallback((server) => {
		cancelPendingWorkspaceRestore()
		const group = config.tools.find(t => t.server === server); if (!group) return
		group.tools.forEach(tool => { const key = `${server}_${tool}`; if (selectedTools.has(key)) guarded.toggleTool(key) })
	}, [config.tools, selectedTools, guarded, cancelPendingWorkspaceRestore])

	const selectAllServerPrompts = useCallback((server) => {
		cancelPendingWorkspaceRestore()
		const group = config.prompts.find(p => p.server === server); if (!group) return
		group.prompts.forEach(p => { const key = `${server}_${p.name}`; if (!selectedPrompts.has(key)) guarded.togglePrompt(key) })
	}, [config.prompts, selectedPrompts, guarded, cancelPendingWorkspaceRestore])

	const deselectAllServerPrompts = useCallback((server) => {
		cancelPendingWorkspaceRestore()
		const group = config.prompts.find(p => p.server === server); if (!group) return
		group.prompts.forEach(p => { const key = `${server}_${p.name}`; if (selectedPrompts.has(key)) guarded.togglePrompt(key) })
	}, [config.prompts, selectedPrompts, guarded, cancelPendingWorkspaceRestore])

	// Flatten ragServers into a list of all available data source IDs (qualified with server name)
	const getAllRagSourceIds = useCallback(() => {
		return config.ragServers.flatMap(server =>
			server.sources.map(source => `${server.server}:${source.id}`)
		)
	}, [config.ragServers])

	const sendChatMessage = useCallback((content, extraFiles = {}, { rewindToUserIndex = null, selectedToolsOverride = null, captureCorrection = null } = {}) => {
		if (!content.trim() || !currentModel) return false
		// Don't allow sending while the WebSocket is disconnected -- the message
		// would never reach the backend and the UI would hang on "Thinking...".
		if (!isConnected) {
			toast.error('Not connected. Waiting to reconnect before sending.')
			return false
		}
		// Agent mode needs at least one tool to act on. Block the send (rather than
		// silently degrading to a normal chat) so the user makes an explicit
		// choice -- otherwise the agent loop has nothing to call and the model can
		// emit tool calls the provider rejects. The backend enforces the same
		// guard for non-UI clients.
		// A fine-tune correction (issue #622) narrows the turn to exactly one tool
		// via selectedToolsOverride, so honor that list for the agent-mode guard and
		// the outgoing payload instead of the persisted selection.
		const toolsToSend = selectedToolsOverride != null ? selectedToolsOverride : [...selectedTools]
		// Selected data sources imply `atlas_search` (#862): the backend adds it to
		// the schema, so such a turn does have a tool to act on and must not be
		// blocked here -- the guard exists for turns with nothing to call at all.
		if (
			agent.agentModeAvailable && agent.agentModeEnabled &&
			toolsToSend.length === 0 && selectedDataSources.size === 0
		) {
			toast.error('Agent mode needs at least one tool selected. Choose a tool or turn off Agent mode.')
			return false
		}
		const tagged = files.getTaggedFilesContent()

		// Determine data sources to send:
		// RAG is activated when either of these are true:
		//   1. The RAG toggle is on (ragEnabled)
		//   2. One or more data sources are selected (hasSelectedSources)
		//   3. The built-in search tool is selected for this turn (#855). The
		//      magnifying-glass toggle is gone; selecting `atlas_search` is how a
		//      user turns search on, and an empty source selection then means
		//      "everything I can reach" rather than "nothing".
		const hasSelectedSources = selectedDataSources.size > 0
		const searchToolSelected = toolsToSend.some(t => migrateToolName(t) === SEARCH_TOOL)
		const ragActivated = ragEnabled || hasSelectedSources || searchToolSelected
		const dataSourcesToSend = ragActivated
			? (hasSelectedSources ? [...selectedDataSources] : getAllRagSourceIds())
			: []

		// A user-authored custom prompt (issue #153) replaces the default system
		// prompt and is sent as custom_system_prompt — never as an MCP prompt.
		// The selected_prompts exclusion is gated purely on the key type and stays
		// unconditional even when the feature is disabled: a stale userprompt:* key
		// persisted from when the feature was on must never leak into the MCP
		// selected_prompts payload (the clear-stale-key effect runs after render, so
		// a send could otherwise race ahead of it). Resolving the prompt content is
		// the part gated on the feature flag; if it no longer resolves we fall back
		// to the default system prompt.
		const activeKey = selections.activePromptKey
		const activeKeyIsUserPrompt = isUserPromptKey(activeKey)
		const activeUserPrompt = (customPromptsEnabled && activeKeyIsUserPrompt)
			? userPrompts.prompts.find(p => p.id === userPromptIdFromKey(activeKey))
			: null

		const sent = sendMessage({
			type: 'chat',
			content,
			model: currentModel,
			selected_tools: toolsToSend,
			selected_prompts: activeKeyIsUserPrompt ? [] : activePrompts,
			custom_system_prompt: activeUserPrompt ? activeUserPrompt.content : undefined,
			selected_data_sources: dataSourcesToSend,
			user: config.user,
			files: { ...extraFiles, ...tagged },
			agent_mode: agent.agentModeEnabled,
			agent_max_steps: settings.maxIterations || agent.agentMaxSteps,
			temperature: settings.llmTemperature || 0.7,
			compliance_level_filter: selections.complianceLevelFilter,
			save_mode: saveMode,
			// Backward compat: backend still checks incognito for older clients
			incognito: saveMode !== 'server',
			conversation_id: activeConversationId || undefined,
			// Rewind/edit-and-resubmit (issue #142): when set, the backend drops
			// this user prompt and everything after it before running the turn.
			rewind_to_user_index: rewindToUserIndex ?? undefined,
			// Fine-tune capture correction (issue #622): when present, the backend
			// records a (rejected, chosen) training pair for the re-run turn.
			capture_correction: captureCorrection ?? undefined,
			// Active workspace (issue #829): persisted with the conversation so
			// reopening it from history can re-enable the workspace it was tied
			// to. A restore that has not fired yet means the *conversation's*
			// workspace is the queued one, not the pointer still showing the
			// previous selections -- sending the pointer here would overwrite the
			// stored binding (usually with null) and lose it for good.
			// Explicitly null -- not undefined -- when there is genuinely no
			// workspace: `undefined` is dropped by JSON.stringify, and the backend
			// treats an omitted field as "leave the binding alone", so a user who
			// cleared their workspace could never unbind the conversation.
			workspace_id: pendingWorkspaceRestoreRef.current ?? activeWorkspaceId ?? null,
		})
		// Guard against a stale isConnected: if the socket dropped between the
		// check above and the send, bail out without mutating the UI so we don't
		// hang on "Thinking...".
		if (!sent) {
			toast.error('Not connected. Waiting to reconnect before sending.')
			return false
		}
		// Sending a turn is the only user action that re-binds a conversation to
		// the active workspace; opening one must not. Only once the frame is
		// actually on the wire -- a send that failed must not leave a durable
		// re-binding in the local record. Mirrors the frame: a queued restore
		// means the conversation's binding is the queued one.
		conversationWorkspaceIdRef.current =
			pendingWorkspaceRestoreRef.current ?? activeWorkspaceId ?? null
		// A turn is a deliberate action too, and it has just told the server which
		// workspace this conversation belongs to. Letting a queued restore fire
		// afterwards would swap the selections out from under the turn the user
		// just sent, and contradict the binding that turn wrote.
		cancelPendingWorkspaceRestore()
		// Only mutate the UI once the message is actually on the wire.
		if (isWelcomeVisible) setIsWelcomeVisible(false)
		setFollowUpSuggestions([])
		// Rewind/edit-and-resubmit (issue #142): now that the send is confirmed on
		// the wire, drop the targeted prompt and everything after it so the new
		// message takes its place. Done here -- after the early returns and the
		// `sent` guard -- so a failed or disconnected send can never truncate the
		// visible transcript while the backend history stays intact (which would
		// desync the two and misaddress the next rewind). The dispatch order
		// (truncate, then add) composes via the reducer's functional updates.
		if (rewindToUserIndex != null) {
			mapMessages(msgs => {
				const cut = userMessageSliceIndex(msgs, rewindToUserIndex)
				return cut === -1 ? msgs : msgs.slice(0, cut)
			})
		}
		addMessage({
			role: 'user',
			content,
			timestamp: new Date().toISOString(),
			_activePromptKey: selections.activePromptKey || null,
		})
		setIsThinking(true)
		setIsSynthesizing(false)
		// Drive the agent Stop button off a dedicated run flag rather than
		// isThinking, which the native agentic loop clears as soon as the first
		// token streams. Only true in agent mode; the terminal agent events clear
		// it (websocketHandlers).
		setIsAgentRunning(agent.agentModeEnabled)
		return true
	}, [addMessage, mapMessages, currentModel, selectedTools, activePrompts, selectedDataSources, ragEnabled, config, selections, agent, files, isWelcomeVisible, isConnected, toast, sendMessage, settings, getAllRagSourceIds, saveMode, activeConversationId, customPromptsEnabled, userPrompts.prompts, activeWorkspaceId, cancelPendingWorkspaceRestore])

	// Rewind to a previous user prompt and resubmit it (optionally edited).
	// Overwrite-in-place: the targeted prompt and everything after it are dropped
	// from the transcript, then the (edited) content is sent as a fresh turn.
	// userIndex is the 0-based ordinal of the message among user messages, which
	// the backend uses to truncate its own history (see truncate_at_user_index).
	const rewindAndResubmit = useCallback((userIndex, newContent) => {
		const content = (newContent ?? '').trim()
		if (!content) return false
		// Don't rewind while a response is streaming -- cancel first to avoid
		// interleaving the in-flight reply with the new turn.
		if (isThinking || isSynthesizing || isStreaming) {
			toast.error('Wait for the current response to finish before editing.')
			return false
		}
		// The local transcript truncation happens inside sendChatMessage, but only
		// after the send is confirmed on the wire, so a failed/disconnected send
		// never drops the visible tail of the conversation.
		return sendChatMessage(content, {}, { rewindToUserIndex: userIndex })
	}, [sendChatMessage, isThinking, isSynthesizing, isStreaming, toast])

	// Fine-tune capture correction (issue #622). Re-runs a previous turn forcing the
	// chosen tool so the backend records a (rejected, chosen) training pair. Built on
	// the same rewind/edit-and-resubmit path: it resubmits the original user prompt
	// (`content`) at its 0-based ordinal (`userIndex`), narrows the turn to exactly
	// one tool, and attaches the rejected assistant text/tool calls.
	const sendCaptureCorrection = useCallback((userIndex, content, chosenTool, { note = '', rejected = null } = {}) => {
		const text = (content ?? '').trim()
		if (text === '' || userIndex == null || !chosenTool) return false
		if (isThinking || isSynthesizing || isStreaming) {
			toast.error('Wait for the current response to finish before correcting.')
			return false
		}
		const captureCorrection = {
			rejected_turn_id: null,
			note: note || '',
			rejected: rejected || { assistant_message: '', tool_calls: [] },
		}
		return sendChatMessage(text, {}, {
			rewindToUserIndex: userIndex,
			selectedToolsOverride: [chosenTool],
			captureCorrection,
		})
	}, [sendChatMessage, isThinking, isSynthesizing, isStreaming, toast])

	const clearChat = useCallback(({ skipConfirm = false } = {}) => {
		// If there is any chat content or generation in progress, confirm before
		// discarding it -- "New Chat" should not silently throw away a reply the
		// user is actively reading / waiting on (mistakes happen).
		// Returns true if the chat was cleared, false if the user cancelled --
		// callers (Header/Ctrl+Alt+N) gate follow-up side-effects on this so a
		// cancelled confirm doesn't still close the canvas or steal focus.
		const isGenerating = isThinking || isSynthesizing || isStreaming
		const hasContent = messages.length > 0
		if (!skipConfirm && (hasContent || isGenerating)) {
			const prompt = isGenerating
				? 'A response is still being generated. Start a new chat and stop the current response?'
				: 'Start a new chat? This will clear the current conversation from view.'
			if (typeof window !== 'undefined' && typeof window.confirm === 'function') {
				if (!window.confirm(prompt)) return false
			}
		}

		// If generation is in progress, tell the backend to cancel it *before* we
		// ask for a new session. Otherwise the in-flight task keeps streaming
		// tokens and they get appended to the fresh, empty chat (the bug users
		// see where "the first amount of the output is removed from view").
		if (sendMessage && isGenerating) {
			if (agent?.agentModeEnabled) {
				sendMessage({ type: 'agent_control', action: 'stop' })
			}
			sendMessage({ type: 'stop_streaming' })
		}

		// Fully reset local UI state so the centered logo reappears and no stale
		// thinking / agent indicators linger.
		cleanupStreamState()
		streamEnd()
		resetMessages()
		setIsThinking(false)
		setIsSynthesizing(false)
		if (agent?.setCurrentAgentStep) agent.setCurrentAgentStep(0)
		if (agent?.setAgentPendingQuestion) agent.setAgentPendingQuestion(null)
		setIsWelcomeVisible(true)
		setActiveConversationId(null)
		setFollowUpSuggestions([])
		// A deferred workspace restore queued by a previous load must not
		// fire into the fresh chat once the workspace list finishes loading.
		pendingWorkspaceRestoreRef.current = null
		conversationWorkspaceIdRef.current = null
		files.setCanvasContent('')
		files.setCustomUIContent(null)
		files.setSessionFiles({ total_files: 0, files: [], categories: { code: [], image: [], data: [], document: [], other: [] } })

		// Notify backend to create a new session
		if (sendMessage) {
			sendMessage({ type: 'reset_session' })
		}
		return true
	}, [resetMessages, files, sendMessage, isThinking, isSynthesizing, isStreaming, messages.length, agent, streamEnd])

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
		// Sends the conversation_id and messages so the LLM has prior context.
		// Display-only rows (e.g. persisted tool_call messages, issue #684) are
		// excluded: they exist purely to re-render the transcript and a bare
		// role:'tool' row with no preceding tool_calls would be rejected as an
		// orphan tool message by some providers.
		if (sendMessage) {
			sendMessage({
				type: 'restore_conversation',
				conversation_id: conversationData.id,
				messages: conversationData.messages
					.filter(msg => (msg.message_type || 'chat') !== 'tool_call')
					.map(msg => ({
						role: msg.role,
						content: msg.content || '',
					})),
			})
		}

		// Re-enable the workspace this conversation was tied to (issue #829).
		// Best effort: a workspace that has since been deleted is silently
		// skipped, and if the list has not loaded yet the switch is deferred
		// until it does. A conversation with no recorded workspace leaves the
		// currently active workspace untouched.
		const meta = conversationData.metadata || {}
		// Remember the binding as loaded so the local autosave re-persists *this*
		// conversation's workspace rather than whatever is active at save time.
		conversationWorkspaceIdRef.current = meta.workspace_id || null
		restoreWorkspace(meta.workspace_id)
	}, [resetMessages, files, sendMessage, bulkAdd, restoreWorkspace])

	const downloadFile = useCallback((filename) => {
		if (!files.sessionFiles.files.find(f => f.filename === filename)) return
		sendMessage({ type: 'download_file', filename, user: config.user })
	}, [files.sessionFiles.files, sendMessage, config.user])

		// Agent controls
		const stopAgent = useCallback(() => {
			// Hide the Stop button immediately; the backend stop is best-effort and
			// the terminal agent_completion event will also clear this.
			setIsAgentRunning(false)
			if (sendMessage) sendMessage({ type: 'agent_control', action: 'stop' })
		}, [sendMessage])

		// Stop non-agent streaming
		const stopStreaming = useCallback(() => {
			cleanupStreamState()
			streamEnd()
			setIsThinking(false)
			if (sendMessage) sendMessage({ type: 'stop_streaming' })
		}, [sendMessage, streamEnd])

			const answerAgentQuestion = useCallback((content) => {
			if (!content || !content.trim()) return
				// Show immediately in UI. _agentInput marks this as an agent-loop
				// answer: the backend consumes it inside the transient agent loop and
				// never appends it to ConversationHistory, so it must NOT count toward
				// the rewind ordinal (see utils/userMessageOrdinal). #142
				addMessage({ role: 'user', content, timestamp: new Date().toISOString(), _agentInput: true })
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

		const promptInfoByKey = buildPromptInfoByKey(config.prompts, userPrompts.prompts)
		const activePromptInfo = resolvePromptInfo(selections.activePromptKey, promptInfoByKey)
		const exportConversation = buildExportConversation(messages, promptInfoByKey)

		if (asText) {
			let promptLine
			if (activePromptInfo) {
				const serverPart = activePromptInfo.server ? ` (from ${activePromptInfo.server})` : ''
				const descPart = activePromptInfo.description ? ` — ${activePromptInfo.description}` : ''
				const previewPart = activePromptInfo.preview ? `\nPrompt preview:\n${activePromptInfo.preview}` : ''
				promptLine = `Active Custom Prompt: ${activePromptInfo.name}${serverPart}${descPart}${previewPart}\n`
			} else {
				promptLine = 'Active Custom Prompt: Default\n'
			}
			let text = `Chat Export - ${config.appName}\nDate: ${new Date().toLocaleString()}\nUser: ${config.user}\nModel: ${currentModel}\nSelected Tools: ${[...selectedTools].join(', ') || 'None'}\nSelected RAG Sources: ${ragSourcesDisplay}\nAgent Mode: ${agent.agentModeEnabled ? 'Enabled' : 'Disabled'}\n${promptLine}\n${'='.repeat(50)}\n\n`
			exportConversation.forEach(m => {
				const toolBlock = formatToolCallForText(m)
				if (toolBlock) {
					text += `${toolBlock}\n\n`
				} else {
					text += `${m.role.toUpperCase()}:\n${m.content}\n\n`
				}
			})
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
					activePrompt: activePromptInfo,
					ragEnabled: ragEnabled,
					selectedRagSources: ragEnabled ? [...selectedDataSources] : null,
					agentModeEnabled: agent.agentModeEnabled,
					agentMaxSteps: agent.agentMaxSteps,
					messageCount: messages.length,
					exportVersion: '1.3'
				},
				conversation: exportConversation,
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
	}, [messages, config.appName, config.user, config.features, config.prompts, currentModel, selectedTools, selectedDataSources, agent.agentModeEnabled, agent.agentMaxSteps, selections.activePromptKey, files.canvasContent, userPrompts.prompts])

	const downloadChat = useCallback(() => exportData(false), [exportData])
	const downloadChatAsText = useCallback(() => exportData(true), [exportData])

	// Wrapper for setComplianceLevelFilter that clears incompatible selections
	const setComplianceLevelFilterWithCleanup = useCallback((newLevel) => {
		// If changing to a new compliance level (not clearing or setting to same)
		if (newLevel && newLevel !== selections.complianceLevelFilter) {
			// Clear tools that don't match the new compliance level
			const toolsToRemove = []
			selectedTools.forEach(toolKey => {
				const server = findServerConfigForMcpKey(toolKey, config.tools)
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
				const server = findServerConfigForMcpKey(promptKey, config.prompts)
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
				messages: messages.map(m => buildPersistedMessage(m)),
				tags: [],
				// Persist the active workspace so a locally saved conversation
				// restores it on reload (issue #829), mirroring the server save
				// path which stores it in conversation metadata.
				// The conversation's own binding -- not `activeWorkspaceId`, which would
				// rewrite the stored workspace ~1s after merely opening the
				// conversation and destroy the binding with no user action.
				metadata: { agent_mode: !!agent?.agentModeEnabled, workspace_id: conversationWorkspaceIdRef.current || null },
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
		toggleTool: guarded.toggleTool,
		selectAllServerTools,
		deselectAllServerTools,
		selectedPrompts: selections.selectedPrompts,
		togglePrompt: guarded.togglePrompt,
		addTools: guarded.addTools,
		removeTools: guarded.removeTools,
		addPrompts: guarded.addPrompts,
		setSinglePrompt: guarded.setSinglePrompt,
		removePrompts: guarded.removePrompts,
		makePromptActive: guarded.makePromptActive,
		clearActivePrompt: guarded.clearActivePrompt,
		activePromptKey: selections.activePromptKey,
		// User-authored custom prompt library (issue #153)
		userPrompts: userPrompts.prompts,
		userPromptsLoading: userPrompts.loading,
		userPromptsError: userPrompts.error,
		fetchUserPrompts: userPrompts.fetchPrompts,
		createUserPrompt: userPrompts.createPrompt,
		updateUserPrompt: userPrompts.updatePrompt,
		deleteUserPrompt: userPrompts.deletePrompt,
		selectAllServerPrompts,
		deselectAllServerPrompts,
		// Workspaces
		workspaces: workspaceList,
		workspacesLoading: workspaces.loading,
		workspacesError: workspaces.error,
		activeWorkspaceId,
		switchWorkspace,
		saveCurrentAsWorkspace,
		updateActiveWorkspace,
		renameWorkspace,
		deleteWorkspace,
		clearActiveWorkspace,
		selectedDataSources: selections.selectedDataSources,
		toggleDataSource: guarded.toggleDataSource,
		addDataSources: guarded.addDataSources,
		clearDataSources: guarded.clearDataSources,
		ragEnabled,
		toggleRagEnabled: guarded.toggleRagEnabled,
		clearToolsAndPrompts: guarded.clearToolsAndPrompts,
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
		fileUpload: config.fileUpload,
		messages,
		updateToolResult,
		isWelcomeVisible,
		isThinking,
		isAgentRunning,
		isSynthesizing,
		sendChatMessage,
		rewindAndResubmit,
		sendCaptureCorrection,
		clearChat,
		stopAgent,
		stopStreaming,
		isStreaming,
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
		configReady: config.configReady,
		saveMode,
		setSaveMode,
		activeConversationId,
		loadSavedConversation,
		followUpSuggestions,
		setFollowUpSuggestions,
	}

	return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>
}

export default ChatContext
