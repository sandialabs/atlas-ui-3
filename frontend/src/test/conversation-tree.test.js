/**
 * Sidebar parent/child tree construction (issue #1081).
 *
 * The relationship is drawn from conversation ids: a saved child carries
 * `parent_conversation_id` in its metadata, and an unsaved child carries it on
 * its live run record. Everything here is a pure function of the fetched list
 * plus the run map, so these tests exercise the same input the sidebar uses.
 */

import { describe, it, expect } from 'vitest'

import { getDisplayConversations } from '../utils/getDisplayConversations'
import { arrangeConversations } from '../utils/conversationTree'

const conv = (id, extra = {}) => ({
  id,
  title: id,
  preview: '',
  updated_at: '2026-10-17T00:00:00Z',
  message_count: 2,
  ...extra,
})

const base = {
  messages: [],
  activeConversationId: null,
  chatHistoryEnabled: true,
  saveMode: 'server',
}

describe('arrangeConversations', () => {
  it('nests a child under its parent and leaves independent chats at the top', () => {
    const rows = arrangeConversations([
      conv('parent', { parent_conversation_id: null }),
      conv('independent'),
      conv('child', { parent_conversation_id: 'parent' }),
    ], {})

    expect(rows.map(r => r.id)).toEqual(['parent', 'child', 'independent'])
    expect(rows[0]).toMatchObject({ _depth: 0, _childCount: 1, _hasChildren: true })
    expect(rows[1]).toMatchObject({ _depth: 1, _childCount: 0, _ancestorIds: ['parent'] })
    expect(rows[2]._depth).toBe(0)
  })

  it('supports multiple levels of nesting', () => {
    const rows = arrangeConversations([
      conv('root'),
      conv('child', { parent_conversation_id: 'root' }),
      conv('grandchild', { parent_conversation_id: 'child' }),
      conv('great-grandchild', { parent_conversation_id: 'grandchild' }),
    ], {})

    expect(rows.map(r => [r.id, r._depth])).toEqual([
      ['root', 0],
      ['child', 1],
      ['grandchild', 2],
      ['great-grandchild', 3],
    ])
    expect(rows[1]._ancestorIds).toEqual(['root'])
    expect(rows[2]._ancestorIds).toEqual(['root', 'child'])
  })

  it('treats a child whose parent is missing as a top-level entry', () => {
    // Deleted, filtered out by search, or not on this page of results.
    const rows = arrangeConversations([
      conv('orphan', { parent_conversation_id: 'gone' }),
      conv('normal'),
    ], {})

    expect(rows.map(r => r.id)).toEqual(['orphan', 'normal'])
    expect(rows[0]._depth).toBe(0)
    expect(rows[0]._hasChildren).toBe(false)
  })

  it('breaks a cycle instead of recursing forever', () => {
    const rows = arrangeConversations([
      conv('a', { parent_conversation_id: 'b' }),
      conv('b', { parent_conversation_id: 'a' }),
      conv('normal'),
    ], {})

    expect(rows.map(r => r.id).sort()).toEqual(['a', 'b', 'normal'])
    expect(rows.every(r => r._depth === 0)).toBe(true)
  })

  it('ignores a self-parent', () => {
    const rows = arrangeConversations([
      conv('self', { parent_conversation_id: 'self' }),
    ], {})
    expect(rows).toHaveLength(1)
    expect(rows[0]._depth).toBe(0)
  })

  it('keeps sibling order from the fetched list (newest first)', () => {
    const rows = arrangeConversations([
      conv('parent'),
      conv('newer-child', { parent_conversation_id: 'parent' }),
      conv('older-child', { parent_conversation_id: 'parent' }),
      conv('other'),
    ], {})
    expect(rows.map(r => r.id)).toEqual([
      'parent', 'newer-child', 'older-child', 'other',
    ])
  })

  it('folds the subtree runs into a collapsed-parent aggregate', () => {
    const rows = arrangeConversations([
      conv('parent'),
      conv('running-child', { parent_conversation_id: 'parent' }),
      conv('waiting-child', { parent_conversation_id: 'parent' }),
    ], {
      'running-child': { conversation_id: 'running-child', status: 'running' },
      'waiting-child': { conversation_id: 'waiting-child', status: 'waiting_for_input' },
    })

    expect(rows[0]._aggregateRun).toMatchObject({ status: 'waiting_for_input', count: 1 })
  })

  it('counts several descendants in the same state', () => {
    const rows = arrangeConversations([
      conv('parent'),
      conv('a', { parent_conversation_id: 'parent' }),
      conv('b', { parent_conversation_id: 'parent' }),
    ], {
      a: { conversation_id: 'a', status: 'running' },
      b: { conversation_id: 'b', status: 'running' },
    })
    expect(rows[0]._aggregateRun).toMatchObject({ status: 'running', count: 2 })
  })

  it('does not aggregate a finished run', () => {
    const rows = arrangeConversations([
      conv('parent'),
      conv('child', { parent_conversation_id: 'parent' }),
    ], {
      child: { conversation_id: 'child', status: 'completed' },
    })
    expect(rows[0]._aggregateRun).toBeNull()
  })

  it('does not mutate its input rows', () => {
    const input = [conv('parent'), conv('child', { parent_conversation_id: 'parent' })]
    arrangeConversations(input, {})
    expect(input[0]._depth).toBeUndefined()
    expect(input[1]._depth).toBeUndefined()
  })
})

