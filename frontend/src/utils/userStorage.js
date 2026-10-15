/**
 * Per-user browser storage keys (issue #1050).
 *
 * Several people can sign in on one browser profile (kiosks, lab machines,
 * testing several accounts), so anything remembered for "the user" -- the
 * selected model, the splash acceptance, the compliance filter -- is keyed by
 * who is signed in rather than shared by everyone on that browser.
 */

// What useChatConfig reports before /api/config has said who is signed in,
// and after it failed to. Neither names a person, so nothing is read or
// written for them.
const PLACEHOLDER_USERS = new Set(['', 'Unknown', 'Unauthenticated'])

/** Whether `user` names the signed-in person rather than a loading placeholder. */
export function isKnownUser(user) {
  return typeof user === 'string' && !PLACEHOLDER_USERS.has(user)
}

/** The storage key for `base` scoped to `user`. */
export function userStorageKey(base, user) {
  return `${base}:${user || ''}`
}
