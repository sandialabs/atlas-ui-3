import { useEffect, useState, useRef } from 'react'
import { usePersistentState } from './usePersistentState'

export function useAgentMode(available = true) {
  const [agentModeEnabled, setAgentModeEnabled] = usePersistentState('chatui-agent-mode-enabled', false)
  const [agentMaxSteps, setAgentMaxSteps] = usePersistentState('chatui-agent-max-steps', 5)
  const [currentAgentStep, setCurrentAgentStep] = usePersistentState('chatui-agent-current-step', 0)
  const [agentPendingQuestion, setAgentPendingQuestion] = useState(null)
  const previousAvailable = useRef(available)

  // If availability turns off (but not on initial load), force-disable stored state
  useEffect(() => {
    if (!available && previousAvailable.current && agentModeEnabled) {
      setAgentModeEnabled(false)
    }
    previousAvailable.current = available
  }, [available, agentModeEnabled, setAgentModeEnabled])

  return {
    agentModeEnabled,
    setAgentModeEnabled,
    agentMaxSteps,
    setAgentMaxSteps,
    currentAgentStep,
    setCurrentAgentStep,
  agentPendingQuestion,
  setAgentPendingQuestion,
    agentModeAvailable: available
  }
}
