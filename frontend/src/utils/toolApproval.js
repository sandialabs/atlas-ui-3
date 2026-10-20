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
  return Boolean(settings?.autoApproveTools && !message?.admin_required)
}

// One wording for what Auto-Approve Tool Calls does, shared by the settings
// toggle, the Active Tools strip and the approval row, so the three never
// disagree about what "off" means. No app name: deployments rename the app.
//
// Scoped to what the client actually decides. The backend can still skip the
// prompt on its own (a policy hook that auto-approves, an agent launch whose
// approval covers its sub-conversation), and a hook that escalates a call to
// "ask" sends admin_required: false, so auto-approve answers it like any
// other non-admin call. Only tools an administrator marked (per-tool
// require_approval, or FORCE_TOOL_APPROVAL_GLOBALLY) are guaranteed to ask.
export const AUTO_APPROVE_OFF_TEXT =
  'Tool calls ask for your approval before they run, unless your deployment\'s policy or an agent launch you approved already covers them. This is the default.'
export const AUTO_APPROVE_ON_TEXT =
  'Tool calls run without asking, except tools an administrator has marked as always requiring approval.'
export const describeAutoApprove = (on) => (on ? AUTO_APPROVE_ON_TEXT : AUTO_APPROVE_OFF_TEXT)

// Why a pending approval row is asking at all. A pending row whose
// `auto_approved` is recorded as false is one the automatic send already
// tried and failed (socket closed); keyed on the row, not just the live
// setting, so a row is never mislabelled by a later toggle.
export const approvalReason = (message, settings) => {
  if (message?.admin_required) {
    return 'An administrator has marked this tool as always requiring approval, so it asks even with auto-approve on.'
  }
  if (message?.auto_approved === false && settings?.autoApproveTools) {
    return 'Auto-approve is on, but this call could not be approved automatically. Approve or reject it here.'
  }
  return 'Asking because Auto-Approve Tool Calls is off. You can turn it on from the Active Tools bar or in Tools and Settings > General.'
}
