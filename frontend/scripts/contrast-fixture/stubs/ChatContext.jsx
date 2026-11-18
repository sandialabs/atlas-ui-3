/* eslint-disable react-refresh/only-export-components -- build-only contrast fixture, never hot-reloaded */
/**
 * Contrast-fixture stand-in for src/contexts/ChatContext.jsx.
 *
 * The fixture mounts shipped components (Message, ToolApprovalMessage,
 * Header, SettingsPanel, ...) without a backend, WebSocket, or config fetch.
 * useChat() returns static, inert data; every action is a no-op.
 */
import { createContext, useContext } from 'react'

const noop = () => {}
const asyncNoop = async () => null

export const BASE_CHAT = {
  appName: 'ATLAS',
  user: 'reviewer@example.com',
  settings: { compactMessages: true, autoApproveTools: false, debugMode: false, reduceHelpText: false },
  updateSettings: noop,
  features: { tools: false, rag: false, custom_prompts: false, chat_history: true, workspaces: false, retro_themes: true },
  help: { mode: 'local', requested_mode: 'local', content_url: '', error: null },
  agentModeAvailable: true,
  agentModeEnabled: false,
  setAgentModeEnabled: noop,
  agentMaxStepsLimit: 10,
  agentCeilingConfirmed: true,
  isInAdminGroup: false,
  saveMode: 'none',
  setSaveMode: noop,
  openChatAsText: noop,
  messages: [],
  clearChat: noop,
  complianceLevelFilter: null,
  setComplianceLevelFilter: noop,
  complianceRequired: false,
  activeConversationClassification: null,
  conversationAccepted: false,
  selectedDataSources: new Set(),
  isSynthesizing: false,
  downloadFile: noop,
  sendApprovalResponse: () => true,
  updateToolResult: noop,
  workspaces: [],
  activeWorkspace: null,
  setActiveWorkspace: noop,
  createWorkspace: asyncNoop,
  updateWorkspace: asyncNoop,
  deleteWorkspace: asyncNoop,
  clearActiveWorkspace: noop,
  userPrompts: [],
  personas: [],
  prompts: [],
  models: [],
  currentModel: '',
  setCurrentModel: noop,
}

const FixtureChat = createContext(BASE_CHAT)

export function FixtureChatProvider({ value, children }) {
  return <FixtureChat.Provider value={{ ...BASE_CHAT, ...value }}>{children}</FixtureChat.Provider>
}

export function useChat() {
  return useContext(FixtureChat)
}

export function ChatProvider({ children }) {
  return children
}
