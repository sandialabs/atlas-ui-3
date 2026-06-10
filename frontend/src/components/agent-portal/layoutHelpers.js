// Pure layout helpers shared between AgentPortal and the test suite.
//
// The layout state is intentionally simple:
//   { mode: 'single' | '2x2' | '3x2' | 'focus+strip',
//     slots: [process_id|null, ...] }
//
// Slot count is fixed per mode. Resizing the array on a mode change
// preserves as many existing process_id assignments as possible from
// the front of the list, then null-pads the rest.

import { LAYOUT_MODES, SLOT_COUNT_BY_MODE } from './layoutConstants'

export const DEFAULT_LAYOUT = { mode: 'single', slots: [null] }

export function isValidLayoutMode(mode) {
  return LAYOUT_MODES.includes(mode)
}

export function normalizeLayout(input) {
  if (!input || typeof input !== 'object') return { ...DEFAULT_LAYOUT }
  const mode = isValidLayoutMode(input.mode) ? input.mode : 'single'
  const slotCount = SLOT_COUNT_BY_MODE[mode]
  const slots = Array.isArray(input.slots) ? input.slots.slice(0, slotCount) : []
  while (slots.length < slotCount) slots.push(null)
  // Coerce non-string entries to null so a stale layout with shape drift
  // doesn't blow up the renderer.
  return {
    mode,
    slots: slots.map((s) => (typeof s === 'string' && s ? s : null)),
  }
}

export function setLayoutMode(layout, nextMode) {
  if (!isValidLayoutMode(nextMode)) return layout
  if (layout.mode === nextMode) return layout
  const slotCount = SLOT_COUNT_BY_MODE[nextMode]
  const slots = layout.slots.slice(0, slotCount)
  while (slots.length < slotCount) slots.push(null)
  return { mode: nextMode, slots }
}

export function placeProcessInLayout(layout, processId, preferredSlot = null) {
  if (!processId) return layout
  // Already present? leave it where it is.
  if (layout.slots.includes(processId)) return layout
  const slots = [...layout.slots]
  if (
    preferredSlot != null
    && preferredSlot >= 0
    && preferredSlot < slots.length
    && slots[preferredSlot] == null
  ) {
    slots[preferredSlot] = processId
    return { ...layout, slots }
  }
  // First empty slot.
  const idx = slots.indexOf(null)
  if (idx >= 0) {
    slots[idx] = processId
    return { ...layout, slots }
  }
  // No empty slot — drop into slot 0 (caller already past the hard cap).
  slots[0] = processId
  return { ...layout, slots }
}

export function clearProcessFromLayout(layout, processId) {
  if (!processId || !layout.slots.includes(processId)) return layout
  return { ...layout, slots: layout.slots.map((s) => (s === processId ? null : s)) }
}

export function clearSlot(layout, slotIndex) {
  if (slotIndex < 0 || slotIndex >= layout.slots.length) return layout
  if (layout.slots[slotIndex] == null) return layout
  const slots = [...layout.slots]
  slots[slotIndex] = null
  return { ...layout, slots }
}

export function moveProcessToSlot(layout, processId, targetSlot) {
  if (!processId) return layout
  if (targetSlot < 0 || targetSlot >= layout.slots.length) return layout
  const slots = [...layout.slots]
  // If the process is already in this slot, no-op.
  if (slots[targetSlot] === processId) return layout
  // Remove from any other slot.
  for (let i = 0; i < slots.length; i++) {
    if (slots[i] === processId) slots[i] = null
  }
  // If target slot is occupied, swap with the source's old position
  // (no-op if process wasn't already in the layout).
  slots[targetSlot] = processId
  return { ...layout, slots }
}

export function countLiveSlots(layout) {
  return layout.slots.filter((s) => !!s).length
}
