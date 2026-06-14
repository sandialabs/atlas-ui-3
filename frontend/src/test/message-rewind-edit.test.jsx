/**
 * Tests for the edit-and-resubmit affordance on user messages (issue #142).
 * A subtle pencil button appears on user messages; clicking it opens an inline
 * editor prefilled with the message, and Send calls onRewind(userIndex, text).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Message from '../components/Message'
import { useChat } from '../contexts/ChatContext'

vi.mock('../contexts/ChatContext')

describe('Message - edit and resubmit', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useChat.mockReturnValue({
      appName: 'Atlas',
      downloadFile: vi.fn(),
      isSynthesizing: false,
      settings: {},
    })
  })

  const userMessage = { role: 'user', content: 'original prompt' }

  it('shows the edit affordance on a user message', () => {
    render(<Message message={userMessage} userIndex={0} onRewind={vi.fn()} />)
    expect(screen.getByLabelText('Edit and resubmit message')).toBeInTheDocument()
  })

  it('does not show the edit affordance when no rewind handler is provided', () => {
    render(<Message message={userMessage} userIndex={null} onRewind={null} />)
    expect(screen.queryByLabelText('Edit and resubmit message')).not.toBeInTheDocument()
  })

  it('does not show the edit affordance on assistant messages', () => {
    const assistant = { role: 'assistant', content: 'a reply' }
    render(<Message message={assistant} userIndex={null} onRewind={vi.fn()} />)
    expect(screen.queryByLabelText('Edit and resubmit message')).not.toBeInTheDocument()
  })

  it('opens an editor prefilled with the message content', () => {
    render(<Message message={userMessage} userIndex={2} onRewind={vi.fn()} />)
    fireEvent.click(screen.getByLabelText('Edit and resubmit message'))
    const textarea = screen.getByLabelText('Edit message')
    expect(textarea).toBeInTheDocument()
    expect(textarea.value).toBe('original prompt')
  })

  it('calls onRewind with the user index and edited text on Send', () => {
    const onRewind = vi.fn()
    render(<Message message={userMessage} userIndex={2} onRewind={onRewind} />)
    fireEvent.click(screen.getByLabelText('Edit and resubmit message'))
    const textarea = screen.getByLabelText('Edit message')
    fireEvent.change(textarea, { target: { value: 'edited prompt' } })
    fireEvent.click(screen.getByText('Send'))
    expect(onRewind).toHaveBeenCalledWith(2, 'edited prompt')
  })

  it('Cancel closes the editor without calling onRewind', () => {
    const onRewind = vi.fn()
    render(<Message message={userMessage} userIndex={0} onRewind={onRewind} />)
    fireEvent.click(screen.getByLabelText('Edit and resubmit message'))
    fireEvent.click(screen.getByText('Cancel'))
    expect(onRewind).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('Edit message')).not.toBeInTheDocument()
  })

  it('Escape cancels the edit', () => {
    const onRewind = vi.fn()
    render(<Message message={userMessage} userIndex={0} onRewind={onRewind} />)
    fireEvent.click(screen.getByLabelText('Edit and resubmit message'))
    const textarea = screen.getByLabelText('Edit message')
    fireEvent.keyDown(textarea, { key: 'Escape' })
    expect(screen.queryByLabelText('Edit message')).not.toBeInTheDocument()
    expect(onRewind).not.toHaveBeenCalled()
  })

  it('does not submit an empty edit', () => {
    const onRewind = vi.fn()
    render(<Message message={userMessage} userIndex={0} onRewind={onRewind} />)
    fireEvent.click(screen.getByLabelText('Edit and resubmit message'))
    const textarea = screen.getByLabelText('Edit message')
    fireEvent.change(textarea, { target: { value: '   ' } })
    fireEvent.keyDown(textarea, { key: 'Enter' })
    expect(onRewind).not.toHaveBeenCalled()
  })
})
