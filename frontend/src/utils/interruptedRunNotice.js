// Notice shown in the transcript when the server ended a run the user did not
// stop (issue #1218).
//
// A run whose cluster-wide claim lapsed without proof (the coordination
// service was unreachable for a whole lease, or refused the claim when it
// came back) is cancelled on its replica, and its partial turn is not saved.
// Without a notice the conversation just goes idle with the finished tool rows
// and no answer. The server reports the outcome in `run_status.end_reason`;
// a normal Stop reports `user_cancelled`, and a shutdown saves its turn, so
// neither gets a notice.

const RESEND = 'Send your message again to retry.'

const NOTICES = {
  fenced:
    'This response was interrupted because the server lost contact with its coordination service for too long. '
    + `The work it had done was not saved. ${RESEND}`,
}

/**
 * The notice text for a run that ended this way, or null when the run needs
 * none (still live, completed, stopped by the user, ...).
 */
export function interruptedRunNotice(run) {
  if (!run || run.status !== 'failed') return null
  return NOTICES[run.end_reason] || null
}
