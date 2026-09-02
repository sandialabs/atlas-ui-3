/**
 * Tests for the canvas width/orientation controls (issue #754).
 * Covers: cycling half <-> full, switching between side-by-side and stacked,
 * and persistence of both choices in localStorage.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { renderHook } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import CanvasPanel from '../components/CanvasPanel'
import { useCanvasLayout } from '../hooks/useCanvasLayout'

vi.mock('../contexts/ChatContext', () => ({
  useChat: () => ({
    canvasContent: 'hello canvas',
    customUIContent: null,
    canvasFiles: [],
    currentCanvasFileIndex: 0,
    setCurrentCanvasFileIndex: vi.fn(),
    downloadFile: vi.fn(),
  }),
}))

const createLocalStorageMock = () => {
  let store = {}
  return {
    getItem: vi.fn(key => (key in store ? store[key] : null)),
    setItem: vi.fn((key, value) => {
      store[key] = String(value)
    }),
    removeItem: vi.fn(key => {
      delete store[key]
    }),
    clear: vi.fn(() => {
      store = {}
    }),
  }
}

describe('canvas layout controls', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.defineProperty(window, 'localStorage', {
      value: createLocalStorageMock(),
      writable: true,
    })
  })

  it('defaults to a half-width canvas beside the chat', () => {
    render(<CanvasPanel isOpen={true} onClose={vi.fn()} />)
    const panel = screen.getByTestId('canvas-panel')
    expect(panel.dataset.canvasSize).toBe('half')
    expect(panel.dataset.canvasOrientation).toBe('right')
  })

  it('renders full size without a drag handle and stacked without a left border', () => {
    const { rerender } = render(
      <CanvasPanel isOpen={true} onClose={vi.fn()} size="full" orientation="right" />
    )
    let panel = screen.getByTestId('canvas-panel')
    expect(panel.style.width).toBe('100%')
    expect(panel.querySelector('[style*="ew-resize"]')).toBeNull()

    rerender(<CanvasPanel isOpen={true} onClose={vi.fn()} size="half" orientation="top" />)
    panel = screen.getByTestId('canvas-panel')
    expect(panel.style.height).toBe('50%')
    expect(panel.className).toContain('border-b')
    expect(panel.className).not.toContain('border-l')
  })

  it('invokes the size and orientation callbacks from the header buttons', async () => {
    const user = userEvent.setup()
    const onToggleSize = vi.fn()
    const onToggleOrientation = vi.fn()
    render(
      <CanvasPanel
        isOpen={true}
        onClose={vi.fn()}
        onToggleSize={onToggleSize}
        onToggleOrientation={onToggleOrientation}
      />
    )

    await user.click(screen.getByTestId('canvas-size-toggle'))
    await user.click(screen.getByTestId('canvas-orientation-toggle'))

    expect(onToggleSize).toHaveBeenCalledTimes(1)
    expect(onToggleOrientation).toHaveBeenCalledTimes(1)
  })

  it('persists the layout choice for the device', () => {
    const { result } = renderHook(() => useCanvasLayout())
    expect(result.current.size).toBe('half')
    expect(result.current.orientation).toBe('right')

    act(() => {
      result.current.toggleSize()
      result.current.toggleOrientation()
    })

    expect(result.current.size).toBe('full')
    expect(result.current.orientation).toBe('top')
    expect(window.localStorage.setItem).toHaveBeenCalledWith('chatui-canvas-size', '"full"')
    expect(window.localStorage.setItem).toHaveBeenCalledWith('chatui-canvas-orientation', '"top"')

    // A fresh mount reads the stored preference back
    const remounted = renderHook(() => useCanvasLayout())
    expect(remounted.result.current.size).toBe('full')
    expect(remounted.result.current.orientation).toBe('top')
  })

  it('stacks the canvas on a narrow viewport without losing the stored preference', () => {
    const original = window.innerWidth
    window.innerWidth = 500
    try {
      const { result } = renderHook(() => useCanvasLayout())
      expect(result.current.isNarrow).toBe(true)
      expect(result.current.orientation).toBe('right')
      expect(result.current.effectiveOrientation).toBe('top')

      act(() => {
        window.innerWidth = 1200
        window.dispatchEvent(new Event('resize'))
      })
      expect(result.current.isNarrow).toBe(false)
      expect(result.current.effectiveOrientation).toBe('right')
    } finally {
      window.innerWidth = original
    }
  })

  it('hides the orientation toggle when the viewport locks the layout', () => {
    render(<CanvasPanel isOpen={true} onClose={vi.fn()} orientation="top" orientationLocked={true} />)
    expect(screen.queryByTestId('canvas-orientation-toggle')).toBeNull()
    expect(screen.getByTestId('canvas-size-toggle')).toBeTruthy()
  })

  it('falls back to the default layout when stored values are unusable', () => {
    window.localStorage.getItem.mockImplementation(key =>
      key === 'chatui-canvas-size' ? '"gigantic"' : '"sideways"'
    )
    const { result } = renderHook(() => useCanvasLayout())
    expect(result.current.size).toBe('half')
    expect(result.current.orientation).toBe('right')
  })
})
