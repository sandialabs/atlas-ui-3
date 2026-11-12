/* eslint-disable react-refresh/only-export-components -- build-only contrast fixture, never hot-reloaded */
/** Contrast-fixture stand-in for src/contexts/MarketplaceContext.jsx. */
import { useChat } from './ChatContext'

export function useMarketplace() {
  // Every server in the stubbed chat config counts as selected.
  const { tools = [], prompts = [] } = useChat()
  return {
    complianceLevels: [], isComplianceAccessible: () => true,
    selectedServers: new Set(['calculator']), toggleServer: () => {},
    isServerSelected: name => name === 'calculator', deselectAllServers: () => {},
    getFilteredTools: () => tools, getFilteredPrompts: () => prompts,
    getComplianceFilteredTools: () => tools, getComplianceFilteredPrompts: () => prompts,
  }
}
export const useOptionalMarketplace = useMarketplace
export function MarketplaceProvider({ children }) {
  return children
}
