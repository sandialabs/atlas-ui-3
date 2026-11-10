/* eslint-disable react-refresh/only-export-components -- build-only contrast fixture, never hot-reloaded */
/** Contrast-fixture stand-in for src/contexts/MarketplaceContext.jsx. */
export function useMarketplace() {
  return {
    complianceLevels: [], isComplianceAccessible: () => true,
    selectedServers: new Set(['calculator']), toggleServer: () => {},
    isServerSelected: name => name === 'calculator', deselectAllServers: () => {},
  }
}
export function MarketplaceProvider({ children }) {
  return children
}
