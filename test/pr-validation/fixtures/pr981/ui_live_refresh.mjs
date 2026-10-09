#!/usr/bin/env node
/**
 * Visual driver for PR #981: a joined conversation shows tool activity that
 * lands while its run is still executing (issue #980).
 *
 * Boots the same scripted mock model and backend as
 * test/pr-validation/fixtures/pr981/scenario.py (so nothing on the path is
 * stubbed), then drives the built frontend with Playwright:
 *
 *   1. Tab A starts a two-step atlas_sleep run and pauses on the first tool
 *      approval.
 *   2. Tab B (a second page -- the shape that receives none of the run's
 *      frames) opens the conversation from history while the run is paused.
 *      The transcript shows the "answer in progress" marker and the pending
 *      approval, with no completed tool row yet.
 *   3. Tab A approves the first call; the tool executes and the run pauses on
 *      the second. Without waiting for the run to end, tab B's periodic poll
 *      appends the first call's completed atlas_sleep row.
 *
 * Screenshots are written beside this script
 * (live-refresh-before-approval.png, live-refresh-tool-row.png), captured
 * while the run is still in flight; debug screenshots on failure go to the
 * run's temp directory.
 *
 * The frontend is rebuilt first (opt out with SKIP_UI_BUILD=1), so the
 * screenshots exercise the commit under test rather than a stale dist/.
 *
 * Prerequisites: `cd test_e2e && npm install && npx playwright install
 * chromium` (the only playwright install in the repo). Run from the repo root:
 *
 *   node test/pr-validation/fixtures/pr981/ui_live_refresh.mjs
 */
import { spawn } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '../../../../test_e2e/node_modules/playwright/index.mjs'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../../../..')
const FIXTURES = join(ROOT, 'test/pr-validation/fixtures/pr981')
const WORK = mkdtempSync(join(tmpdir(), 'pr981-ui-'))

const freePort = () => new Promise((resolve) => {
  const s = createServer()
  s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => resolve(p)) })
})

const waitFor = async (url, tries = 60) => {
  for (let i = 0; i < tries; i++) {
    try { const r = await fetch(url); if (r.ok) return true } catch { /* not up yet */ }
    await new Promise(r => setTimeout(r, 1000))
  }
  return false
}

const MOCK_PORT = await freePort()
const ATLAS_PORT = await freePort()
const procs = []
let tabA = null
let tabB = null
const run = (cmd, args, opts) => {
  const p = spawn(cmd, args, { stdio: 'inherit', ...opts })
  procs.push(p)
  return p
}

const runToCompletion = (cmd, args, opts) => new Promise((resolve, reject) => {
  const p = spawn(cmd, args, { stdio: 'inherit', ...opts })
  p.on('exit', (code) => (code === 0 ? resolve() : reject(new Error(`${cmd} exited ${code}`))))
  p.on('error', reject)
})

