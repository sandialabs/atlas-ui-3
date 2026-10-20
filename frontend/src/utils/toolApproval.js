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
// disagree about what "off" means.
export const AUTO_APPROVE_OFF_TEXT =
  'Off (default): Atlas asks you to approve each tool call before it runs.'
export const AUTO_APPROVE_ON_TEXT =
  'On: tool calls run without asking, except tools an administrator requires approval for. Those always ask.'
export const describeAutoApprove = (on) => (on ? AUTO_APPROVE_ON_TEXT : AUTO_APPROVE_OFF_TEXT)

// Why a pending approval row is asking at all.
export const approvalReason = (message) => (message?.admin_required
  ? 'An administrator requires approval for this tool, even with auto-approve on.'
  : 'Asking because Auto-Approve Tool Calls is off. You can turn it on from the Active Tools bar or in Tools and Settings > General.')
