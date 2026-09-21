// Slim ChatContext (clean refactor)
import { createContext, useContext, useEffect, useState, useCallback, useMemo, useRef } from 'react'
import { useWS } from './WSContext'
import { useToast } from '../components/ui/toastContext'
import { useChatConfig } from '../hooks/chat/useChatConfig'
import { useSelections, isUserPromptKey, userPromptIdFromKey, isPersonaKey, personaIdFromKey, personaSurvivesComplianceFilter } from '../hooks/chat/useSelections'
import { useUserPrompts } from '../hooks/useUserPrompts'
import { usePersonas } from '../hooks/usePersonas'
import { useWorkspaces, isStaleWorkspacePointer } from '../hooks/useWorkspaces'
import { useAgentMode } from '../hooks/chat/useAgentMode'
import { useMessages } from '../hooks/chat/useMessages'
import { useFiles } from '../hooks/chat/useFiles'
import { useSettings } from '../hooks/useSettings'
import { usePersistentState } from '../hooks/chat/usePersistentState'
import { createWebSocketHandler, cleanupStreamState } from '../handlers/chat/websocketHandlers'
import { useConversationRuns, isRunActive } from '../hooks/chat/useConversationRuns'
import { saveConversation as saveLocalConv } from '../utils/localConversationDB'
import { buildPromptInfoByKey, resolvePromptInfo, buildExportConversation, buildPersistedMessage, isReplayPlaceholder, DISPLAY_ONLY_MESSAGE_TYPES, formatToolCallForText, openBlobInNewTab } from '../utils/chatExport'
import { findServerConfigForMcpKey } from '../utils/mcpKeys'
import { userMessageSliceIndex } from '../utils/userMessageOrdinal'
import { SEARCH_TOOL, migrateToolName } from '../constants/atlasTools'

