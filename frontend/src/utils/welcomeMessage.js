// Welcome screen instructional text (issue #1072).
//
// Deployments can override the paragraph under the logo at frontend build time
// with VITE_WELCOME_MESSAGE. The value is rendered as plain text by React, so
// any markup in it is displayed literally, never interpreted as HTML.

export const DEFAULT_WELCOME_MESSAGE =
  'Select a model and start chatting. To explore available tools, click the Wrench icon or to view files, click the Folder icon in the top right.'

// Return the configured message, or the default when unset or blank.
export function resolveWelcomeMessage(configured) {
  if (typeof configured !== 'string') return DEFAULT_WELCOME_MESSAGE
  const trimmed = configured.trim()
  return trimmed || DEFAULT_WELCOME_MESSAGE
}
