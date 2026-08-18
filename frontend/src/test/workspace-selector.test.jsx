/**
 * WorkspaceSelector Component Tests
 *
 * A workspace bundles prompt + RAG sources + tools; these cover the switcher's
 * behavior: showing the active workspace, applying one, saving the current
 * context, and deleting.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import WorkspaceSelector from '../components/WorkspaceSelector'
import { useChat } from '../contexts/ChatContext'
import { useDialog, useToast } from '../components/ui/toastContext'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn(),
}))

vi.mock('../components/ui/toastContext', () => ({
  useDialog: vi.fn(),
  useToast: vi.fn(),
}))

vi.mock('lucide-react', () => ({
  Layers: () => <span data-testid="layers">L</span>,
  ChevronDown: () => <span data-testid="chevron-down">v</span>,
  Plus: () => <span data-testid="plus">+</span>,
  Save: () => <span data-testid="save">s</span>,
  Trash2: () => <span data-testid="trash">t</span>,
  Pencil: () => <span data-testid="pencil">p</span>,
}))

const WORKSPACES = [
  {
    id: 'ws-work',
    name: 'Work',
    description: 'Day job context',
    config: {
      active_prompt_key: 'userprompt:abc',
      selected_tools: ['files_read', 'files_write'],
      selected_prompts: [],
      selected_data_sources: ['corpus-a'],
      rag_enabled: true,
    },
  },
  {
    id: 'ws-home',
    name: 'Home',
    description: null,
    config: {
      active_prompt_key: null,
      selected_tools: [],
      selected_prompts: [],
      selected_data_sources: [],
      rag_enabled: false,
    },
  },
]

describe('WorkspaceSelector', () => {
  const switchWorkspace = vi.fn()
  const saveCurrentAsWorkspace = vi.fn()
  const updateActiveWorkspace = vi.fn()
  const renameWorkspace = vi.fn()
  const deleteWorkspace = vi.fn()
  const clearActiveWorkspace = vi.fn()
  const dialogPrompt = vi.fn()
  const dialogConfirm = vi.fn()

  const USER_PROMPTS = [
    { id: 'abc', title: 'Terse Code Reviewer', content: 'Be terse.' },
  ]

  const baseContext = {
    workspaces: WORKSPACES,
    userPrompts: USER_PROMPTS,
    prompts: [],
    workspacesLoading: false,
    activeWorkspaceId: null,
    switchWorkspace,
    saveCurrentAsWorkspace,
    updateActiveWorkspace,
    renameWorkspace,
    deleteWorkspace,
    clearActiveWorkspace,
  }

  const setContext = overrides => {
    useChat.mockReturnValue({ ...baseContext, ...overrides })
  }

  beforeEach(() => {
    vi.clearAllMocks()
    useDialog.mockReturnValue({ prompt: dialogPrompt, confirm: dialogConfirm })
    useToast.mockReturnValue({ success: vi.fn(), error: vi.fn(), info: vi.fn(), dismiss: vi.fn() })
    setContext()
  })

  const openDropdown = () => fireEvent.click(screen.getByRole('button', { name: /workspace/i }))

  it('shows a neutral label when no workspace is active', () => {
    render(<WorkspaceSelector />)
    expect(screen.getByText('Workspace')).toBeInTheDocument()
  })

  it('shows the active workspace name', () => {
    setContext({ activeWorkspaceId: 'ws-work' })
    render(<WorkspaceSelector />)
    expect(screen.getByText('Work')).toBeInTheDocument()
  })

  it('lists workspaces with a selection summary', () => {
    render(<WorkspaceSelector />)
    openDropdown()
    expect(screen.getByText('Day job context')).toBeInTheDocument()
    expect(screen.getByText('2 tools · 1 source · Terse Code Reviewer')).toBeInTheDocument()
    expect(screen.getByText('no selections')).toBeInTheDocument()
  })

  it('names the workspace prompt so you can see which one you are switching into', () => {
    render(<WorkspaceSelector />)
    openDropdown()
    expect(screen.getByText(/Terse Code Reviewer/)).toBeInTheDocument()
  })

  it('falls back to a generic label when the prompt no longer exists', () => {
    // A prompt deleted out from under a workspace must not blank the summary.
    setContext({ userPrompts: [] })
    render(<WorkspaceSelector />)
    openDropdown()
    expect(screen.getByText('2 tools · 1 source · custom prompt')).toBeInTheDocument()
  })

  it('applies a workspace when one is clicked', () => {
    render(<WorkspaceSelector />)
    openDropdown()
    fireEvent.click(screen.getByText('Work'))
    expect(switchWorkspace).toHaveBeenCalledWith('ws-work')
  })

  it('saves the current context under a new name', async () => {
    dialogPrompt.mockResolvedValue({ value: 'Project A', secondary: 'client work' })
    saveCurrentAsWorkspace.mockResolvedValue({ id: 'new', name: 'Project A' })

    render(<WorkspaceSelector />)
    openDropdown()
    fireEvent.click(screen.getByText('Save current context as workspace'))

    await waitFor(() =>
      expect(saveCurrentAsWorkspace).toHaveBeenCalledWith('Project A', 'client work')
    )
  })

  it('does not save when the name dialog is cancelled', async () => {
    dialogPrompt.mockResolvedValue(null)
    render(<WorkspaceSelector />)
    openDropdown()
    fireEvent.click(screen.getByText('Save current context as workspace'))
    await waitFor(() => expect(dialogPrompt).toHaveBeenCalled())
    expect(saveCurrentAsWorkspace).not.toHaveBeenCalled()
  })

  it('offers updating only when a workspace is active', () => {
    const { rerender } = render(<WorkspaceSelector />)
    openDropdown()
    expect(screen.queryByText(/Update "/)).not.toBeInTheDocument()

    setContext({ activeWorkspaceId: 'ws-work' })
    rerender(<WorkspaceSelector />)
    expect(screen.getByText('Update "Work" with current context')).toBeInTheDocument()
  })

  it('confirms before deleting', async () => {
    dialogConfirm.mockResolvedValue(true)
    deleteWorkspace.mockResolvedValue(true)

    render(<WorkspaceSelector />)
    openDropdown()
    fireEvent.click(screen.getByLabelText('Delete Work'))

    await waitFor(() => expect(deleteWorkspace).toHaveBeenCalledWith('ws-work'))
  })

  it('does not delete when the confirmation is declined', async () => {
    dialogConfirm.mockResolvedValue(false)
    render(<WorkspaceSelector />)
    openDropdown()
    fireEvent.click(screen.getByLabelText('Delete Home'))
    await waitFor(() => expect(dialogConfirm).toHaveBeenCalled())
    expect(deleteWorkspace).not.toHaveBeenCalled()
  })

  it('clicking a row action does not also switch workspace', async () => {
    dialogConfirm.mockResolvedValue(false)
    render(<WorkspaceSelector />)
    openDropdown()
    fireEvent.click(screen.getByLabelText('Delete Work'))
    await waitFor(() => expect(dialogConfirm).toHaveBeenCalled())
    expect(switchWorkspace).not.toHaveBeenCalled()
  })

  it('clears the active workspace via "No workspace"', () => {
    setContext({ activeWorkspaceId: 'ws-work' })
    render(<WorkspaceSelector />)
    openDropdown()
    fireEvent.click(screen.getByText('No workspace'))
    expect(clearActiveWorkspace).toHaveBeenCalled()
  })

  it('shows an empty state when the user has no workspaces', () => {
    setContext({ workspaces: [] })
    render(<WorkspaceSelector />)
    openDropdown()
    expect(screen.getByText(/No workspaces yet/)).toBeInTheDocument()
  })
})
