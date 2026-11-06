// Whether a tool call was (or is about to be) approved without a human in the
// loop. The approval decision is history, so it is persisted on the message as
// `auto_approved` once it is made, and that persisted value always wins — a
// transcript must not re-render differently just because the user later toggled
// the auto-approve setting (#762). The live setting is only consulted for a row
// that is still pending, i.e. before the decision has been recorded; a resolved
// row without the field predates it and is treated as a manual approval.
export const resolveAutoApproved = (message, settings) => {
  if (typeof message?.auto_approved === 'boolean') return message.auto_approved
  if (message?.status !== 'pending') return false
  return clientMayAutoApprove(message, settings)
}

// Whether the auto-approve switch may answer this request in the browser.
// The server sends `auto_approvable: false` for a request it deliberately
// left on the gate even with the switch on: a policy hook escalated it to
// "ask", or it belongs to a model-launched child conversation. Answering it
// here would undo that. Admin-required tools are never the switch's to answer.
export const clientMayAutoApprove = (message, settings) => Boolean(
  settings?.autoApproveTools &&
  !message?.admin_required &&
  message?.auto_approvable !== false
)

// One wording for what Auto-Approve Tool Calls does, shared by the settings
// toggle, the Active Tools strip and the approval row, so the three never
// disagree about what "off" means. No app name: deployments rename the app.
//
// The server applies the same switch without a connected browser. This copy
// still describes the boundary the switch does not cross: it never answers a
// tool an administrator marked (per-tool require_approval, or
// FORCE_TOOL_APPROVAL_GLOBALLY), a call a policy hook escalated to "ask", or a
// tool call in a conversation an agent launched. A policy hook or an agent
// launch can still skip the prompt on its own. The copy makes no "always"
// promise.
export const AUTO_APPROVE_OFF_TEXT =
  'Tool calls ask for your approval before they run, unless your deployment\'s policy or an agent launch you approved already covers them. This is the default.'
export const AUTO_APPROVE_ON_TEXT =
  'Tool calls run without asking, including after you leave the page or close the browser. Auto-approve never answers tools an administrator has marked as requiring approval; those still ask.'
export const describeAutoApprove = (on) => (on ? AUTO_APPROVE_ON_TEXT : AUTO_APPROVE_OFF_TEXT)

// Why a pending approval row is asking at all. A pending row whose
// `auto_approved` is recorded as false is one the automatic send already
// tried and failed (socket closed); keyed on the row, not just the live
// setting, so a row is never mislabelled by a later toggle.
export const approvalReason = (message, settings) => {
  if (message?.admin_required) {
    return 'An administrator has marked this tool as requiring approval, so auto-approve does not answer it.'
  }
  if (message?.auto_approvable === false && settings?.autoApproveTools) {
    return 'Auto-approve is on, but this call needs your answer: a policy check asked for review, or it runs in a conversation an agent launched.'
  }
  if (message?.auto_approved === false && settings?.autoApproveTools) {
    return 'Auto-approve is on, but this call could not be approved automatically. Approve or reject it here.'
  }
  // Points only at Settings: the Active Tools bar is hidden when no tools
  // are selected, which is exactly when an agent launch can still ask.
  return 'Asking because Auto-Approve Tool Calls is off. You can turn it on in Tools and Settings > General.'
}
