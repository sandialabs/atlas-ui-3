/**
 * Tests for paste behavior when clipboard has mixed text + image content.
 * Verifies that Office-doc copy/paste defaults to text, while screenshot
 * paste still uploads the image.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ChatArea from '../components/ChatArea'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/WSContext')

describe('ChatArea - Paste text vs image handling', () => {
  const defaultChatContext = {
    messages: [],
    isWelcomeVisible: true,
    isThinking: false,
    sendChatMessage: vi.fn(),
    currentModel: 'gpt-4',
    tools: [],
    prompts: [],
    selectedTools: new Set(),
    selectedPrompts: new Set(),
    toggleTool: vi.fn(),
    togglePrompt: vi.fn(),
    setToolChoiceRequired: vi.fn(),
    sessionFiles: { files: [], total_files: 0, categories: {} },
    agentModeEnabled: false,
    agentPendingQuestion: null,
    setAgentPendingQuestion: vi.fn(),
    stopAgent: vi.fn(),
    answerAgentQuestion: vi.fn(),
    followUpSuggestions: [],
    setFollowUpSuggestions: vi.fn()
  }

  const defaultWSContext = {
    isConnected: true,
    sendMessage: vi.fn()
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useChat.mockReturnValue(defaultChatContext)
    useWS.mockReturnValue(defaultWSContext)
  })

  const makeStringItem = (type) => ({
    kind: 'string',
    type,
    getAsFile: () => null
  })

  const makeFileItem = (type) => {
    const blob = new Blob(['fake'], { type })
    const file = new File([blob], 'image.png', { type })
    return {
      kind: 'file',
      type,
      getAsFile: () => file
    }
  }

  /**
   * Dispatch a paste event with custom clipboardData.items on the textarea.
   * We need to use a real DOM event and override clipboardData because
   * jsdom's ClipboardEvent has a read-only clipboardData.
   */
  const firePaste = (textarea, items) => {
    const event = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'clipboardData', {
      value: { items }
    })
    textarea.dispatchEvent(event)
    return event
  }

  it('should default to text paste when clipboard has both text and image (Office docs)', () => {
    const mockFileReader = { readAsDataURL: vi.fn(), onload: null, onerror: null }
    vi.spyOn(global, 'FileReader').mockImplementation(() => mockFileReader)

    render(<BrowserRouter><ChatArea /></BrowserRouter>)

    const textarea = screen.getByPlaceholderText(/Type a message/i)
    const event = firePaste(textarea, [
      makeStringItem('text/plain'),
      makeStringItem('text/html'),
      makeFileItem('image/png')
    ])

    // Should NOT preventDefault — browser handles text paste natively
    expect(event.defaultPrevented).toBe(false)
    // Should NOT attempt to read the image file
    expect(mockFileReader.readAsDataURL).not.toHaveBeenCalled()

    global.FileReader.mockRestore()
  })

  it('should upload image when clipboard has only an image (screenshots)', () => {
    const mockFileReader = { readAsDataURL: vi.fn(), onload: null, onerror: null }
    vi.spyOn(global, 'FileReader').mockImplementation(() => mockFileReader)

    render(<BrowserRouter><ChatArea /></BrowserRouter>)

    const textarea = screen.getByPlaceholderText(/Type a message/i)
    const event = firePaste(textarea, [
      makeFileItem('image/png')
    ])

    // Should preventDefault and process the image
    expect(event.defaultPrevented).toBe(true)
    expect(mockFileReader.readAsDataURL).toHaveBeenCalledTimes(1)

    global.FileReader.mockRestore()
  })

  it('should not block non-image file paste that includes text/uri-list', () => {
    const mockFileReader = { readAsDataURL: vi.fn(), onload: null, onerror: null }
    vi.spyOn(global, 'FileReader').mockImplementation(() => mockFileReader)

    render(<BrowserRouter><ChatArea /></BrowserRouter>)

    const textarea = screen.getByPlaceholderText(/Type a message/i)
    // Simulates file-manager paste: text/uri-list + a non-image file
    const event = firePaste(textarea, [
      makeStringItem('text/uri-list'),
      makeFileItem('application/pdf')
    ])

    // pdf is not an image, so the allFilesAreImages guard doesn't apply
    expect(event.defaultPrevented).toBe(true)
    expect(mockFileReader.readAsDataURL).toHaveBeenCalledTimes(1)

    global.FileReader.mockRestore()
  })

  it('should not block image paste when text/uri-list is the only text item', () => {
    const mockFileReader = { readAsDataURL: vi.fn(), onload: null, onerror: null }
    vi.spyOn(global, 'FileReader').mockImplementation(() => mockFileReader)

    render(<BrowserRouter><ChatArea /></BrowserRouter>)

    const textarea = screen.getByPlaceholderText(/Type a message/i)
    // text/uri-list is excluded from the hasText check, so images should upload
    const event = firePaste(textarea, [
      makeStringItem('text/uri-list'),
      makeFileItem('image/png')
    ])

    expect(event.defaultPrevented).toBe(true)
    expect(mockFileReader.readAsDataURL).toHaveBeenCalledTimes(1)

    global.FileReader.mockRestore()
  })
})
