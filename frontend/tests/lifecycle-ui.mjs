/** Real React lifecycle regressions. Run only through scripts/check_frontend.py.
 * No backend starts: API responses and storage below are entirely synthetic.
 * Every unrecognised API, external URL, popup or browser error fails the check.
 */
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { readFile, realpath, stat, writeFile } from 'node:fs/promises'
import { join, resolve, extname, sep } from 'node:path'
import { chromium, expect } from '@playwright/test'

const root = process.cwd()
const staticRoot = await realpath(join(root, 'lifecycle-dist'))
const browserExecutable = process.env.KNOWRA_BROWSER_EXECUTABLE
if (!browserExecutable) throw new Error('Set KNOWRA_BROWSER_EXECUTABLE to an installed Chrome/Chromium; no browser is downloaded')
const requests = []
const violations = []
let context
const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://127.0.0.1')
    const file = await realpath(resolve(staticRoot, '.' + decodeURIComponent(url.pathname)))
    if (!file.startsWith(staticRoot + sep) || !(await stat(file)).isFile()) throw new Error('Outside static fixture')
    const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf' }[extname(file)] || 'application/octet-stream'
    res.writeHead(200, { 'content-type': mime })
    res.end(await readFile(file))
  } catch {
    res.writeHead(404)
    res.end('No backend or fallback exists in the lifecycle fixture')
  }
})
await new Promise((done, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', done) })
const origin = `http://127.0.0.1:${server.address().port}`
let closing
let cleanupConfirmed = false
function close() {
  return closing ||= (async () => {
    let browserError
    try { await context?.close() }
    catch (error) { browserError = error }
    finally {
      server.closeAllConnections()
      await new Promise((done, reject) => server.close(error => error ? reject(error) : done()))
    }
    cleanupConfirmed = !browserError && !server.listening
    if (browserError) throw browserError
  })()
}
for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => { void close().finally(() => process.exit(128)) })

const sessions = {
  activeSessionId: 'fixture-a',
  sessions: [
    { id: 'fixture-a', title: 'Fixture Alpha', question: 'Alpha draft', createdAt: '2026-01-01', updatedAt: '2026-01-02', turns: [
      { role: 'user', content: 'First synthetic question' }, { role: 'assistant', content: 'First synthetic answer' },
      { role: 'user', content: 'Second synthetic question' }, { role: 'assistant', content: 'Second synthetic answer' },
    ] },
    { id: 'fixture-b', title: 'Fixture Beta', question: 'Beta draft', createdAt: '2026-01-01', updatedAt: '2026-01-01', turns: [] },
  ],
}
const indexStatus = { exists: true, path: 'fixture/index.md', size: 1024, current_papers: 2, current_concepts: 0, stale: false }
const chat = { messages: [], thread_created_at: null, expires_at: null, days_remaining: null, ttl_days: 30, ready: true }
const notes = blocks => '<!--notes-v2-->\n' + JSON.stringify(blocks)
function paper(id, title, category) {
  return { id, title, filename: `fixture-${id}.pdf`, filepath: `fixture/${id}.pdf`, authors: ['Synthetic Author'], num_pages: 1,
    processed: true, processed_at: null, paper_category: category, paper_category_model: category,
    paper_category_override: category, paper_category_source: 'manual', paper_team: null, paper_team_model: null,
    paper_team_override: null, paper_team_source: 'none', learning_status: 'not_started', year: 2026, error: null,
    created_at: null, extracted_text: null, raw_llm_response: JSON.stringify({ title, abstract_summary: 'Synthetic abstract' }),
    extraction: { title, abstract_summary: 'Synthetic abstract' }, extraction_model: 'fixture-only',
    notes: notes([{ id: `note-${id}`, title: `${title} note`, content: `${title} original content` }]),
    has_first_page_image: true, chat: structuredClone(chat), knowledge_nodes: [] }
}
const json = (route, value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) })
const deferred = () => { let release; const promise = new Promise(done => { release = done }); return { promise, release } }
// Wait for response handlers and React's next painted state before asserting
// absence; an immediate negative assertion could pass before a stale callback.
const settled = page => page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))))

