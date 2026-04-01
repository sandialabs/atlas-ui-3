/**
 * Format a token count into a human-readable string.
 * @param {number|null|undefined} tokens
 * @returns {string|null}
 */
export function formatContextWindow(tokens) {
  if (tokens == null) return null
  if (tokens >= 1000000) return `${(tokens / 1000000).toFixed(1)}M tokens`
  if (tokens >= 1000) return `${Math.round(tokens / 1000)}K tokens`
  return `${tokens} tokens`
}
