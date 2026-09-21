// The scroll decision ChatArea makes when the message list changes.
//
// It lives here, rather than inline in the component, so the tests exercise
// the expression the app actually ships. It was previously restated in
// `autoscroll-streaming.test.js`, which meant deleting a clause from the
// component left the suite green and the rule effectively untested.
//
// Pure: no DOM, no refs. `scrollToBottom` still owns the "has the user
// scrolled away" check, which is expressed by `shouldActuallyScroll`.

// Decide whether a message-list change should force the viewport to the
// bottom, and whether this is a mid-stream token update.
//
//   - A genuinely new message from the assistant forces the scroll.
//   - A streaming token update (same message, content growing) never forces
//     it: the reader keeps their place so they can read earlier output (#441).
//   - A transcript refresh (issue #959) appends the rows a background run
//     wrote while this conversation was off screen. That is a catch-up, not a
//     new answer arriving live, so it does not force the scroll either --
//     otherwise a reader who is scrolled up is yanked back to the bottom.
export function computeMessageChangeScroll(messages, prevMessageCount) {
  const newCount = messages.length
  const lastMsg = messages[messages.length - 1]
  const isNewMessage = newCount !== prevMessageCount
  const isStreamingUpdate = Boolean(lastMsg && lastMsg._streaming && !isNewMessage)

  // Find the last settled row, skipping any trailing open bubble. A refresh
  // that lands while another run is still writing keeps that run's bubble,
  // and REFRESH_APPEND puts it *after* the appended tail -- so the marked
  // catch-up row is no longer last. Reading only `lastMsg` here would miss
  // the marker and force the scroll, yanking precisely the scrolled-up
  // reader this is meant to protect.
  let anchorIdx = messages.length - 1
  while (anchorIdx >= 0 && messages[anchorIdx]._streaming) anchorIdx -= 1
  const anchor = anchorIdx >= 0 ? messages[anchorIdx] : undefined

  const isTranscriptRefresh = Boolean(anchor && anchor._transcriptRefresh === true)
  const force = Boolean(isNewMessage && lastMsg && lastMsg.role !== 'user' && !isTranscriptRefresh)

  if (isStreamingUpdate) return { force: false, isStreamingUpdate: true }
  return { force, isStreamingUpdate: false }
}

// Whether scrollToBottom actually moves the viewport: a reader who has
// scrolled away is only overridden by a forced scroll.
export function shouldActuallyScroll(force, userScrolledAway) {
  if (userScrolledAway && !force) return false
  return true
}
