// Ctrl-Shift-P command palette for the Agent Portal.
//
// Action shape:  { id, title, hint?, scope, run, when? }
//
//   scope ∈ "Process" | "Layout" | "Group" | "Global"
//   when() optionally hides the action when context-irrelevant.
//
// The palette is mounted high in the tree so it can fire actions
// regardless of where focus currently sits. Activation is gated on
// `document.activeElement` so a focused xterm gets first dibs (the
// terminal, not the palette, gets keystrokes when it's actively
// being typed into) — see the bound-listener in AgentPortal.jsx.

import { Command } from 'cmdk'
import { useEffect, useMemo } from 'react'

const SCOPE_ORDER = ['Process', 'Layout', 'Group', 'Global']

function CommandPalette({ open, onOpenChange, actions }) {
  // cmdk styles itself; we layer Tailwind on top via the className prop
  // chain. Keep the surface small and keyboard-only — no fancy hover
  // chrome.
  useEffect(() => {
    if (!open) return
    const onKey = (e) => {
      if (e.key === 'Escape') {
        onOpenChange(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onOpenChange])

  const grouped = useMemo(() => {
    const buckets = new Map()
    for (const a of actions) {
      if (typeof a.when === 'function' && !a.when()) continue
      const scope = a.scope || 'Global'
      if (!buckets.has(scope)) buckets.set(scope, [])
      buckets.get(scope).push(a)
    }
    // Stable scope ordering, then alphabetical within scope.
    return SCOPE_ORDER
      .filter((s) => buckets.has(s))
      .map((s) => ({ scope: s, items: buckets.get(s).slice().sort((a, b) => a.title.localeCompare(b.title)) }))
      .concat(
        [...buckets.keys()]
          .filter((s) => !SCOPE_ORDER.includes(s))
          .sort()
          .map((s) => ({ scope: s, items: buckets.get(s).slice().sort((a, b) => a.title.localeCompare(b.title)) }))
      )
  }, [actions])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[10000] flex items-start justify-center pt-24 px-4 bg-black/50 backdrop-blur-sm"
      onClick={() => onOpenChange(false)}
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <div
        className="w-full max-w-xl bg-gray-900 border border-gray-700 rounded-xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <Command label="Agent Portal commands" loop>
          <Command.Input
            autoFocus
            placeholder="Search commands…"
            className="w-full px-4 py-3 bg-gray-900 border-b border-gray-700 text-gray-100 placeholder-gray-500 focus:outline-none"
          />
          <Command.List className="max-h-96 overflow-y-auto py-2">
            <Command.Empty className="px-4 py-3 text-sm text-gray-500">
              No matching command.
            </Command.Empty>
            {grouped.map(({ scope, items }) => (
              <Command.Group key={scope} heading={scope} className="px-2">
                {items.map((a) => (
                  <Command.Item
                    key={a.id}
                    value={`${a.scope || ''} ${a.title} ${a.hint || ''}`}
                    onSelect={() => {
                      onOpenChange(false)
                      // Defer the run so the palette unmounts before the
                      // action fires (some actions open another modal).
                      setTimeout(() => a.run?.(), 0)
                    }}
                    className="flex items-center justify-between px-3 py-2 rounded text-sm text-gray-200 cursor-pointer aria-selected:bg-blue-600/30 aria-selected:text-white"
                  >
                    <span className="truncate">{a.title}</span>
                    {a.hint && <span className="text-xs text-gray-500 ml-3">{a.hint}</span>}
                  </Command.Item>
                ))}
              </Command.Group>
            ))}
          </Command.List>
        </Command>
        <div className="px-4 py-2 text-[11px] text-gray-500 border-t border-gray-700 flex justify-between">
          <span>Esc to close · Enter to run</span>
          <span>Ctrl-Shift-P to reopen</span>
        </div>
      </div>
    </div>
  )
}

export default CommandPalette
