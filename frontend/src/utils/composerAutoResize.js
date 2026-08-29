/**
 * Sizing the chat composer without disturbing the transcript's scroll
 * position (#866).
 *
 * Measuring a textarea's natural height requires collapsing it to `height:auto`
 * and reading `scrollHeight`. The composer is a flex sibling of the scrollable
 * message list, so that collapse momentarily *grows* the list's viewport, which
 * shrinks its scrollable range and makes the browser clamp `scrollTop`. The
 * clamp survives the re-expansion, so the previously rendered response visibly
 * drops by a line or two on every keystroke before some later auto-scroll snaps
 * it back up.
 *
 * Every height mutation therefore goes through `withPreservedScroll`, which
 * snapshots the transcript's offset and restores it once the height is settled.
 * The whole thing runs inside one task, so nothing is painted in the
 * intermediate state and the displacement is unobservable.
 */

/**
 * Height cap for the composer, in px. Shared by the measurement, the call sites
 * and the element's inline `max-height` so the three cannot drift apart.
 */
export const COMPOSER_MAX_HEIGHT = 128

/**
 * Run `mutateHeight` with the transcript's scroll position held steady.
 *
 * @param {HTMLElement|null} scrollContainer scrollable transcript element
 * @param {() => string} mutateHeight applies the height, returns what it set
 * @returns {string} whatever `mutateHeight` returned
 */
function withPreservedScroll(scrollContainer, mutateHeight) {
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

  const applied = mutateHeight()

  if (canRestore) {
    if (wasPinnedToBottom) {
      scrollContainer.scrollTop = scrollContainer.scrollHeight
    } else if (scrollContainer.scrollTop !== prevScrollTop) {
      scrollContainer.scrollTop = prevScrollTop
    }
  }

  return applied
}

/**
 * Grow or shrink the composer to fit its content, capped at `maxHeight`.
 *
 * @param {HTMLTextAreaElement|null} textarea composer element to size
 * @param {HTMLElement|null} scrollContainer scrollable transcript element
 * @param {number} maxHeight cap in px, matching the element's CSS max-height
 * @returns {string} the height that was applied (empty string when no element)
 */
export function autoResizeComposer(
  textarea,
  scrollContainer,
  maxHeight = COMPOSER_MAX_HEIGHT
) {
  if (!textarea) return ''
  return withPreservedScroll(scrollContainer, () => {
    textarea.style.height = 'auto'
    const nextHeight = Math.min(textarea.scrollHeight, maxHeight) + 'px'
    textarea.style.height = nextHeight
    return nextHeight
  })
}

/**
 * Collapse the composer back to a single row after a message is sent.
 *
 * This clears the inline height rather than re-measuring: it runs immediately
 * after `setInputValue('')`, before React has written the empty value to the
 * DOM, so a measurement here would still see the sent text and keep the
 * composer tall. Dropping to the stylesheet's `rows=1` / `min-height` is both
 * correct and independent of when the value lands.
 *
 * @param {HTMLTextAreaElement|null} textarea composer element to reset
 * @param {HTMLElement|null} scrollContainer scrollable transcript element
 * @returns {string} the height that was applied (empty string = stylesheet)
 */
export function resetComposerHeight(textarea, scrollContainer) {
  if (!textarea) return ''
  return withPreservedScroll(scrollContainer, () => {
    textarea.style.height = ''
    return ''
  })
}
