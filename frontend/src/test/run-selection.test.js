/**
 * Which run the tracker keeps when a snapshot holds several for one
 * conversation (issue #1219). A table over every pair of runs, in both
 * snapshot orders, so no ordering of the list can change the answer.
 */
import { describe, it, expect } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { runOutranks, useConversationRuns } from '../hooks/chat/useConversationRuns'

const KINDS = [
  { kind: 'active-local', status: 'running', detached: false },
  { kind: 'active-detached', status: 'running', detached: true },
  { kind: 'finished-local', status: 'completed', detached: false },
  { kind: 'finished-detached', status: 'failed', detached: true },
]
const AGES = [{ age: 'older', created_at: 100 }, { age: 'newer', created_at: 200 }]

const RUNS = KINDS.flatMap(k => AGES.map(a => ({
  name: `${k.kind}/${a.age}`,
  run_id: `${k.kind}-${a.age}`,
  conversation_id: 'conv-1',
  status: k.status,
  created_at: a.created_at,
  ...(k.detached ? { detached: true } : {}),
  live: k.status === 'running',
})))

// The rule, written out independently of the implementation: live beats
// finished; then the newer one; then (only a true tie) the detached one.
const expectedWinner = (a, b) => {
  if (a.live !== b.live) return a.live ? a : b
  if (a.created_at !== b.created_at) return a.created_at > b.created_at ? a : b
  const aDetached = a.detached === true
  const bDetached = b.detached === true
  if (aDetached !== bDetached) return aDetached ? a : b
  return null // a true tie of equal runs
}

const PAIRS = RUNS.flatMap(a => RUNS.filter(b => a !== b).map(b => [a, b]))

describe('run selection in a snapshot', () => {
  it.each(PAIRS.map(([a, b]) => [a.name, b.name, a, b]))('%s vs %s', (_an, _bn, a, b) => {
    const winner = expectedWinner(a, b)
    // Both snapshot orders.
    for (const order of [[a, b], [b, a]]) {
      const { result, unmount } = renderHook(() => useConversationRuns())
      const wire = order.map(run => {
        const copy = { ...run }
        delete copy.live
        delete copy.name
        return copy
      })
      act(() => { result.current.handleRunFrame({ type: 'runs_snapshot', runs: wire }) })
      const kept = result.current.runsByConversation['conv-1']
      if (winner) expect(kept.run_id).toBe(winner.run_id)
      unmount()
    }
    // The comparator agrees with the table in both directions.
    if (winner) {
      expect(runOutranks(winner, winner === a ? b : a)).toBe(true)
      expect(runOutranks(winner === a ? b : a, winner)).toBe(false)
    }
  })

  it('an active local run replaces an older finished one in either order', () => {
    const live = { conversation_id: 'c', run_id: 'live', status: 'running', created_at: 300 }
    const done = { conversation_id: 'c', run_id: 'done', status: 'completed', created_at: 100 }
    for (const order of [[live, done], [done, live]]) {
      const { result, unmount } = renderHook(() => useConversationRuns())
      act(() => { result.current.handleRunFrame({ type: 'runs_snapshot', runs: order }) })
      expect(result.current.runsByConversation.c.run_id).toBe('live')
      unmount()
    }
  })

  it('a newer finished local run is not replaced by an older watched remote run', () => {
    const local = { conversation_id: 'c', run_id: 'local', status: 'completed', created_at: 500 }
    const remote = { conversation_id: 'c', run_id: 'remote', status: 'failed', created_at: 400, detached: true }
    for (const order of [[local, remote], [remote, local]]) {
      const { result, unmount } = renderHook(() => useConversationRuns())
      act(() => { result.current.handleRunFrame({ type: 'runs_snapshot', runs: order }) })
      expect(result.current.runsByConversation.c.run_id).toBe('local')
      unmount()
    }
  })
})
