/** Contrast-fixture stand-in for src/hooks/useGlobusAuth.js: no network. */
export function useGlobusAuth() {
  return {
    authStatus: null, loading: false, error: null,
    fetchAuthStatus: () => {}, login: () => {}, logout: () => {}, isAuthenticated: false,
  }
}
