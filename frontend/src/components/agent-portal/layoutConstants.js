// Layout-mode constants used by both the PaneGrid component and the
// pure layoutHelpers module. Living in a constants-only file keeps
// react-refresh happy (it complains when a component file also exports
// non-component values).

export const LAYOUT_MODES = ['single', '2x2', '3x2', 'focus+strip']

export const SLOT_COUNT_BY_MODE = {
  single: 1,
  '2x2': 4,
  '3x2': 6,
  // focus+strip = one big cell plus a side strip of 3. 4 slots total.
  'focus+strip': 4,
}

// Soft cap (banner) and hard cap (refuse, require swap) on live xterms.
// xterm.js keeps a per-instance scrollback buffer; ~9 cells with 5000
// lines each is the upper limit before scroll perf degrades on lower-end
// boxes.
export const SOFT_CAP_LIVE = 6
export const HARD_CAP_LIVE = 9
