/**
 * Contract of the shared overlay focus trap (issue #1039).
 *
 * One useFocusTrap implementation now serves every outermost overlay -- the
 * Tools and Settings panel (with its nested dialogs), the Data Sources
 * drawer, and the elicitation prompt -- so each behavior every consumer
 * relies on is pinned here:
 *
 * - focus enters on activation (default first focusable, or the
 *   initialFocusSelector match) and returns on deactivation,
 * - restore is skipped while a different modal remains open,
 * - Tab wraps inside the overlay and pulls stray focus back in,
 * - ownership stands down for a later sibling modal,
 * - a nested dialog keeps the PARENT owning the keyboard: Tab is trapped
 *   inside the innermost dialog and Escape stands down for the nested
 *   overlay's own handler,
 * - Escape delegates to the latest onEscape, stops propagation once, and
 *   ignores keys aimed outside the overlay.
 *
 * jsdom has no layout and returns no client rects, so every test stubs
 * getClientRects to pretend elements render -- the same approach the drawer
 * suite uses.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { useRef } from 'react'
import { useFocusTrap, isTopmostModalDialog, getInnermostDialog } from '../utils/focusTrap'

function Overlay({ active = true, onEscape, initialFocusSelector, withNested = false }) {
  const ref = useRef(null)
  useFocusTrap({ containerRef: ref, active, initialFocusSelector, onEscape })
  return (
    <div ref={ref} role="dialog" aria-modal="true" aria-label="Trap under test">
      <button type="button">outer first</button>
      {withNested && (
        <div role="dialog" aria-modal="true" aria-label="Nested dialog">
          <button type="button">nested first</button>
          <button type="button">nested last</button>
        </div>
      )}
      <button type="button">outer last</button>
    </div>
  )
}

function SelectorOverlay({ initialFocusSelector }) {
  const ref = useRef(null)
  useFocusTrap({ containerRef: ref, active: true, initialFocusSelector })
  return (
    <div ref={ref} role="dialog" aria-modal="true" aria-label="Selector overlay">
      <button type="button">first</button>
      <input id="entry-target" aria-label="entry target" />
    </div>
  )
}

let rectsSpy
let strayNodes

beforeEach(() => {
  rectsSpy = vi.spyOn(Element.prototype, 'getClientRects').mockReturnValue([{}])
  strayNodes = []
})

afterEach(() => {
  rectsSpy.mockRestore()
  strayNodes.forEach(node => node.remove())
  strayNodes = []
})

// Whatever the user was interacting with before the overlay opened.
const addOpener = () => {
  const opener = document.createElement('button')
  opener.textContent = 'opener'
  document.body.appendChild(opener)
  strayNodes.push(opener)
  return opener
}

// A separate aria-modal dialog AFTER the overlay in document order -- the
// shape of the Tools and Settings modal layered on the drawer, or the
// elicitation prompt layered on the panel.
const addLaterDialog = () => {
  const dialog = document.createElement('div')
  dialog.setAttribute('role', 'dialog')
  dialog.setAttribute('aria-modal', 'true')
  const inside = document.createElement('button')
  dialog.appendChild(inside)
  document.body.appendChild(dialog)
  strayNodes.push(dialog)
  return inside
}

describe('useFocusTrap - focus entry and restore', () => {
  it('moves focus to the first focusable on activation', () => {
    const opener = addOpener()
    opener.focus()
    render(<Overlay />)

    expect(document.activeElement).toHaveTextContent('outer first')
  })

  it('honours initialFocusSelector over the first focusable', () => {
    render(<SelectorOverlay initialFocusSelector="#entry-target" />)

    expect(document.activeElement).toBe(document.getElementById('entry-target'))
  })

  it('returns focus to the previous element on deactivation', () => {
    const opener = addOpener()
    opener.focus()
    const { rerender } = render(<Overlay />)

    rerender(<Overlay active={false} />)

    expect(document.activeElement).toBe(opener)
  })

  it('skips restore while a different modal remains open', () => {
    // Restoring would yank focus behind the newer modal's backdrop.
    const opener = addOpener()
    opener.focus()
    const { rerender } = render(<Overlay />)
    const insideLater = addLaterDialog()
    insideLater.focus()

    rerender(<Overlay active={false} />)

    expect(document.activeElement).toBe(insideLater)
  })
})

describe('useFocusTrap - the Tab trap', () => {
  it('wraps forward from the last control to the first', () => {
    const { container } = render(<Overlay />)
    const buttons = container.querySelectorAll('button')
    buttons[buttons.length - 1].focus()

    fireEvent.keyDown(container.firstChild, { key: 'Tab' })

    expect(document.activeElement).toBe(buttons[0])
  })

  it('wraps backward from the first control to the last', () => {
    const { container } = render(<Overlay />)
    const buttons = container.querySelectorAll('button')
    buttons[0].focus()

    fireEvent.keyDown(container.firstChild, { key: 'Tab', shiftKey: true })

    expect(document.activeElement).toBe(buttons[buttons.length - 1])
  })

  it('pulls stray focus back in: the trap listens on the document', () => {
    // Focus can legitimately sit outside while the overlay is up (a control
    // disabled itself and dropped focus to <body>); from there a Tab must
    // return into the overlay, not walk the covered page behind it.
    const { container } = render(<Overlay />)
    const buttons = container.querySelectorAll('button')
    buttons[0].blur()
    expect(container.contains(document.activeElement)).toBe(false)

    fireEvent.keyDown(document.body, { key: 'Tab' })
    expect(document.activeElement).toBe(buttons[0])

    buttons[0].blur()
    fireEvent.keyDown(document.body, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(buttons[buttons.length - 1])
  })

  it('stands down for a later sibling modal: it owns Tab and Escape', () => {
    const onEscape = vi.fn()
    render(<Overlay onEscape={onEscape} />)
    const insideLater = addLaterDialog()
    insideLater.focus()

    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(insideLater)

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onEscape).not.toHaveBeenCalled()
  })
})

describe('useFocusTrap - nested dialogs stay part of the parent overlay', () => {
  it('keeps the parent owning the keyboard while a nested dialog is topmost', () => {
    // The containment rule: the nested dialog is the last aria-modal dialog
    // in the document, but it belongs to the parent's overlay group rather
    // than competing with it. Without this the parent would stand down and
    // Tab would escape the nested dialog into the overlay behind it.
    const parent = document.createElement('div')
    parent.setAttribute('role', 'dialog')
    parent.setAttribute('aria-modal', 'true')
    const nested = document.createElement('div')
    nested.setAttribute('role', 'dialog')
    nested.setAttribute('aria-modal', 'true')
    parent.appendChild(nested)
    document.body.appendChild(parent)
    strayNodes.push(parent)

    expect(isTopmostModalDialog(parent)).toBe(true)
    expect(isTopmostModalDialog(nested)).toBe(true)
  })

  it('scopes the Tab trap to the innermost open dialog', () => {
    const { container } = render(<Overlay withNested />)
    const nested = container.querySelector('[aria-label="Nested dialog"]')
    const nestedButtons = nested.querySelectorAll('button')
    nestedButtons[nestedButtons.length - 1].focus()

    fireEvent.keyDown(nested, { key: 'Tab' })

    // Wrapped inside the NESTED dialog, not out to the outer controls.
    expect(document.activeElement).toBe(nestedButtons[0])
  })

  it('pulls focus from the outer overlay into the nested dialog', () => {
    const { container } = render(<Overlay withNested />)
    const nested = container.querySelector('[aria-label="Nested dialog"]')
    const outerFirst = container.querySelectorAll('button')[0]
    outerFirst.focus()

    fireEvent.keyDown(container.firstChild, { key: 'Tab' })

    expect(nested.contains(document.activeElement)).toBe(true)
  })

  it('stands down on Escape while a nested dialog is open', () => {
    // The nested overlay closes itself (useEscapeKey listens in the capture
    // phase); the parent's own Escape must not also fire -- closing the
    // whole panel out from under a half-entered token would destroy input.
    const onEscape = vi.fn()
    const { container } = render(<Overlay withNested onEscape={onEscape} />)

    fireEvent.keyDown(container.firstChild, { key: 'Escape' })

    expect(onEscape).not.toHaveBeenCalled()
  })

  it('scopes getInnermostDialog to the last rendered nested dialog', () => {
    const parent = document.createElement('div')
    parent.setAttribute('role', 'dialog')
    const firstNested = document.createElement('div')
    firstNested.setAttribute('role', 'dialog')
    const lastNested = document.createElement('div')
    lastNested.setAttribute('role', 'dialog')
    parent.append(firstNested, lastNested)
    document.body.appendChild(parent)
    strayNodes.push(parent)

    expect(getInnermostDialog(parent)).toBe(lastNested)
    lastNested.remove()
    // Still scoped: the earlier nested dialog is rendered too.
    expect(getInnermostDialog(parent)).toBe(firstNested)
    firstNested.remove()
    // No nested dialog left: the overlay itself is the scope.
    expect(getInnermostDialog(parent)).toBe(parent)
  })

  it('ignores a closed-but-mounted nested dialog when scoping', () => {
    // A nested dialog that stayed mounted while closed must never hijack
    // the trap's scope: the trap would rotate through elements that are not
    // on screen and swallow every Tab.
    rectsSpy.mockImplementation(function () {
      return this.dataset.rendered === 'false' ? [] : [{}]
    })
    const parent = document.createElement('div')
    parent.setAttribute('role', 'dialog')
    const closedNested = document.createElement('div')
    closedNested.setAttribute('role', 'dialog')
    closedNested.dataset.rendered = 'false'
    parent.appendChild(closedNested)
    document.body.appendChild(parent)
    strayNodes.push(parent)

    expect(getInnermostDialog(parent)).toBe(parent)
  })
})

describe('useFocusTrap - Escape delegation', () => {
  it('calls the latest onEscape after a rerender changes its identity', () => {
    const first = vi.fn()
    const second = vi.fn()
    const { rerender } = render(<Overlay onEscape={first} />)
    rerender(<Overlay onEscape={second} />)

    fireEvent.keyDown(document.body, { key: 'Escape' })

    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledTimes(1)
  })

  it('stops propagation once it handles Escape', () => {
    // Window-level listeners (the prompt/confirm dialog host) must not also
    // react to an Escape the overlay already handled.
    render(<Overlay onEscape={vi.fn()} />)
    const windowSawEscape = vi.fn()
    window.addEventListener('keydown', windowSawEscape)
    try {
      fireEvent.keyDown(document.body, { key: 'Escape' })
      expect(windowSawEscape).not.toHaveBeenCalled()
    } finally {
      window.removeEventListener('keydown', windowSawEscape)
    }
  })

  it('does not close on Escape aimed at an unrelated outside element', () => {
    // The listener lives on the document, so scope by target: a keydown
    // aimed at an element that is not the overlay and not a "focus fell out
    // entirely" target belongs to something else.
    const onEscape = vi.fn()
    render(<Overlay onEscape={onEscape} />)
    const unrelated = addOpener()
    unrelated.focus()

    fireEvent.keyDown(unrelated, { key: 'Escape' })
    expect(onEscape).not.toHaveBeenCalled()

    // ...while <body> and `document` targets are the fallen-out case and
    // still close the overlay.
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(onEscape).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onEscape).toHaveBeenCalledTimes(2)
  })
})