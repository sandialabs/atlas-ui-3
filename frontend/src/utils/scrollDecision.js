// Scrolling up is an immediate request to stop following, even within the
// near-bottom tolerance. A later downward scroll into that tolerance resumes it.
export const NEAR_BOTTOM_PX = 8

export function followAfterScroll(following, previousTop, { scrollTop, scrollHeight, clientHeight }) {
  const movement = scrollTop - previousTop
  if (movement < -0.5) return false
  if (movement > 0.5 && scrollHeight - clientHeight - scrollTop <= NEAR_BOTTOM_PX) return true
  return following
}
