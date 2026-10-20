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
export const AUTO_APPROVE_OFF_TEXT =
  'You are asked to approve each tool call before it runs. This is the default.'
export const AUTO_APPROVE_ON_TEXT =
  'Tool calls run without asking, except tools an administrator requires approval for, which always ask.'
export const describeAutoApprove = (on) => (on ? AUTO_APPROVE_ON_TEXT : AUTO_APPROVE_OFF_TEXT)

// Why a pending approval row is asking at all. With auto-approve on, a
// non-admin row only renders when the automatic send failed (socket closed),
// so it must not claim the setting is off.
export const approvalReason = (message, settings) => {
  if (message?.admin_required) {
    return 'An administrator requires approval for this tool, even with auto-approve on.'
  }
  if (settings?.autoApproveTools) {
    return 'Auto-approve is on, but this call could not be approved automatically. Approve or reject it here.'
  }
  return 'Asking because Auto-Approve Tool Calls is off. You can turn it on from the Active Tools bar or in Tools and Settings > General.'
}
