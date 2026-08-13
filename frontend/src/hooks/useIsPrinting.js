/**
 * Tracks whether the document is currently being rendered for print.
 *
 * Exists so print-only content can be mounted lazily. Tool call details
 * (input arguments + output) must appear in the PDF even for rows the user
 * never expanded (#774), but MCP results are not size-bounded -- keeping every
 * collapsed row's arguments and result serialized into hidden <pre> elements
 * would grow the DOM and cost a JSON.stringify per row on every render of an
 * ordinary on-screen chat. Rendering them only while printing keeps the screen
 * path as lazy as it was before.
 *
 * Two signals are watched because they cover different entry points:
 *  - `beforeprint` / `afterprint` fire for window.print() and Ctrl+P. The state
 *    change is wrapped in flushSync so React has committed the extra DOM before
 *    the browser snapshots the page -- a normal (batched) update would land
 *    after the snapshot and the details would be missing from the PDF.
 *  - `matchMedia('print')` covers headless rendering, where the print media is
 *    emulated and no beforeprint event is dispatched.
 *
 * The window listeners are registered once at module level rather than per
 * component: a long transcript mounts hundreds of Message components, and one
 * flushSync per component would mean hundreds of synchronous renders.
 */

import { useSyncExternalStore } from 'react'
import { flushSync } from 'react-dom'

const canUseDom = typeof window !== 'undefined'
const printQuery = canUseDom && window.matchMedia ? window.matchMedia('print') : null

const listeners = new Set()
let printingViaEvent = false

const getSnapshot = () => printingViaEvent || !!(printQuery && printQuery.matches)

// Server-side/prerender snapshot: never printing.
const getServerSnapshot = () => false

const notify = () => {
  for (const listener of listeners) listener()
}

const setPrinting = (value) => {
  printingViaEvent = value
  // flushSync so the mount is committed before the browser takes the page
  // snapshot; these handlers run outside React's event system, where updates
  // would otherwise be deferred to a microtask.
  flushSync(notify)
}

if (canUseDom) {
  window.addEventListener('beforeprint', () => setPrinting(true))
  window.addEventListener('afterprint', () => setPrinting(false))
  if (printQuery) {
    const onQueryChange = () => flushSync(notify)
    if (printQuery.addEventListener) {
      printQuery.addEventListener('change', onQueryChange)
    } else if (printQuery.addListener) {
      // Safari < 14
      printQuery.addListener(onQueryChange)
    }
  }
}

const subscribe = (listener) => {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useIsPrinting() {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}

export default useIsPrinting
