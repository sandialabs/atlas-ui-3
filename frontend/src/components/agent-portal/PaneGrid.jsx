// Multi-pane layout grid. Renders 1, 4, 6, or "focus + strip" cells
// using plain CSS grid templates — no docking lib, no drag-resize. The
// goal is to cover the 95% of useful layouts with zero runtime cost,
// not to ship tmux's split-resize.
//
// Slots are addressed by index. Cells are React-keyed on slot index
// (NOT process id) so dragging / swapping a process between slots
// reuses the same Pane instance and does not remount xterm. The Pane
// itself reopens its WebSocket when its `process.id` changes.

import Pane from './Pane'
import { SLOT_COUNT_BY_MODE } from './layoutConstants'


/**
 * PaneGrid — render the right-hand multi-pane area.
 *
 * Props:
 *   mode               - one of LAYOUT_MODES.
 *   slots              - array of process_id-or-null, length = SLOT_COUNT_BY_MODE[mode].
 *   processesById      - { [id]: processSummary }
 *   focusedSlot        - integer slot index that has the visible focus ring.
 *   fullscreenSlot     - integer slot index to render fullscreen (overrides mode).
 *   onFocusSlot        - (slotIndex) => void
 *   onCloseSlot        - (slotIndex) => void  (clears that slot)
 *   onFullscreenSlot   - (slotIndex) => void  (toggle)
 *   onRenameProcess    - (id, newName) => void
 *   onProcessUpdate    - (summary) => void
 */
function PaneGrid({
  mode,
  slots,
  processesById,
  focusedSlot,
  fullscreenSlot,
  onFocusSlot,
  onCloseSlot,
  onFullscreenSlot,
  onRenameProcess,
  onProcessUpdate,
  syncedGroupIds = [],
}) {
  const syncedSet = syncedGroupIds && syncedGroupIds.length > 0
    ? new Set(syncedGroupIds)
    : null
  // Fullscreen short-circuits the layout — only render the one cell,
  // chrome-free. Other panes' Pane components stay alive only if the
  // *parent* keeps them mounted; we deliberately do NOT keep them
  // mounted in fullscreen mode because the WS keeps streaming on the
  // server side regardless, and remounting on exit is cheap (server
  // history buffer replays the visible scrollback).
  if (typeof fullscreenSlot === 'number' && slots[fullscreenSlot]) {
    const procId = slots[fullscreenSlot]
    const process = processesById[procId] || null
    const syncEnabled = syncedSet && process?.group_id ? syncedSet.has(process.group_id) : false
    return (
      <div className="h-full w-full p-2">
        <Pane
          process={process}
          isFullscreen
          isFocused
          onFocus={() => onFocusSlot?.(fullscreenSlot)}
          onClose={() => onCloseSlot?.(fullscreenSlot)}
          onFullscreen={() => onFullscreenSlot?.(fullscreenSlot)}
          onRename={onRenameProcess}
          onProcessUpdate={onProcessUpdate}
          syncEnabled={syncEnabled}
        />
      </div>
    )
  }

  const slotCount = SLOT_COUNT_BY_MODE[mode] || 1

  let className = 'h-full w-full p-2 grid gap-2 min-h-0'
  if (mode === '2x2') className += ' grid-cols-2 grid-rows-2'
  else if (mode === '3x2') className += ' grid-cols-3 grid-rows-2'
  else if (mode === 'focus+strip') {
    className += ' grid-cols-[3fr_1fr] grid-rows-3'
  } else {
    className += ' grid-cols-1 grid-rows-1'
  }

  const cells = []
  for (let i = 0; i < slotCount; i++) {
    const procId = slots[i] || null
    const process = procId ? processesById[procId] || null : null

    let style
    if (mode === 'focus+strip') {
      // Slot 0 = the big focus pane (spans all rows of the first column).
      // Slots 1, 2, 3 = the side strip.
      if (i === 0) style = { gridColumn: 1, gridRow: '1 / span 3' }
      else style = { gridColumn: 2, gridRow: i }
    }

    const syncEnabled = syncedSet && process?.group_id ? syncedSet.has(process.group_id) : false
    cells.push(
      <div key={i} style={style} className="min-h-0 min-w-0 h-full w-full flex">
        <Pane
          process={process}
          isFocused={focusedSlot === i}
          onFocus={() => onFocusSlot?.(i)}
          onClose={procId ? () => onCloseSlot?.(i) : undefined}
          onFullscreen={() => onFullscreenSlot?.(i)}
          onRename={onRenameProcess}
          onProcessUpdate={onProcessUpdate}
          syncEnabled={syncEnabled}
        />
      </div>
    )
  }

  return <div className={className}>{cells}</div>
}

export default PaneGrid
