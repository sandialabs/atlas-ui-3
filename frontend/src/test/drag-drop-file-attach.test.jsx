/**
 * Tests for drag and drop file attachment in ChatArea component
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import ChatArea from '../components/ChatArea'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/WSContext')

describe('ChatArea - Drag and Drop File Attachment', () => {
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
    answerAgentQuestion: vi.fn()
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

  const createDragEvent = (type, files = []) => {
    const dataTransfer = {
      items: files.map(f => ({ kind: 'file', type: f.type })),
      files: files,
      setData: vi.fn(),
      getData: vi.fn()
    }
    return {
      preventDefault: vi.fn(),
      stopPropagation: vi.fn(),
      dataTransfer
    }
  }

  const createMockFile = (name, content, type = 'text/plain') => {
    const blob = new Blob([content], { type })
    return new File([blob], name, { type })
  }

  it('should show drag overlay when files are dragged over the chat area', () => {
    render(
      <BrowserRouter>
        <ChatArea />
      </BrowserRouter>
    )

    const chatArea = screen.getByRole('main').parentElement

    const dragEnterEvent = createDragEvent('dragenter', [
      createMockFile('test.txt', 'test content')
    ])
    fireEvent.dragEnter(chatArea, dragEnterEvent)

    expect(screen.getByTestId('drag-overlay')).toBeInTheDocument()
    expect(screen.getByText('Drop files to attach')).toBeInTheDocument()
  })

  it('should hide drag overlay when drag leaves the chat area', () => {
    render(
      <BrowserRouter>
        <ChatArea />
      </BrowserRouter>
    )

    const chatArea = screen.getByRole('main').parentElement

    const dragEnterEvent = createDragEvent('dragenter', [
      createMockFile('test.txt', 'test content')
    ])
    fireEvent.dragEnter(chatArea, dragEnterEvent)

    expect(screen.getByTestId('drag-overlay')).toBeInTheDocument()

    const dragLeaveEvent = createDragEvent('dragleave')
    fireEvent.dragLeave(chatArea, dragLeaveEvent)

    expect(screen.queryByTestId('drag-overlay')).not.toBeInTheDocument()
  })

  it('should hide drag overlay when files are dropped', () => {
    render(
      <BrowserRouter>
        <ChatArea />
      </BrowserRouter>
    )

    const chatArea = screen.getByRole('main').parentElement

    const mockFile = createMockFile('test.txt', 'test content')
    const dragEnterEvent = createDragEvent('dragenter', [mockFile])
    fireEvent.dragEnter(chatArea, dragEnterEvent)

    expect(screen.getByTestId('drag-overlay')).toBeInTheDocument()

    const dropEvent = createDragEvent('drop', [mockFile])
    fireEvent.drop(chatArea, dropEvent)

    expect(screen.queryByTestId('drag-overlay')).not.toBeInTheDocument()
  })

  it('should handle dragOver event without errors', () => {
    render(
      <BrowserRouter>
        <ChatArea />
      </BrowserRouter>
    )

    const chatArea = screen.getByRole('main').parentElement

    const dragOverEvent = createDragEvent('dragover', [
      createMockFile('test.txt', 'test content')
    ])
    
    expect(() => {
      fireEvent.dragOver(chatArea, dragOverEvent)
    }).not.toThrow()
  })

  it('should display uploaded files indicator after drop', async () => {
    const mockFileReader = {
      readAsDataURL: vi.fn(),
      result: 'data:text/plain;base64,dGVzdCBjb250ZW50',
      onload: null
    }
    
    vi.spyOn(global, 'FileReader').mockImplementation(() => mockFileReader)

    render(
      <BrowserRouter>
        <ChatArea />
      </BrowserRouter>
    )

    const chatArea = screen.getByRole('main').parentElement
    const mockFile = createMockFile('test-file.txt', 'test content')

    const dropEvent = createDragEvent('drop', [mockFile])
    fireEvent.drop(chatArea, dropEvent)

    mockFileReader.onload({ target: { result: 'data:text/plain;base64,dGVzdCBjb250ZW50' } })

    await vi.waitFor(() => {
      expect(screen.getByText('test-file.txt')).toBeInTheDocument()
    })

    global.FileReader.mockRestore()
  })

  it('should handle multiple files dropped at once', async () => {
    const mockFileReader = {
      readAsDataURL: vi.fn(),
      result: 'data:text/plain;base64,dGVzdA==',
      onload: null
    }
    
    vi.spyOn(global, 'FileReader').mockImplementation(() => mockFileReader)

    render(
      <BrowserRouter>
        <ChatArea />
      </BrowserRouter>
    )

    const chatArea = screen.getByRole('main').parentElement
    const mockFiles = [
      createMockFile('file1.txt', 'content 1'),
      createMockFile('file2.txt', 'content 2')
    ]

    const dropEvent = createDragEvent('drop', mockFiles)
    fireEvent.drop(chatArea, dropEvent)

    expect(mockFileReader.readAsDataURL).toHaveBeenCalledTimes(2)

    global.FileReader.mockRestore()
  })

  it('should not show overlay when no files are being dragged', () => {
    render(
      <BrowserRouter>
        <ChatArea />
      </BrowserRouter>
    )

    const chatArea = screen.getByRole('main').parentElement

    const dragEnterEvent = createDragEvent('dragenter', [])
    fireEvent.dragEnter(chatArea, dragEnterEvent)

    expect(screen.queryByTestId('drag-overlay')).not.toBeInTheDocument()
  })

  it('should show helpful text in drag overlay', () => {
    render(
      <BrowserRouter>
        <ChatArea />
      </BrowserRouter>
    )

    const chatArea = screen.getByRole('main').parentElement

    const dragEnterEvent = createDragEvent('dragenter', [
      createMockFile('test.txt', 'test content')
    ])
    fireEvent.dragEnter(chatArea, dragEnterEvent)

    expect(screen.getByText('Drop files to attach')).toBeInTheDocument()
    expect(screen.getByText('Files will be added to your message')).toBeInTheDocument()
  })
})