describe('getDisplayConversations with parent links', () => {
  it('nests a saved child under its saved parent', () => {
    const rows = getDisplayConversations({
      ...base,
      conversations: [
        conv('parent'),
        conv('child', { parent_conversation_id: 'parent' }),
      ],
    })
    expect(rows.map(r => [r.id, r._depth])).toEqual([['parent', 0], ['child', 1]])
  })

  it('nests an in-flight child under an in-flight parent run', () => {
    const rows = getDisplayConversations({
      ...base,
      conversations: [],
      runsByConversation: {
        'parent-run-conv': {
          run_id: 'r-parent', conversation_id: 'parent-run-conv', status: 'running', title: 'Parent task',
        },
        'child-run-conv': {
          run_id: 'r-child', conversation_id: 'child-run-conv', status: 'waiting_for_input',
          title: 'Child task', parent_conversation_id: 'parent-run-conv',
        },
      },
    })

    // Newest first at the top of the raw list; the tree still puts the parent
    // before its child.
    expect(rows.map(r => [r.id, r._depth])).toEqual([
      ['parent-run-conv', 0],
      ['child-run-conv', 1],
    ])
    expect(rows[0]._aggregateRun).toMatchObject({ status: 'waiting_for_input', count: 1 })
  })

  it('nests an in-flight child under a saved parent that is already listed', () => {
    const rows = getDisplayConversations({
      ...base,
      conversations: [conv('parent')],
      runsByConversation: {
        child: {
          run_id: 'r-child', conversation_id: 'child', status: 'running',
          title: 'Child task', parent_conversation_id: 'parent',
        },
      },
    })
    expect(rows.map(r => [r.id, r._depth])).toEqual([['parent', 0], ['child', 1]])
  })

  it('shows a live child with a missing parent as a top-level run row', () => {
    const rows = getDisplayConversations({
      ...base,
      conversations: [conv('unrelated')],
      runsByConversation: {
        child: {
          run_id: 'r-child', conversation_id: 'child', status: 'running',
          parent_conversation_id: 'gone',
        },
      },
    })
    const child = rows.find(r => r.id === 'child')
    expect(child._depth).toBe(0)
    expect(child._run).toBe(true)
  })

  it('prefers the saved parent link over a stale run record', () => {
    const rows = getDisplayConversations({
      ...base,
      conversations: [
        conv('parent'),
        conv('child', { parent_conversation_id: 'parent' }),
      ],
      runsByConversation: {
        child: { conversation_id: 'child', status: 'running', parent_conversation_id: 'old-parent' },
      },
    })
    expect(rows.map(r => r.id)).toEqual(['parent', 'child'])
    expect(rows[1]._depth).toBe(1)
  })
})
