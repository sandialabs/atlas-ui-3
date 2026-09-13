import { useState } from 'react'
import { usePersistentState } from './usePersistentState'

export function useAgentMode(available = true) {
  // Raw persisted preference (issue #849): on by default, and a stored choice
  // always wins -- only a browser that never made a choice starts enabled.
  const [storedEnabled, setAgentModeEnabled] = usePersistentState('chatui-agent-mode-enabled', true)
  const [agentMaxSteps, setAgentMaxSteps] = usePersistentState('chatui-agent-max-steps', 5)
  const [currentAgentStep, setCurrentAgentStep] = usePersistentState('chatui-agent-current-step', 0)
  const [agentPendingQuestion, setAgentPendingQuestion] = useState(null)

  // Effective flag: the deployment's availability gates the stored preference
  // instead of overwriting it. A feature-disabled deployment therefore sees
  // "off" everywhere (the toggle is hidden anyway) without silently flipping a
  // user's saved choice, and the preference re-applies if the feature returns.
  const agentModeEnabled = available && storedEnabled

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