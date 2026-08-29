/**
 * Auto-size the chat composer textarea without disturbing the transcript's
 * scroll position (#866).
 *
 * Measuring a textarea's natural height requires collapsing it to `height:auto`
 * and reading `scrollHeight`. The composer is a flex sibling of the scrollable
 * message list, so that collapse momentarily *grows* the list's viewport, which
 * shrinks its scrollable range and makes the browser clamp `scrollTop`. The
 * clamp survives the re-expansion, so the previously rendered response visibly
 * drops by a line or two on every keystroke before some later auto-scroll snaps
 * it back up.
 *
 * The whole measurement happens inside one task, so nothing is painted in the
 * collapsed state -- restoring the scroll position before returning makes the
 * displacement unobservable.
 *
 * @param {HTMLTextAreaElement|null} textarea composer element to size
 * @param {HTMLElement|null} scrollContainer scrollable transcript element
 * @param {number} maxHeight cap in px, matching the element's CSS max-height
 * @returns {string} the height that was applied (empty string when no element)
 */
export function autoResizeComposer(textarea, scrollContainer, maxHeight = 128) {
  if (!textarea) return ''

  const canRestore =
    scrollContainer &&
    typeof scrollContainer.scrollTop === 'number' &&
    typeof scrollContainer.scrollHeight === 'number' &&
    typeof scrollContainer.clientHeight === 'number'

  const prevScrollTop = canRestore ? scrollContainer.scrollTop : 0
  // Treat "within a pixel of the bottom" as pinned: re-pin rather than restore a
  // stale offset, so a transcript that is following along stays at the bottom.
  const wasPinnedToBottom = canRestore
    ? scrollContainer.scrollHeight -
        (scrollContainer.scrollTop + scrollContainer.clientHeight) <=
      1
    : false

  textarea.style.height = 'auto'
  const nextHeight = Math.min(textarea.scrollHeight, maxHeight) + 'px'
  textarea.style.height = nextHeight

  if (canRestore) {
    if (wasPinnedToBottom) {
      scrollContainer.scrollTop = scrollContainer.scrollHeight
    } else if (scrollContainer.scrollTop !== prevScrollTop) {
      scrollContainer.scrollTop = prevScrollTop
    }
  }

  return nextHeight
}
