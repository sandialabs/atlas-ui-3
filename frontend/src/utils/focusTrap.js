/**
 * Elements a focus trap may move focus through.
 *
 * Shared by every overlay trap (SettingsPanel's dialog stack, the RAG drawer)
 * so the copies cannot drift: a selector that forgets, say, `[href]` links
 * would silently drop them from the trap's rotation in one overlay only.
 */
export const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