try {
  // The screenshots must exercise this commit, not whatever was last built.
  // The repository requires a frontend build after UI changes before taking
  // screenshots; the isolated backend below then serves the fresh dist/.
  if (!process.env.SKIP_UI_BUILD) {
    console.log('building frontend (set SKIP_UI_BUILD=1 to reuse dist/)')
    await runToCompletion('npm', ['run', 'build'], { cwd: join(ROOT, 'frontend') })
  }

  run('python', [join(FIXTURES, 'mock_llm.py')], {
    env: { ...process.env, MOCK_LLM_PORT: String(MOCK_PORT) },
  })
  const configDir = join(WORK, 'config')
  mkdirSync(configDir, { recursive: true })
  mkdirSync(join(WORK, 'data'), { recursive: true })
  mkdirSync(join(WORK, 'logs'), { recursive: true })
  for (const f of ['mcp.json', 'rag-sources.json']) {
    writeFileSync(join(configDir, f), readFileSync(join(FIXTURES, f)))
  }
  writeFileSync(
    join(configDir, 'llmconfig.yml'),
    readFileSync(join(FIXTURES, 'llmconfig.yml'), 'utf8').replaceAll('__MOCK_LLM_PORT__', String(MOCK_PORT)),
  )
  run('python', ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(ATLAS_PORT)], {
    cwd: join(ROOT, 'atlas'),
    env: {
      ...process.env,
      DEBUG_MODE: 'true',
      FEATURE_CHAT_HISTORY_ENABLED: 'true',
      FEATURE_AGENT_MODE_AVAILABLE: 'true',
      FEATURE_TOOLS_ENABLED: 'true',
      FEATURE_RAG_ENABLED: 'false',
      FEATURE_WORKSPACES_ENABLED: 'false',
      USE_MOCK_S3: 'true',
      REQUIRE_TOOL_APPROVAL_BY_DEFAULT: 'true',
      FORCE_TOOL_APPROVAL_GLOBALLY: 'false',
      CHAT_HISTORY_DB_URL: `duckdb:///${WORK}/data/chat_history.db`,
      APP_LOG_DIR: join(WORK, 'logs'),
      APP_CONFIG_DIR: configDir,
      PYTHONPATH: ROOT,
    },
  })
  if (!(await waitFor(`http://127.0.0.1:${ATLAS_PORT}/api/health`))) throw new Error('backend did not come up')
  if (!(await waitFor(`http://127.0.0.1:${MOCK_PORT}/health`))) throw new Error('mock model did not come up')
  console.log('backend and mock model are up')

  const browser = await chromium.launch()
  const ctx = await browser.newContext()
  // Force auto-approve off so the run stays paused for tab B to join.
  await ctx.addInitScript(() => {
    try { localStorage.setItem('chatui-settings', JSON.stringify({ autoApproveTools: false })) } catch { /* ignore */ }
  })
  tabA = await ctx.newPage()
  tabB = await ctx.newPage()
  const base = `http://127.0.0.1:${ATLAS_PORT}`

  const watch = (name, pg) => {
    pg.on('console', (m) => {
      const t = m.text()
      if (/error|run_started|run_status|runs_snapshot|approv/i.test(t)) console.log(`[console ${name}]`, t.slice(0, 200))
    })
    pg.on('websocket', (ws) => {
      ws.on('framereceived', (e) => {
        try {
          const f = JSON.parse(e.payload)
          if (['run_started', 'run_status', 'runs_snapshot', 'tool_approval_request'].includes(f.type)) {
            console.log(`[ws ${name}]`, f.type, f.run?.conversation_id || f.conversation_id || '', f.runs?.length ?? '')
          }
        } catch { /* non-JSON */ }
      })
    })
  }
  watch('A', tabA)
  watch('B', tabB)
  console.log('work dir:', WORK)

  // Tab A: server save mode is what puts the in-flight run in the history list.
  await tabA.goto(base)
  await tabA.getByRole('textbox', { name: /Type a message/ }).waitFor()
  const modeButton = tabA.getByRole('button', { name: /Incognito|Saved Locally|Saved to Server/ })
  await modeButton.waitFor()
  for (let i = 0; i < 3; i++) {
    if ((await modeButton.textContent()).includes('Saved to Server')) break
    await modeButton.click()
    await tabA.waitForTimeout(500)
  }
  if (!((await modeButton.textContent()).includes('Saved to Server'))) {
    throw new Error('could not switch to server save mode')
  }
  console.log('tab A: server save mode')

  // Tab B connects early so it receives the run's lifecycle broadcasts; it is
  // the shape that receives none of the run's transcript frames.
  await tabB.goto(base)
  await tabB.getByRole('textbox', { name: /Type a message/ }).waitFor()
  console.log('tab B: connected')

  // Select atlas_sleep so the turn is admitted as a tracked agent run. The
  // chat-bar picker shows the tool without its server prefix ("sleep" under
  // the Atlas group).
  await tabA.getByTitle('Turn tools on and off').click()
  const sleepRow = tabA.locator('button').filter({ hasText: /^sleep/ }).first()
  await sleepRow.waitFor({ timeout: 5000 })
  if ((await sleepRow.getAttribute('aria-pressed')) !== 'true') {
    await sleepRow.click()
  }
  await tabA.keyboard.press('Escape')
  console.log('tab A: atlas_sleep selected')

  // Start the two-step run; it pauses on the first tool approval.
  const composer = tabA.getByRole('textbox', { name: /Type a message/ })
  await composer.fill('label=UI981 sleep=4 steps=2 words=3')
  // Enter submits (Shift+Enter inserts a newline).
  await composer.press('Enter')
  const approveButton = tabA.getByRole('button', { name: /Approve/ }).first()
  await approveButton.waitFor({ timeout: 30000 })
  console.log('tab A: run paused on the first approval')

  // Tab B opens the conversation while the run is paused on the first
  // approval. The run's live record already holds the pending call (the
  // approval row is replayed), and the "answer in progress" marker is what
  // marks this as the joined view. No tool call has *completed* yet, which is
  // what the next step waits on.
  await tabB.getByText(/UI981/).first().click({ timeout: 15000 })
  const marker = tabB.getByText('Answer in progress — it will refresh when the response finishes.')
  await marker.waitFor({ timeout: 15000 })
  // A completed tool row carries a status glyph with aria-label SUCCESS; the
  // pending approval row does not. Counting them avoids mistaking a replayed
  // approval label for the persisted row.
  const successGlyphs = tabB.locator('[role="img"][aria-label="SUCCESS"]')
  const completedBefore = await successGlyphs.count()
  if (completedBefore !== 0) {
    throw new Error('a tool row had already completed before the approval; rerun')
  }
  await tabB.screenshot({ path: join(FIXTURES, 'live-refresh-before-approval.png') })
  console.log('PASS: joined tab opened the in-flight record (marker, pending approval, no completed row)')

  // Approve the first call. The tool executes and the run pauses on the
  // second approval -- which nothing answers -- so the run stays in flight.
  // Tab B's poll must append the now-persisted completed row without the run
  // ending.
  await approveButton.click()
  // The next SUCCESS glyph is the first call's completed row.
  await successGlyphs.nth(completedBefore).waitFor({ timeout: 30000 })
  const completedRow = tabB.locator('button').filter({ has: tabB.locator('[role="img"][aria-label="SUCCESS"]') })
  const completedText = await completedRow.nth(completedBefore).textContent()
  if (!completedText.includes('atlas_sleep')) {
    throw new Error(`the completing row was not atlas_sleep: ${completedText}`)
  }
  // The run is still executing: the pending-approval marker is still on
  // screen, so this row arrived mid-run, not from the run-end reload.
  if (!(await marker.count())) {
    throw new Error('the run had already ended when the tool row appeared; rerun')
  }
  await tabB.screenshot({ path: join(FIXTURES, 'live-refresh-tool-row.png') })
  console.log(`PASS: joined tab appended a completed atlas_sleep row while the run was still in progress (screenshot: ${join(FIXTURES, 'live-refresh-tool-row.png')})`)

  await browser.close()
} catch (err) {
  try {
    if (tabA) await tabA.screenshot({ path: join(WORK, 'debug-tabA.png') })
    if (tabB) await tabB.screenshot({ path: join(WORK, 'debug-tabB.png') })
    console.log(`debug screenshots: ${join(WORK, 'debug-tabA.png')} / ${join(WORK, 'debug-tabB.png')}`)
  } catch { /* best effort */ }
  throw err
} finally {
  for (const p of procs) p.kill()
}