async function newPage(component, apiHandler) {
  const page = await context.newPage()
  page.setDefaultTimeout(8000)
  page.on('pageerror', error => violations.push(String(error)))
  page.on('popup', popup => { violations.push('Unexpected popup'); void popup.close() })
  await page.addInitScript(seed => {
    localStorage.clear()
    localStorage.setItem('knowra.locale', 'zh')
    localStorage.setItem('knowra:ask-drawer:sessions', JSON.stringify(seed))
  }, sessions)
  await page.routeWebSocket(/.*/, ws => { violations.push('Unexpected WebSocket'); ws.close() })
  await page.route('**/*', async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) { violations.push(`External request blocked: ${url.origin}`); return route.abort() }
    if (url.pathname.startsWith('/api/')) {
      requests.push(`${request.method()} ${url.pathname}`)
      if (await apiHandler(route, url.pathname, request)) return
      violations.push(`Unmocked API blocked: ${request.method()} ${url.pathname}`)
      return route.abort()
    }
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 })
    if (url.pathname === '/tests/lifecycle-harness.html' || url.pathname.startsWith('/assets/')) return route.continue()
    violations.push(`Unexpected path blocked: ${url.pathname}`)
    return route.abort()
  })
  await page.goto(`${origin}/tests/lifecycle-harness.html?component=${component}`)
  return page
}
const tests = []
async function test(name, action) {
  const started = Date.now()
  try { await action(); assert.deepEqual(violations, []); tests.push({ name, status: 'passed', ms: Date.now() - started }) }
  catch (error) { tests.push({ name, status: 'failed', error: String(error), ms: Date.now() - started }); throw error }
  finally { for (const page of context.pages()) await page.close() }
  console.log(JSON.stringify(tests.at(-1)))
}

