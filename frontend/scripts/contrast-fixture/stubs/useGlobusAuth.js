/** Contrast-fixture stand-in for src/hooks/useGlobusAuth.js: no network. */
export function useGlobusAuth() {
  return {
    authStatus: null, loading: false,
    // Only visible where a fixture region turns features.globus_auth on.
    error: 'Globus sign-in failed: the authorization code expired. Try again.',
    fetchAuthStatus: () => {}, login: () => {}, logout: () => {}, isAuthenticated: false,
  }
}
