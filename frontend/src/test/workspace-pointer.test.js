/**
 * Regression tests for the active-workspace pointer.
 *
 * The pointer lives in localStorage and survives refreshes, but the workspace
 * list is fetched asynchronously. Clearing the pointer against the empty
 * pre-fetch list wiped the active workspace on every page load.
 */

import { describe, it, expect } from 'vitest'
import { isStaleWorkspacePointer } from '../hooks/useWorkspaces'

const LIST = [{ id: 'ws-1', name: 'Work' }, { id: 'ws-2', name: 'Home' }]

const check = overrides => isStaleWorkspacePointer({
  activeWorkspaceId: 'ws-1',
  configReady: true,
  enabled: true,
  loaded: true,
  workspaces: LIST,
  ...overrides,
})

describe('isStaleWorkspacePointer', () => {
  it('keeps the pointer until the config payload lands', () => {
    // Feature flags default to off, so an early check reads the feature as
    // disabled and would wipe the pointer on every page load.
    expect(check({ configReady: false, enabled: false, loaded: false })).toBe(false)
  })

  it('keeps the pointer while the list has not loaded yet', () => {
    expect(check({ loaded: false, workspaces: [] })).toBe(false)
  })

  it('keeps a pointer that matches a loaded workspace', () => {
    expect(check({})).toBe(false)
  })

  it('drops a pointer to a workspace that no longer exists', () => {
    expect(check({ activeWorkspaceId: 'deleted' })).toBe(true)
  })

  it('drops the pointer when the feature is off once config is known', () => {
    expect(check({ enabled: false, loaded: false })).toBe(true)
  })

  it('is a no-op when no workspace is active', () => {
    expect(check({ activeWorkspaceId: null, workspaces: [] })).toBe(false)
  })

  it('tolerates a missing list', () => {
    expect(check({ workspaces: undefined })).toBe(true)
  })
})
