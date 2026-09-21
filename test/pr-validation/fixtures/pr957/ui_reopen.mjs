#!/usr/bin/env node
/**
 * Visual driver for PR #962: the mid-stream reopen, in a real browser.
 *
 * Boots the same scripted mock model and backend as
 * test/pr-validation/test_pr957_mid_stream_reopen.sh (so nothing on the path
 * is stubbed), then drives the built frontend with Playwright:
 *
 *   1. Tab A starts a slow-streaming run (200 words, 600 ms apart) and
 *      navigates away with New Chat;
 *   2. Tab B (a second browser page -- the shape that receives no live
 *      frames) opens the conversation from history mid-stream and asserts:
 *      the bubble starts at the answer's first word, the "answer in
 *      progress" marker is shown, and no live caret blinks;
 *   3. after the run ends, tab B's view settles to the complete stored
 *      transcript and the marker is gone.
 *
 * Screenshots land beside this script (reopen-mid-stream-marker.png,
 * reopen-settled-transcript.png).
 *
 * Prerequisites: `cd test_e2e && npm install && npx playwright install
 * chromium` (the only playwright install in the repo), and the frontend
 * built (`cd frontend && npm run build`). Run from the repo root:
 *
 *   node test/pr-validation/fixtures/pr957/ui_reopen.mjs
 */
import { spawn } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '../../../../test_e2e/node_modules/playwright/index.mjs'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../../../..')
const FIXTURES = join(ROOT, 'test/pr-validation/fixtures/pr957')
const WORK = mkdtempSync(join(tmpdir(), 'pr957-ui-'))

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

try {
  // Mock model + backend, exactly as the shell driver does it.
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
      FEATURE_AGENT_PORTAL_ENABLED: 'false',
      USE_MOCK_S3: 'true',
      REQUIRE_TOOL_APPROVAL_BY_DEFAULT: 'false',
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
  tabA = await ctx.newPage()
  tabB = await ctx.newPage()
  const base = `http://127.0.0.1:${ATLAS_PORT}`

  // Server save mode is what puts the in-flight run in the history list.
  // The mode button cycles Incognito -> Saved Locally -> Saved to Server;
  // click through until it lands, waiting for each render.
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

  // Tab A starts the slow run and navigates away mid-stream.
  await tabA.getByRole('textbox', { name: /Type a message/ }).fill('stream_ms=600 words=200 label=UI957 steps=0')
  await tabA.getByTestId('send-button').click()
  await tabA.waitForTimeout(9000)
  await tabA.getByRole('button', { name: /New Chat/ }).first().click()
  console.log('tab A: run started, navigated away')

  // Tab B opens the conversation from history mid-stream.
  await tabB.goto(base)
  await tabB.getByText(/UI957/).first().click()
  console.log('tab B: conversation opened')
  const bubble = tabB.locator('main p', { hasText: 'FINAL[UI957] UI957-w1 ' }).first()
  await bubble.waitFor({ timeout: 15000 })
  const marker = tabB.getByText('Answer in progress — it will refresh when the response finishes.')
  await marker.waitFor({ timeout: 5000 })
  const bubbleText = await bubble.textContent()
  if (!bubbleText.startsWith('FINAL[UI957] UI957-w1 ')) {
    throw new Error(`reopened bubble does not start at the beginning: ${bubbleText.slice(0, 80)}`)
  }
  if (bubbleText.includes('END[UI957]')) throw new Error('reopen landed after the run ended; rerun')
  if (await tabB.getByLabel('Generating response...').count()) {
    throw new Error('a replayed bubble must not show the live caret')
  }
  await tabB.screenshot({ path: join(FIXTURES, 'reopen-mid-stream-marker.png') })
  console.log('PASS: mid-stream reopen shows the answer from its beginning, with the marker, no live caret')

  // The run ends; tab B settles to the complete stored transcript.
  await tabB.locator('main p', { hasText: 'END[UI957]' }).first().waitFor({ timeout: 150000 })
  await marker.waitFor({ state: 'detached', timeout: 15000 }).catch(() => {})
  if (await marker.count()) throw new Error('the marker survived the run-end reload')
  await tabB.screenshot({ path: join(FIXTURES, 'reopen-settled-transcript.png') })
  console.log('PASS: run-end reload settles the view to the complete stored transcript, marker gone')

  await browser.close()
} catch (err) {
  try {
    if (tabA) await tabA.screenshot({ path: join(FIXTURES, 'debug-tabA.png') })
    if (tabB) await tabB.screenshot({ path: join(FIXTURES, 'debug-tabB.png') })
  } catch { /* best effort */ }
  throw err
} finally {
  for (const p of procs) p.kill()
}
