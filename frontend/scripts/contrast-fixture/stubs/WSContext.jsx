/* eslint-disable react-refresh/only-export-components -- build-only contrast fixture, never hot-reloaded */
/** Contrast-fixture stand-in for src/contexts/WSContext.jsx: always connected. */
export function useWS() {
  return { isConnected: true, connectionStatus: 'connected', sendMessage: () => true, addMessageHandler: () => () => {} }
}
export function WSProvider({ children }) {
  return children
}
