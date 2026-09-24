/**
 * The joined-run alignment rule (issue #959), driven by the shared case
 * table in test/fixtures/transcript-alignment-cases.json.
 *
 * The same table drives the Python mirror in the PR-validation scenario
 * (fixtures/pr959/scenario.py). The mirror drifted from the client rule twice
 * during this PR's review -- once missing the prose-type coalescing, once
 * accepting a shorter store the client refuses -- and each time the
 * end-to-end check went on certifying a rule the shipped code does not apply.
 * One table, two consumers, so a change to the rule has to be made in both
 * places or this suite fails.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import { alignTranscript, isLiveOnlyRow } from '../utils/transcriptAlignment'

const here = dirname(fileURLToPath(import.meta.url))
const table = JSON.parse(
  readFileSync(resolve(here, '../../../test/fixtures/transcript-alignment-cases.json'), 'utf8')
)

// The wire shape the repository returns, mapped the way ChatContext maps it.
const toRow = (msg) => ({
  ...(msg.metadata || {}),
  role: msg.role,
  content: msg.content || '',
  type: msg.message_type || 'chat',
})

describe('transcript alignment (shared case table)', () => {
  it('has cases', () => {
    expect(table.cases.length).toBeGreaterThan(0)
  })

  for (const c of table.cases) {
    it(c.name, () => {
      const view = c.view.map(toRow).filter(m => !isLiveOnlyRow(m))
      const stored = c.stored.map(toRow)
      expect(alignTranscript(view, stored)).toBe(c.expect)
    })
  }
})
