/**
 * useChatTopVar publishes the chat area's top edge, measured against #root,
 * as --atlas-chat-top. Phone toolbar panels cap their height to the room
 * between the toolbar and that edge (index.css), so the value must:
 * - stay put when the page or visual viewport pans (both rects shift),
 * - grow when the chrome above the chat area grows (a banner message),
 * - not drop to 0 when the chat area is hidden (no layout box),
 * - clear on unmount so CSS falls back to its default.
 */

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'
import { useRef } from 'react'
import { useChatTopVar, CHAT_TOP_VAR } from '../hooks/useChatTopVar'

const box = { visible: true, chatTop: 77, rootTop: 0 }

function Probe() {
  const ref = useRef(null)
  useChatTopVar(ref)
  return (
    <div
      ref={(el) => {
        if (el) {
          el.getClientRects = () => (box.visible ? [{}] : [])
          el.getBoundingClientRect = () => ({ top: box.chatTop })
        }
        ref.current = el
      }}
    />
  )
}

const value = () => document.documentElement.style.getPropertyValue(CHAT_TOP_VAR)
const republish = () => act(() => { window.dispatchEvent(new Event('resize')) })

let appRoot = null
const mount = () => {
  appRoot = document.createElement('div')
  appRoot.id = 'root'
  appRoot.getBoundingClientRect = () => ({ top: box.rootTop })
  document.body.appendChild(appRoot)
  return render(<Probe />, { container: appRoot })
}

afterEach(() => {
  cleanup()
  appRoot?.remove()
  appRoot = null
  Object.assign(box, { visible: true, chatTop: 77, rootTop: 0 })
  document.documentElement.style.removeProperty(CHAT_TOP_VAR)
})

describe('useChatTopVar', () => {
  it('publishes the chat top relative to #root', () => {
    mount()
    expect(value()).toBe('77px')
  })

  it('does not move when the page or visual viewport pans', () => {
    mount()
    // A pan (keyboard up on iOS) shifts both rects together.
    box.chatTop -= 120
    box.rootTop -= 120
    republish()
    expect(value()).toBe('77px')
  })

  it('follows taller chrome above the chat area', () => {
    mount()
    // A wrapped admin banner message pushes the chat area down.
    box.chatTop += 48
    republish()
    expect(value()).toBe('125px')
  })

  it('republishes on visual viewport resize and on ResizeObserver callbacks', () => {
    const vv = new EventTarget()
    const originalVV = window.visualViewport
    const originalRO = globalThis.ResizeObserver
    let observed = null
    globalThis.ResizeObserver = class {
      constructor(cb) { observed = cb }
      observe() {}
      disconnect() {}
    }
    Object.defineProperty(window, 'visualViewport', { value: vv, configurable: true })
    try {
      mount()
      box.chatTop += 10
      act(() => { vv.dispatchEvent(new Event('resize')) })
      expect(value()).toBe('87px')
      box.chatTop += 10
      act(() => { observed() })
      expect(value()).toBe('97px')
    } finally {
      cleanup()
      Object.defineProperty(window, 'visualViewport', { value: originalVV, configurable: true })
      globalThis.ResizeObserver = originalRO
    }
  })

  it('keeps the last value while the chat area has no layout box', () => {
    mount()
    box.visible = false
    box.chatTop = 0
    republish()
    expect(value()).toBe('77px')
  })

  it('clears the variable on unmount', () => {
    const { unmount } = mount()
    expect(value()).toBe('77px')
    unmount()
    expect(value()).toBe('')
  })
})