try {
  context = await chromium.launchPersistentContext(join(root, 'tmp', 'lifecycle-profile'), {
    executablePath: browserExecutable, headless: true, viewport: { width: 1800, height: 1100 },
    serviceWorkers: 'block', acceptDownloads: false,
    env: { PATH: process.env.PATH, HOME: join(root, 'tmp'), TMPDIR: join(root, 'tmp') },
    args: ['--disable-background-networking', '--disable-component-update', '--no-first-run', '--disable-sync',
      '--no-proxy-server', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'],
  })
  // A popup's initial request precedes the page popup event. This context-level
  // deny rule catches it; managed pages install their narrower fixture routes.
  await context.route('**/*', route => { violations.push('Unexpected context request blocked'); return route.abort() })
  await context.routeWebSocket(/.*/, ws => { violations.push('Unexpected context WebSocket'); ws.close() })
  await test('Ask session drafts, error clearing and synthesis title ownership', async () => {
    let asks = 0
    const page = await newPage('ask', async (route, path) => {
      if (path === '/api/wiki/index/status') { await json(route, indexStatus); return true }
      if (path === '/api/wiki/ask') { asks++; await json(route, { detail: 'Synthetic ask failure' }, 400); return true }
      return false
    })
    const input = page.getByPlaceholder(/^问点什么/)
    const select = page.getByTitle('切换已保存会话')
    await expect(input).toHaveValue('Alpha draft')
    await input.fill('Keep Alpha input')
    await select.selectOption('fixture-b')
    await expect(input).toHaveValue('Beta draft')
    await select.selectOption('fixture-a')
    await expect(input).toHaveValue('Keep Alpha input')
    await page.getByRole('button', { name: '发送', exact: true }).click()
    await expect(page.getByText('Synthetic ask failure', { exact: true })).toBeVisible()
    await select.selectOption('fixture-b')
    await expect(page.getByText('Synthetic ask failure', { exact: true })).toHaveCount(0)
    await expect(input).toHaveValue('Beta draft')
    await select.selectOption('fixture-a')
    await page.getByRole('button', { name: '存为概念页', exact: true }).last().click()
    const title = page.getByPlaceholder('给这个综合答案起个名字')
    await title.fill('User edited title')
    await page.getByRole('button', { name: '当前回答', exact: true }).click()
    await expect(title).toHaveValue('User edited title')
    await title.fill('')
    await page.getByRole('button', { name: '当前会话', exact: true }).click()
    await expect(title).toHaveValue('')
    assert.equal(asks, 1, 'One click must issue exactly one ask')
  })
  await test('Ask reopen ignores stale index and cross-session export responses', async () => {
    const oldIndex = deferred(), exportReply = deferred()
    let indexes = 0, exports = 0
    try {
      const page = await newPage('ask', async (route, path) => {
        if (path === '/api/wiki/index/status') {
          indexes++
          if (indexes === 1) { await oldIndex.promise; await json(route, { ...indexStatus, exists: false }) }
          else await json(route, { ...indexStatus, current_papers: 77 })
          return true
        }
        if (path === '/api/wiki/outputs/render') { exports++; await exportReply.promise; await json(route, { rel_path: 'fixture-old-export.md' }); return true }
        return false
      })
      await expect.poll(() => indexes).toBe(1)
      await page.getByTitle('关闭', { exact: true }).click()
      await page.getByRole('button', { name: 'Open test drawer' }).click()
      await expect(page.getByText(/index\.md · 1\.0 KB · 77/)).toBeVisible()
      const olderResponse = page.waitForResponse(response => response.url().endsWith('/wiki/index/status'))
      oldIndex.release()
      await olderResponse
      await settled(page)
      await expect(page.getByText(/index\.md · 1\.0 KB · 77/)).toBeVisible()
      await page.getByRole('button', { name: '报告', exact: true }).last().click()
      await expect.poll(() => exports).toBe(1)
      await page.getByTitle('切换已保存会话').selectOption('fixture-b')
      const response = page.waitForResponse(r => r.url().endsWith('/wiki/outputs/render'))
      exportReply.release()
      await response
      await settled(page)
      await expect(page.getByText(/fixture-old-export/)).toHaveCount(0)
      assert.equal(indexes, 2, 'Close/reopen must issue one refresh per opening')
    } finally { oldIndex.release(); exportReply.release() }
  })
  await test('Review keeps active note draft, merges refreshed notes and resets on paper switch', async () => {
    const papers = new Map([[101, paper(101, 'Fixture Alpha', 'LLM')], [102, paper(102, 'Fixture Beta', 'VLM')]])
    let savedNotes
    let noteSaves = 0
    let categorySaves = 0
    const page = await newPage('review', async (route, path, request) => {
      if (path === '/api/papers') { await json(route, [...papers.values()]); return true }
      if (path === '/api/paper-categories') { await json(route, { categories: ['LLM', 'VLM'].map(name => ({ name, builtin: true, removable: false, count: 1 })) }); return true }
      if (path === '/api/status') { await json(route, { running: false, current: null }); return true }
      const match = path.match(/^\/api\/papers\/(101|102)(?:\/(category|notes|first_page))?$/)
      if (!match) return false
      const item = papers.get(Number(match[1]))
      if (match[2] === 'first_page') { await route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>' }); return true }
      if (match[2] === 'category') {
        categorySaves++
        item.paper_category = item.paper_category_override = request.postDataJSON().category
        item.notes = notes([{ id: 'note-101', title: 'Fixture Alpha note', content: 'Externally refreshed content' }, { id: 'external', title: 'External added note', content: 'External content' }])
      }
      if (match[2] === 'notes') {
        noteSaves++
        if (noteSaves === 1) { await json(route, { detail: 'Synthetic note save failure' }, 400); return true }
        savedNotes = request.postDataJSON().notes; item.notes = savedNotes
      }
      await json(route, item); return true
    })
    await expect(page.getByRole('heading', { name: 'Fixture Alpha', exact: true })).toBeVisible()
    await page.getByTitle('编辑', { exact: true }).click()
    const draft = page.getByPlaceholder(/^用 Markdown 写下/)
    await draft.fill('User note draft survives refresh')
    const chatInput = page.getByPlaceholder('例如：第 3 节的推导为什么成立？')
    await chatInput.fill('Keep this chat input')
    const category = page.locator('article select')
    await category.selectOption('VLM')
    await page.locator('article header').getByRole('button', { name: '保存', exact: true }).click()
    await expect.poll(() => categorySaves).toBe(1)
    await expect(page.getByRole('heading', { name: 'External added note', exact: true })).toBeVisible()
    await expect(draft).toHaveValue('User note draft survives refresh')
    await expect(chatInput).toHaveValue('Keep this chat input')
    await page.getByRole('button', { name: '保存', exact: true }).last().click()
    await expect(page.getByText('Synthetic note save failure', { exact: true })).toBeVisible()
    await expect(draft).toHaveValue('User note draft survives refresh')
    await page.getByRole('button', { name: '保存', exact: true }).last().click()
    await expect.poll(() => savedNotes).toContain('User note draft survives refresh')
    assert.ok(savedNotes.includes('External content'), 'Saving the edit must preserve externally added blocks')
    await page.getByTitle('编辑', { exact: true }).first().click()
    await draft.fill('Discard this unsaved edit')
    await page.getByRole('button', { name: '取消', exact: true }).click()
    await expect(page.getByText('User note draft survives refresh', { exact: true })).toBeVisible()
    await page.getByTestId('review-paper-list').getByRole('button', { name: /Fixture Beta/ }).click()
    await expect(page.getByRole('heading', { name: 'Fixture Beta', exact: true })).toBeVisible()
    await expect(category).toHaveValue('VLM')
    await expect(page.getByText('Fixture Beta original content', { exact: true })).toBeVisible()
    await expect(page.getByText('User note draft survives refresh', { exact: true })).toHaveCount(0)
  })
  await test('Review paper switch ignores an older chat response', async () => {
    const pending = deferred()
    const papers = [paper(101, 'Fixture Alpha', 'LLM'), paper(102, 'Fixture Beta', 'VLM')]
    let sends = 0
    try {
      const page = await newPage('review', async (route, path) => {
        if (path === '/api/papers') { await json(route, papers); return true }
        if (path === '/api/paper-categories') { await json(route, { categories: [] }); return true }
        if (path === '/api/status') { await json(route, { running: false, current: null }); return true }
        if (path === '/api/papers/101/chat') { sends++; await pending.promise; await json(route, { ...chat, messages: [{ role: 'assistant', content: 'Stale Alpha answer', ts: '2026-01-01' }] }); return true }
        const match = path.match(/^\/api\/papers\/(101|102)(\/first_page)?$/)
        if (!match) return false
        if (match[2]) await route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>' })
        else await json(route, papers.find(item => item.id === Number(match[1])))
        return true
      })
      const input = page.getByPlaceholder('例如：第 3 节的推导为什么成立？')
      await input.fill('Synthetic delayed question')
      await input.press('Enter')
      await expect.poll(() => sends).toBe(1)
      await page.getByTestId('review-paper-list').getByRole('button', { name: /Fixture Beta/ }).click()
      await expect(page.getByRole('heading', { name: 'Fixture Beta', exact: true })).toBeVisible()
      const response = page.waitForResponse(r => r.url().endsWith('/101/chat'))
      pending.release()
      await response
      await settled(page)
      await expect(input).toHaveValue('')
      await expect(page.getByText('Stale Alpha answer', { exact: true })).toHaveCount(0)
      await expect(page.getByRole('heading', { name: 'Fixture Beta', exact: true })).toBeVisible()
    } finally { pending.release() }
  })
  await test('Rescue recall survives close/reopen and cannot be submitted twice', async () => {
    const pending = deferred()
    let loads = 0, recalls = 0
    const candidate = { id: 901, title: 'Synthetic rejected concept', node_type: 'concept', tags: [], source_paper_ids: [], promotion_status: 'rejected', promoted_by: 'llm', promotion_reason: 'Synthetic fixture', last_promotion_eval_at: null }
    try {
      const page = await newPage('rescue', async (route, path) => {
        if (path === '/api/promotion/candidates') { loads++; await json(route, { items: [candidate], counts: { pending: 0, rejected: 1, promoted: 0 } }); return true }
        if (path === '/api/promotion/901') { recalls++; await pending.promise; await json(route, { node: { ...candidate, promotion_status: 'pending' } }); return true }
        return false
      })
      const recall = page.getByRole('button', { name: '召回', exact: true })
      await recall.click()
      await expect.poll(() => recalls).toBe(1)
      // Existing modal close control is the first header's only button.
      await page.locator('header').first().getByRole('button').click()
      await page.getByRole('button', { name: 'Open test rescue' }).click()
      await expect.poll(() => loads).toBe(2)
      await expect(page.getByText(candidate.title, { exact: true })).toBeVisible()
      await expect(recall).toBeDisabled()
      const response = page.waitForResponse(r => r.url().endsWith('/promotion/901'))
      pending.release()
      await response
      await expect(page.getByText(candidate.title, { exact: true })).toHaveCount(0)
      await expect(page.getByTestId('recalled')).toHaveText('901')
      assert.equal(recalls, 1)
    } finally { pending.release() }
  })
} catch (error) {
  console.error(error)
  process.exitCode = 1
} finally {
  try { await close() }
  catch (error) { console.error('Lifecycle cleanup failed:', error); process.exitCode = 1 }
  // Keep only synthetic diagnostics/profile in this run's ignored snapshot.
  // context.close() and server.close() must both finish to report success.
  const result = { tests, requests, violations, cleanupConfirmed }
  await writeFile(join(root, 'lifecycle-results.json'), JSON.stringify(result, null, 2) + '\n')
  if (tests.length !== 5 || tests.some(test => test.status !== 'passed') || violations.length || !cleanupConfirmed) process.exitCode = 1
}