// Safety timeout for stuck thinking state (no backend response)
// How long to wait for a `conversation_saved` after a joined run ends before
// reloading anyway (a run that failed before saving never sends one).
const RUN_END_RELOAD_GRACE_MS = 2500
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
	// Admin-preconfigured personas loaded from markdown files (issue #880).
	// Always available: they need no chat history and no per-user storage.
	const personas = usePersonas()
	// Workspaces: saved bundles of prompt + RAG source + tool selections
	const workspacesEnabled = !!config.features?.workspaces
	const workspaces = useWorkspaces(workspacesEnabled)
	// Pass through dynamic availability from backend config
		const agent = useAgentMode(config.agentModeAvailable)
	const files = useFiles()
	const { messages, addMessage, bulkAdd, mapMessages, updateToolResult, resetMessages, streamToken, streamEnd, discardReplayPlaceholders } = useMessages()
	const { settings, updateSettings } = useSettings()

	// A replayed placeholder (issue #957) is not this tab streaming: it marks a
	// run whose frames go to another socket, and a live append would have
	// cleared _replayed. Counting it would show Stop and hide Send for the
	// whole run in a tab that can do neither -- the run's own Stop affordance
	// is driven off run state (isAgentRunning includes it).
	const isStreaming = messages.some(m => m._streaming === true && !m._replayed)

	const [isWelcomeVisible, setIsWelcomeVisible] = useState(true)
	const [isThinking, setIsThinking] = useState(false)
	// Tracks an in-flight agent-mode run end to end. Unlike isThinking (which the
	// native agentic loop clears the moment token streaming begins) this stays
	// true for the whole run -- tool calls, streamed segments, and the final
	// answer -- so the agent Stop button remains visible until the run actually
	// ends. Cleared on the terminal agent events (see websocketHandlers) and on
	// an explicit stop.
	const [isAgentRunning, setIsAgentRunning] = useState(false)
	// Issue #884: a conversation opened while its run was still executing.
	// The run streams into the view it started in, so this one is stale by
	// construction; once the run ends the Sidebar reloads it from the store.
	const joinedRunConversationRef = useRef(null)
	const joinedRunTimerRef = useRef(null)
	const [runEndedConversationId, setRunEndedConversationId] = useState(null)
	const clearRunEndedConversation = useCallback(() => setRunEndedConversationId(null), [])
	const finishJoinedRun = useCallback((id) => {
		if (!id || joinedRunConversationRef.current !== id) return
		joinedRunConversationRef.current = null
		if (joinedRunTimerRef.current) {
			clearTimeout(joinedRunTimerRef.current)
			joinedRunTimerRef.current = null
		}
		setRunEndedConversationId(id)
	}, [])
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

	// Parallel conversation runs (issue #884). Keyed by conversation so a run in
	// a conversation the user is not looking at still has somewhere to live --
	// and so the history list can mark it as still working.
	const runs = useConversationRuns()
	// Read by the websocket handler to route incoming events. A ref, not the
	// state value: the handler is registered once and must always see the
	// conversation that is on screen *now*, not the one that was on screen when
	// it was built.
	const activeConversationIdRef = useRef(null)
	activeConversationIdRef.current = activeConversationId
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

	// A persisted persona key can outlive the persona itself: the admin deleted or
	// renamed the file, or the user lost access to its group. Only clear it once a
	// successful load has actually told us the persona is gone -- clearing while
	// the fetch is in flight (or after it failed) would drop the selection on
	// every refresh and silently fall back to the default prompt.
	useEffect(() => {
		if (!personas.loaded || personas.loading || personas.error) return
		if (!isPersonaKey(activePromptKey)) return
		if (personas.personas.some(p => p.id === personaIdFromKey(activePromptKey))) return
		clearActivePrompt()
	}, [personas.loaded, personas.loading, personas.error, personas.personas, activePromptKey, clearActivePrompt])

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

	// restoreUndoSnapshot is declared after loadSavedConversation, which it
	// delegates to; clearChat's Undo action reaches it through this ref rather
	// than reordering the callbacks.
	const restoreUndoSnapshotRef = useRef(null)

	// clearChat needs the full transcript to build its Undo snapshot, but must
	// not take `messages` as a dependency: its identity would then change on
	// every streamed token, and Header re-registers its Ctrl+Alt+N keydown
	// listener whenever clearChat changes. Read the latest value from a ref
	// instead, and keep depending on messages.length for the emptiness check.
	const latestMessagesRef = useRef(messages)
	latestMessagesRef.current = messages

	// The Undo offer currently on screen: { token, toastId }. Undo is only valid
	// while the chat it cleared into is still empty and untouched. The moment
	// anything replaces it -- the user sends a turn, loads a conversation from
	// history, or clears again -- restoring the snapshot would destroy that
	// replacement, which in incognito mode is not persisted anywhere and would
	// be gone for good. So the offer is invalidated and its toast dismissed.
	const undoOfferRef = useRef(null)

	const invalidateUndoOffer = useCallback(() => {
		const offer = undoOfferRef.current
		if (!offer) return
		undoOfferRef.current = null
		toast.dismiss(offer.toastId)
	}, [toast])

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
			getVisibleConversationId: () => activeConversationIdRef.current,
			onRunStatus: (data) => {
				if (data?.type === 'run_started' && !activeConversationIdRef.current) {
					// The run was just admitted for the chat on screen, which has no
					// saved conversation yet. Remember its first prompt so the
					// history list can name it after the user navigates away. The
					// server title wins when it has one (an attachment-only prompt
					// produces no client-side title to fall back on).
					const firstUser = latestMessagesRef.current.find(m => m.role === 'user')
					data = { ...data, title: data.title || (firstUser?.content || '').substring(0, 200) || null }
				}
				if (data?.type === 'background_activity' && data.frame?.type === 'tool_approval_request') {
					// Auto-approve is a client behaviour, and the row that performs
					// it only renders for the conversation on screen. A background
					// run would otherwise sit on every tool call until the user
					// happened to open it -- the opposite of running unattended.
					// Scoped to runs the user started: a child conversation the
					// model launched (atlas_launch) runs on model-chosen
					// arguments, and an on/off toggle must not silently broaden
					// to approving those unattended.
					const frame = data.frame
					const childRun = runs.getRun(frame.conversation_id)?.parent_run_id
					if (
						settingsRef.current?.autoApproveTools &&
						!frame.admin_required &&
						!childRun &&
						sendMessageRef.current
					) {
						const sent = sendMessageRef.current({
							type: 'tool_approval_response',
							tool_call_id: frame.tool_call_id,
							approved: true,
							arguments: frame.arguments,
							run_id: frame.run_id,
							conversation_id: frame.conversation_id,
						})
						if (!sent) {
							// The answer would be dropped; leaving the run parked with
							// no signal turns an unattended run into a silent stall.
							toast.error('Could not auto-approve a background tool call: not connected')
						}
					}
				}
				runs.handleRunFrame(data)
			},
			// The joined conversation's turn is on disk: reload now rather than
			// on the run's terminal status, which a stopped run reports before
			// its interrupted turn is persisted.
			onConversationSaved: finishJoinedRun,
		})
		return addMessageHandler(handler)
	// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [addMessageHandler, addMessage, mapMessages, agent.setCurrentAgentStep, files, triggerFileDownload, addAttachment, addPendingFileEvent, resolvePendingFileEvent, setActiveConversationId, streamToken, streamEnd, runs.handleRunFrame])

	// Refs so the message handler above (rebuilt only when its deps change)
	// sees the current settings and socket without re-subscribing per change.
	const settingsRef = useRef(settings)
	settingsRef.current = settings
	const sendMessageRef = useRef(sendMessage)
	sendMessageRef.current = sendMessage

	// The run a joined conversation was opened under has ended. Its save
	// normally arrives first (see onConversationSaved); this is the fallback
	// for a run that ended without one, after a grace period so a stop --
	// which reports `cancelled` before the interrupted turn is written --
	// does not reload a transcript the save is about to change.
	useEffect(() => {
		const id = joinedRunConversationRef.current
		if (!id || joinedRunTimerRef.current) return
		const run = runs.runsByConversation[id]
		if (run && !isRunActive(run)) {
			joinedRunTimerRef.current = setTimeout(() => {
				joinedRunTimerRef.current = null
				finishJoinedRun(id)
			}, RUN_END_RELOAD_GRACE_MS)
		}
	}, [runs.runsByConversation, finishJoinedRun])

	// Ask the server which runs are still in flight whenever the socket comes
	// up. This is what makes a run survive the browser closing in a way the
	// user can see: reopening the app repopulates the indicators instead of
	// showing a history list with no sign that an agent is still working.
	// Gated on the chat-history feature because that is also what gates a run
	// being created at all -- without it there is never anything to list.
	useEffect(() => {
		if (isConnected && sendMessage && config.features?.chat_history) {
			// Naming the open conversation lets the server replay whatever that
			// conversation's run is blocked on (a tool approval it sent while we
			// were away), which is the only way that request can still be answered.
			sendMessage({ type: 'list_runs', conversation_id: activeConversationIdRef.current || undefined })
		}
	}, [isConnected, sendMessage, config.features?.chat_history])

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
			!isPersonaKey(selections.activePromptKey) &&
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
		// A turn in the replacement chat retires any outstanding Undo: restoring
		// the old snapshot now would discard this exchange, which in incognito
		// mode is not saved anywhere and could not be recovered.
		invalidateUndoOffer()
		// Don't allow sending while the WebSocket is disconnected -- the message
		// would never reach the backend and the UI would hang on "Thinking...".
		if (!isConnected) {
			toast.error('Not connected. Waiting to reconnect before sending.')
			return false
		}
		// Agent mode with no tools selected is allowed but warned about
		// (issue #921 follow-up): the composer shows a persistent warning
		// banner, and the backend downgrades the turn to a normal chat with
		// its own in-chat note. Nothing to call is not worth blocking the
		// send over -- the user keeps their typed message either way.
		//
		// A fine-tune correction (issue #622) narrows the turn to exactly one tool
		// via selectedToolsOverride, so honor that list for the outgoing payload
		// instead of the persisted selection.
		const toolsToSend = selectedToolsOverride != null ? selectedToolsOverride : [...selectedTools]
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
		// When the RAG toggle alone expanded the list to "everything I can
		// reach", the sources were not hand-picked -- the backend must not
		// warn per turn that they were not searched (#930 review).
		const dataSourcesAuto = ragActivated && !hasSelectedSources

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

		// A preconfigured persona (issue #880) also replaces the system prompt and
		// is never an MCP prompt, but only its *id* goes on the wire: the server
		// resolves the text from its own persona folder after re-checking the
		// access group, so personas work regardless of the custom-prompt flag and
		// a client can never substitute its own text for one. The id comes from
		// the active key, not the fetched list: after a reload (or a failed
		// /api/personas fetch) the list can be empty while the persisted key is
		// still valid, and sending no id would silently run the default prompt.
		// If the persona is genuinely gone or gated away, the server resolves it
		// to None and the default prompt applies -- fail closed on the side that
		// owns the folder.
		const activeKeyIsPersona = isPersonaKey(activeKey)

		const sent = sendMessage({
			type: 'chat',
			content,
			model: currentModel,
			selected_tools: toolsToSend,
			selected_prompts: (activeKeyIsUserPrompt || activeKeyIsPersona) ? [] : activePrompts,
			custom_system_prompt: activeUserPrompt ? activeUserPrompt.content : undefined,
			persona_id: activeKeyIsPersona ? personaIdFromKey(activeKey) : undefined,
			selected_data_sources: dataSourcesToSend,
			data_sources_auto: dataSourcesAuto,
			user: config.user,
			files: { ...extraFiles, ...tagged },
			// Gate the wire flag on availability as well: useAgentMode already derives
// the effective flag, but this is the transmission boundary, so the default-on
// preference must not leak a live agent_mode even if the hook's inputs drift
// (issue #849 review).
agent_mode: agent.agentModeAvailable && agent.agentModeEnabled,
			// The payload clamp only applies once a live config response
			// confirmed the ceiling: a stale cache (or the fallback 10) can sit
			// *below* the deployment's real ceiling, and the server can only
			// clamp down -- an early turn would lose the user's intended
			// headroom (#849 review). Until then the raw preference goes out
			// and the authoritative server clamp bounds it.
			agent_max_steps: config.agentCeilingConfirmed
				? Math.min(settings.maxIterations || agent.agentMaxSteps, config.agentMaxStepsLimit || 10)
				: (settings.maxIterations || agent.agentMaxSteps),
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
		// Discard a replay placeholder left over from a reopened in-flight
		// conversation (issue #957): in a tab that receives no further frames
		// nothing else ends that stream, and STREAM_TOKEN's append lookup
		// targets the last _streaming row -- without this the new turn's reply
		// would accumulate into the stale bubble above the user's message.
		// Discard, not streamEnd: a placeholder is a transient fragment the
		// run's stored transcript supersedes, and closing it would clear
		// _replayed and let the persistence paths write the partial text into
		// history as if it were the finished reply. Scoped to placeholders:
		// ending a genuinely live bubble here (a steering send mid-run) would
		// split the segment into two bubbles.
		discardReplayPlaceholders()
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
		// The agent-run flag is deliberately untouched here. It is armed only
		// by the server's `agent_start` acknowledgement and cleared on
		// terminal events, so a fresh turn starts from the previous run's
		// settled state -- and a send into an already acknowledged run is
		// steering (issue #824): the server queues it into the live channel
		// without another `agent_start`, so clearing the flag would drop the
		// agent Stop button and block further steering mid-run (#849 review).
		return true
	}, [addMessage, mapMessages, currentModel, selectedTools, activePrompts, selectedDataSources, ragEnabled, config, selections, agent, files, isWelcomeVisible, isConnected, toast, sendMessage, settings, getAllRagSourceIds, saveMode, activeConversationId, customPromptsEnabled, userPrompts.prompts, activeWorkspaceId, cancelPendingWorkspaceRestore, invalidateUndoOffer, discardReplayPlaceholders])

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
		const isGenerating = isThinking || isSynthesizing || isStreaming
		// Issue #884: when the current conversation is a tracked run, New Chat is
		// pure navigation -- the run keeps going in the background and shows up
		// as an indicator in history. Only an untracked turn still has to be
		// stopped, because nothing else can own it once the view is cleared.
		const hasBackgroundRun = isRunActive(runs.getRun(activeConversationId))
		const mustStopCurrentTurn = isGenerating && !hasBackgroundRun
		const hasContent = messages.length > 0
		// Snapshot what the view is about to lose. Held
		// in a ref rather than state: nothing renders from it, and it must not
		// retrigger the autosave effect.
		// `id` is the REAL conversation id or nothing. Fabricating one would be
		// worse than useless: handle_restore_conversation rejects any id the
		// configured repository does not know (service.py, "Rejected restore
		// for conversation ... not found"), so a made-up id produces an error
		// frame and no re-seed while the UI happily shows the transcript back.
		// With no id we skip the backend round-trip entirely and say so.
		const undoSnapshot = (!skipConfirm && hasContent && !mustStopCurrentTurn)
			? {
				id: activeConversationId || null,
				// Replayed placeholder bubbles (issue #957) are transient -- a
				// mid-answer fragment the run's stored transcript supersedes --
				// so they are not part of what Undo puts back.
				messages: latestMessagesRef.current.filter(m => !isReplayPlaceholder(m)).map(m => buildPersistedMessage(m)),
				canvasContent: files.canvasContent || '',
				metadata: { workspace_id: conversationWorkspaceIdRef.current || null },
			}
			: null

		// A second New Chat supersedes any offer still on screen.
		invalidateUndoOffer()

		// If generation is in progress, tell the backend to cancel it *before* we
		// ask for a new session. Otherwise the in-flight task keeps streaming
		// tokens and they get appended to the fresh, empty chat (the bug users
		// see where "the first amount of the output is removed from view").
		if (sendMessage && mustStopCurrentTurn) {
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
		// The visible turn's flags belong to the conversation being left. A
		// tracked run keeps going, but its completion frames are filed as
		// background activity and never reach the flag clears, so the fresh
		// chat would otherwise inherit a Stop button and a busy indicator.
		setIsAgentRunning(false)
		joinedRunConversationRef.current = null
		if (joinedRunTimerRef.current) {
			clearTimeout(joinedRunTimerRef.current)
			joinedRunTimerRef.current = null
		}
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
		if (mustStopCurrentTurn) {
			toast.info('Response stopped. New chat started.')
		}

		// Offer Undo. The token guards against a stale toast surviving whatever
		// invalidated it (a dismiss that lost a race, a toast kept open by the
		// user): restoring here would wipe out the chat that replaced this one.
		if (undoSnapshot) {
			const token = {}
			const toastId = toast.info('New chat started.', {
				duration: 8000,
				action: {
					label: 'Undo',
					onClick: () => {
						if (undoOfferRef.current?.token !== token) return
						undoOfferRef.current = null
						const restore = restoreUndoSnapshotRef.current
						if (!restore) return
						Promise.resolve(restore(undoSnapshot)).then(() => {
							if (undoSnapshot.canvasContent) files.setCanvasContent(undoSnapshot.canvasContent)
						})
					},
				},
			})
			undoOfferRef.current = { token, toastId }
		}
		return true
	}, [resetMessages, files, sendMessage, isThinking, isSynthesizing, isStreaming, messages.length, agent, streamEnd, runs, activeConversationId, toast, invalidateUndoOffer])

	// Load a saved conversation from history into the chat view
	const loadSavedConversation = useCallback(async (conversationData) => {
		if (!conversationData || !conversationData.messages) return

		// Whatever was on offer refers to a chat that is no longer on screen.
		invalidateUndoOffer()

		// Clear current state. The per-turn flags and the token buffer belong
		// to the conversation being left: a run there continues (issue #884),
		// but nothing it emits from here on is applied to this view, so the
		// flags would never clear on their own and a buffered token fragment
		// would be flushed into the transcript loaded below.
		cleanupStreamState()
		streamEnd()
		resetMessages()
		setIsThinking(false)
		setIsSynthesizing(false)
		setIsAgentRunning(false)
		if (agent?.setCurrentAgentStep) agent.setCurrentAgentStep(0)
		if (agent?.setAgentPendingQuestion) agent.setAgentPendingQuestion(null)
		// Opened while its run is executing: the live stream is not replayed,
		// so remember to reload from the store once the run ends. The record's
		// own `in_flight` flag decides this, not the run tracker: the snapshot
		// that would carry the run may still be in flight in this tab (another
		// tab, a reload), and a null here would leave the partial view stale
		// forever.
		if (joinedRunTimerRef.current) {
			clearTimeout(joinedRunTimerRef.current)
			joinedRunTimerRef.current = null
		}
		const runInFlight = isRunActive(runs.getRun(conversationData.id)) || conversationData.in_flight === true
		joinedRunConversationRef.current = runInFlight ? conversationData.id : null
		if (runInFlight) {
			// Ask for a fresh run snapshot: this tab may not have received
			// `run_started`/`run_status` for a run its tracker has never seen,
			// and the reload-on-run-end below keys off that tracker.
			sendMessage?.({ type: 'list_runs', conversation_id: conversationData.id })
		}
		files.setCanvasContent('')
		files.setCustomUIContent(null)
		files.setSessionFiles({ total_files: 0, files: [], categories: { code: [], image: [], data: [], document: [], other: [] } })

		// Track the loaded conversation. The ref is set synchronously too: the
		// replay frame answering the restore below is tagged with the run's ids
		// and the routing gate reads the ref, so a frame that arrives before
		// the next render must already see this conversation as visible --
		// otherwise the replay is filed as background activity and dropped.
		activeConversationIdRef.current = conversationData.id
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

		// Issue #957: the run executing in this conversation has streamed text
		// this client never saw -- it was dropped as background activity while
		// another conversation was on screen, or this tab was not the one the
		// run streams into. Seed the open bubble with what the run holds so
		// the reply starts at its first word, not the token that happened to
		// be current when the user came back. The restore below replays the
		// same segment again (a newer snapshot), which replaces this seed, and
		// the live stream continues from there. Runs until the turn ends, so
		// the bubble keeps its "in progress" marker (Message.jsx) until the
		// reload that replaces it with the stored transcript.
		if (conversationData.streaming_text) {
			streamToken(conversationData.streaming_text, true)
		} else if (conversationData.in_flight === true) {
			// No open segment right now -- the run is between steps (executing
			// tools, parked on an approval). The transcript still needs a sign
			// of life: an empty bubble carrying the marker, which the next
			// segment's first token fills in this run's own tab, and the
			// run-end reload replaces everywhere.
			streamToken('', true)
		}

		// Notify backend to restore this conversation's context
		// Sends the conversation_id and messages so the LLM has prior context.
		// Display-only rows (persisted tool_call messages, issue #684, and
		// agent_intermediate narration, issue #957) are excluded: they exist
		// purely to re-render the transcript. A bare role:'tool' row with no
		// preceding tool_calls would be rejected as an orphan tool message by
		// some providers, and with no conversation repository configured the
		// client payload is canonical, so a narration row would replay as a
		// second assistant turn and break strict alternation.
		if (sendMessage) {
			sendMessage({
				type: 'restore_conversation',
				conversation_id: conversationData.id,
				messages: conversationData.messages
					.filter(msg => !DISPLAY_ONLY_MESSAGE_TYPES.includes(msg.message_type || 'chat'))
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
		// Stable members only: `runs` and `agent` are unmemoised objects that a
		// new token frame rebuilds, so the objects themselves would tear this
		// callback down -- and re-subscribe everything that depends on it -- on
		// every streaming frame.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [resetMessages, files, sendMessage, bulkAdd, restoreWorkspace, invalidateUndoOffer, streamEnd, streamToken, agent.setCurrentAgentStep, agent.setAgentPendingQuestion, runs.getRun])

	// Undo's restore. Two shapes, because the backend cannot re-seed a
	// conversation it has never stored:
	//
	//   - With a real conversation id, go through loadSavedConversation, which
	//     sends restore_conversation. The server reloads the conversation from
	//     its repository (the canonical, non-forgeable copy) into the session
	//     history, so the next turn has the full prior context.
	//   - Without one -- incognito, the default mode, where the conversation
	//     exists only in this tab -- there is nothing on the server to restore
	//     from, and the session was already reset. Put the transcript back
	//     locally and say plainly in the timeline that the assistant no longer
	//     has the earlier messages, rather than letting Undo look like a full
	//     recovery when it is not.
	const restoreUndoSnapshot = useCallback((snapshot) => {
		if (snapshot.id) {
			return loadSavedConversation({
				id: snapshot.id,
				messages: snapshot.messages,
				metadata: snapshot.metadata,
			})
		}

		resetMessages()
		setIsWelcomeVisible(false)
		const restored = snapshot.messages.map(msg => ({
			role: msg.role,
			content: msg.content || '',
			timestamp: msg.timestamp,
			type: msg.message_type || 'chat',
			...(msg.metadata || {}),
		}))
		if (restored.length > 0) bulkAdd(restored)
		// Message.jsx renders an unrecognised system subtype from `content`, not
		// `text` -- set both so the row is actually visible. A note nobody can
		// read is worse than no note: it would leave Undo looking like a full
		// recovery, which is the thing this row exists to prevent.
		const undoNote = 'Restored the previous messages. This conversation is not saved anywhere, so the assistant does not have them in context -- re-state anything it needs.'
		addMessage({
			role: 'system',
			type: 'system',
			subtype: 'info',
			content: undoNote,
			text: undoNote,
			meta: {},
			timestamp: new Date().toISOString(),
			id: `system_${Date.now()}_${generateSecureRandomString()}`,
		})
		return Promise.resolve()
	}, [loadSavedConversation, resetMessages, bulkAdd, addMessage])

	restoreUndoSnapshotRef.current = restoreUndoSnapshot

	// Ask the backend for the file by name and let it answer. The name a chat
	// or canvas download control carries is the one the tool advertised, while
	// the session file list is keyed by the sanitized name storage assigned --
	// so a client-side "is it in sessionFiles?" guard silently swallowed every
	// download of a file whose name storage had to rewrite. The backend matches
	// the two names and reports a real miss, which the handler surfaces.
	// `s3Key` is optional: a control that knows which stored file it stands for
	// passes it, and the backend answers by key. Two files can carry names that
	// reduce to the same stored name, so a row that has the key must not have
	// its bytes chosen by name matching.
	const downloadFile = useCallback((filename, s3Key) => {
		if (!filename && !s3Key) return
		// Name the conversation (and its run, when one is live). The backend
		// searches the connection session first and then this user's run
		// sessions newest-first, so an unaddressed frame could answer from a
		// different parallel conversation that produced a file of the same
		// name; naming the conversation puts its own session at the front.
		const run = runs.getRun(activeConversationId)
		sendMessage({
			type: 'download_file',
			filename,
			s3_key: s3Key || undefined,
			user: config.user,
			conversation_id: activeConversationId || undefined,
			run_id: run?.run_id || undefined,
		})
	}, [sendMessage, config.user, runs, activeConversationId])

		// Agent controls
		// Stop addresses one run (issue #884). Naming the conversation -- and the
		// run id when we have one -- is what keeps stopping conversation A from
		// stopping whatever is running in B.
		const stopAgent = useCallback(() => {
			// Hide the Stop button immediately; the backend stop is best-effort and
			// the terminal agent_completion event will also clear this.
			setIsAgentRunning(false)
			if (!sendMessage) return
			const run = runs.getRun(activeConversationId)
			sendMessage({
				type: 'agent_control',
				action: 'stop',
				conversation_id: activeConversationId || undefined,
				run_id: run?.run_id || undefined,
			})
		}, [sendMessage, runs, activeConversationId])

		// Stop non-agent streaming
		const stopStreaming = useCallback(() => {
			cleanupStreamState()
			streamEnd()
			setIsThinking(false)
			if (!sendMessage) return
			const run = runs.getRun(activeConversationId)
			sendMessage({
				type: 'stop_streaming',
				conversation_id: activeConversationId || undefined,
				run_id: run?.run_id || undefined,
			})
		}, [sendMessage, streamEnd, runs, activeConversationId])

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

	const openTranscriptInTab = useCallback((asText) => {
		if (!messages.length) { alert('No chat history to open'); return }
		const ragEnabled = config.features?.rag
		const ragSourcesDisplay = ragEnabled
			? ([...selectedDataSources].join(', ') || 'None selected')
			: 'None (RAG disabled)'

		const promptInfoByKey = buildPromptInfoByKey(config.prompts, userPrompts.prompts, personas.personas)
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
			const ts = new Date().toISOString().replace(/[:.]/g,'-').slice(0,19)
			openBlobInNewTab(blob, `chat-export-${ts}.txt`)
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
			const ts = new Date().toISOString().replace(/[:.]/g,'-').slice(0,19)
			openBlobInNewTab(blob, `chat-export-${ts}.json`)
		}
	}, [messages, config.appName, config.user, config.features, config.prompts, currentModel, selectedTools, selectedDataSources, agent.agentModeEnabled, agent.agentMaxSteps, selections.activePromptKey, files.canvasContent, userPrompts.prompts, personas.personas])

	const openChat = useCallback(() => openTranscriptInTab(false), [openTranscriptInTab])
	const openChatAsText = useCallback(() => openTranscriptInTab(true), [openTranscriptInTab])

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

			// Clear the active persona if the new context excludes it: the picker
			// hides compliance-incompatible personas and the server refuses to
			// resolve them, so keeping one selected would silently run the
			// default prompt on the next turn.
			if (isPersonaKey(selections.activePromptKey)) {
				const persona = personas.personas.find(
					p => p.id === personaIdFromKey(selections.activePromptKey)
				)
				if (!personaSurvivesComplianceFilter(persona, newLevel)) {
					clearActivePrompt()
				}
			}
		}

		// Set the new compliance level
		selections.setComplianceLevelFilter(newLevel)
	}, [selections, selectedTools, selectedPrompts, config.tools, config.prompts, personas.personas, clearActivePrompt])

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
				// Replayed placeholder bubbles (issue #957) are transient: they
				// hold a mid-answer fragment the run's stored transcript will
				// supersede, so persisting one would write the fragment into
				// local history as if it were the finished reply.
				messages: messages.filter(m => !isReplayPlaceholder(m)).map(m => buildPersistedMessage(m)),
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
		// Parallel conversation runs (issue #884): conversation_id -> run record,
		// including conversations that are not on screen.
		runsByConversation: runs.runsByConversation,
		backgroundSaves: runs.backgroundSaves,
		runEndedConversationId,
		clearRunEndedConversation,
		// Whether the conversation on screen has work in flight. Derived from
		// the run snapshot as well as the transient streaming flags, so a user
		// who reopens the browser onto a still-running conversation gets the
		// Stop control back rather than an indicator they cannot act on.
		isConversationRunActive: isRunActive(runs.runsByConversation[activeConversationId]),
		activeRunCount: runs.activeRunCount,
		maxConcurrentRuns: runs.maxConcurrentRuns,
		getConversationRun: runs.getRun,
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
		// Admin-preconfigured personas (issue #880)
		personas: personas.personas,
		personasLoading: personas.loading,
		personasError: personas.error,
		fetchPersonas: personas.fetchPersonas,
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
		agentMaxStepsLimit: config.agentMaxStepsLimit,
		// True only once a live config response confirmed the ceiling; the
		// cache alone must not drive persisted clamps (#849 review).
		agentCeilingConfirmed: config.agentCeilingConfirmed,
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
		// A run rediscovered after a reconnect has no streaming state behind it,
		// but it is still running and must still be stoppable (issue #884).
		isAgentRunning: isAgentRunning || isRunActive(runs.runsByConversation[activeConversationId]),
		isSynthesizing,
		sendChatMessage,
		rewindAndResubmit,
		sendCaptureCorrection,
		clearChat,
		stopAgent,
		stopStreaming,
		isStreaming,
		answerAgentQuestion,
		openChat,
		openChatAsText,
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
