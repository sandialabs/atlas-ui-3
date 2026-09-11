import { useCallback, useRef, useState } from 'react'

/**
 * Tracks conversation runs the user has in flight (issue #884).
 *
 * A "run" is one execution of a conversation on the server. Several may be
 * running at once, in conversations the user is not currently looking at, so
 * this state is deliberately keyed by conversation id rather than folded into
 * the single global "is the chat busy" flags: the history list needs to render
 * an indicator for a conversation that is not on screen, and Stop needs to
 * address one specific run.
 *
 * Terminal runs are kept rather than deleted so a conversation can still show
 * how its last run ended ("failed", "stopped") after it finished.
 */

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])

export function isRunActive(run) {
	return !!run && !TERMINAL_STATUSES.has(run.status)
}

export function useConversationRuns() {
	// conversation_id -> run record from the server.
	const [runsByConversation, setRunsByConversation] = useState({})
	const [maxConcurrentRuns, setMaxConcurrentRuns] = useState(null)
	// Mirror of the map for callbacks that must not re-subscribe on every
	// status frame (the websocket handler is rebuilt when its deps change).
	const runsRef = useRef({})

	const applyRuns = useCallback((updater) => {
		setRunsByConversation(prev => {
			const next = updater(prev)
			runsRef.current = next
			return next
		})
	}, [])

	const handleRunFrame = useCallback((data) => {
		if (!data) return
		if (data.type === 'runs_snapshot') {
			// Sent in reply to `list_runs`, i.e. on (re)connect. This is how a
			// browser that was closed while an agent was working learns that the
			// run is still going -- or how it ended while the tab was shut.
			const next = {}
			for (const run of data.runs || []) {
				const existing = next[run.conversation_id]
				// Several runs can share a conversation over time; the newest wins.
				if (!existing || (run.created_at || 0) >= (existing.created_at || 0)) {
					next[run.conversation_id] = run
				}
			}
			runsRef.current = next
			setRunsByConversation(next)
			if (typeof data.max_concurrent_runs_per_user === 'number') {
				setMaxConcurrentRuns(data.max_concurrent_runs_per_user)
			}
			return
		}
		if (data.type === 'run_started') {
			applyRuns(prev => ({
				...prev,
				[data.conversation_id]: {
					run_id: data.run_id,
					conversation_id: data.conversation_id,
					status: 'running',
				},
			}))
			return
		}
		if (data.type === 'run_status' && data.run) {
			applyRuns(prev => ({ ...prev, [data.run.conversation_id]: data.run }))
			return
		}
		if (data.type === 'background_activity' && data.conversation_id) {
			// An event arrived for a conversation that is not on screen. The run
			// record may not have reached this tab yet (the socket connected
			// after the run started), so record enough to show the indicator.
			applyRuns(prev => (
				prev[data.conversation_id]
					? prev
					: {
						...prev,
						[data.conversation_id]: {
							run_id: data.run_id || null,
							conversation_id: data.conversation_id,
							status: 'running',
						},
					}
			))
		}
	}, [applyRuns])

	const getRun = useCallback((conversationId) => (
		conversationId ? runsRef.current[conversationId] || null : null
	), [])

	const activeRunCount = Object.values(runsByConversation).filter(isRunActive).length

	return {
		runsByConversation,
		maxConcurrentRuns,
		activeRunCount,
		handleRunFrame,
		getRun,
	}
}
