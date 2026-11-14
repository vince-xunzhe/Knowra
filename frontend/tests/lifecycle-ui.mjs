/** Real React lifecycle regressions. Run only through scripts/check_frontend.py.
 * No backend starts: API responses and storage below are entirely synthetic.
 * Every unrecognised API, external URL, popup or browser error fails the check.
 */
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdir, readFile, realpath, stat, writeFile } from 'node:fs/promises'
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
const indexStatus = { exists: true, healthy: true, path: 'fixture/index.md', size: 1024, estimated_tokens: 240, read_latency_ms: 1, current_papers: 2, current_concepts: 0, stale: false }
const searchIndexStatus = { exists: true, healthy: true, total: 2, by_kind: { paper: 2, concept: 0 }, db_path: 'fixture/wiki_search.sqlite', db_size: 32768, indexed_characters: 1800, estimated_tokens: 520, stale: false, status_latency_ms: 2,
  source: { files: 2, by_kind: { paper: 2, concept: 0 }, bytes: 4096, characters: 1800, estimated_tokens: 520, token_estimate_method: 'fixture heuristic', unreadable_files: 0, latest_modified_ns: 0, measurement_ms: 1 } }
const evidenceStatus = { status: 'ready', mode: 'assist', ready: true, generation_id: 'fixture-generation-v1', reason: 'reviewed_generation_active' }
const chat = { messages: [], thread_created_at: null, expires_at: null, days_remaining: null, ttl_days: 30, ready: true }
const notes = blocks => '<!--notes-v2-->\n' + JSON.stringify(blocks)
function paper(id, title, category, learningStatus = 'not_started') {
  return { id, title, filename: `fixture-${id}.pdf`, filepath: `fixture/${id}.pdf`, authors: ['Synthetic Author'], num_pages: 1,
    processed: true, processed_at: null, paper_category: category, paper_category_model: category,
    paper_category_override: category, paper_category_source: 'manual', paper_team: null, paper_team_model: null,
    paper_team_override: null, paper_team_source: 'none', learning_status: learningStatus, year: 2026, error: null,
    created_at: null, extracted_text: null, raw_llm_response: JSON.stringify({ title, abstract_summary: 'Synthetic abstract' }),
    extraction: { title, abstract_summary: 'Synthetic abstract' }, extraction_model: 'fixture-only',
    extraction_coverage: { strategy: 'full-page-hierarchy-v1', model_input_coverage: 'all_extracted_text_chunks',
      source_pages: 18, attempted_pages: 18, ok_pages: 17, empty_pages: 1, failed_pages: 0,
      failed_page_numbers: [], page_iteration_complete: true, source_chunks: 42, map_batches: 7 },
    notes: notes([{ id: `note-${id}`, title: `${title} note`, content: `${title} original content` }]),
    has_first_page_image: true, chat: structuredClone(chat), knowledge_nodes: [] }
}
const json = (route, value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) })
const deferred = () => { let release; const promise = new Promise(done => { release = done }); return { promise, release } }
// Wait for response handlers and React's next painted state before asserting
// absence; an immediate negative assertion could pass before a stale callback.
const settled = page => page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))))

async function newPage(component, apiHandler, options = {}) {
  const page = await context.newPage()
  page.setDefaultTimeout(8000)
  page.on('pageerror', error => violations.push(String(error)))
  page.on('popup', popup => { violations.push('Unexpected popup'); void popup.close() })
  await page.addInitScript(seed => {
    localStorage.clear()
    localStorage.setItem('knowra.locale', 'zh')
    localStorage.setItem('knowra.theme', seed.theme)
    localStorage.setItem('knowra:ask-drawer:sessions', JSON.stringify(seed.sessions))
  }, { sessions, theme: options.theme ?? 'dark' })
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
      if (path === '/api/wiki/search/stats') { await json(route, searchIndexStatus); return true }
      if (path === '/api/wiki/ask/evidence-status') { await json(route, evidenceStatus); return true }
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
  await test('Ask renders exact page citations without exposing shadow candidates', async () => {
    let asks = 0
    const page = await newPage('ask', async (route, path) => {
      if (path === '/api/wiki/index/status') { await json(route, indexStatus); return true }
      if (path === '/api/wiki/search/stats') { await json(route, searchIndexStatus); return true }
      if (path === '/api/wiki/ask/evidence-status') { await json(route, evidenceStatus); return true }
      if (path === '/api/wiki/ask') {
        asks++
        await json(route, {
          answer: '## 合成回答\n\n动作成为潜在转移的显式输入。',
          cited_files: ['data/wiki/papers/0001-fixture.md'],
          citations: [{
            kind: 'paper',
            ref: 'data/wiki/papers/0001-fixture.md',
            path: 'data/wiki/papers/0001-fixture.md',
            filename: '0001-fixture.md',
            paper_id: 1,
            page_number: 7,
            section_kind: 'method',
            section_heading: '3. Method',
            quote: 'Actions enter the latent transition directly.',
            char_start: 120,
            char_end: 165,
            evidence_id: 'evidence:fixture',
            manifest_id: 'manifest:fixture',
            provenance_status: 'retrieved_source_text',
          }],
          trace: [{ step: 0, tool: 'shadow_retrieval', args: { mode: 'observe' }, result_summary: 'candidate-only', duration_ms: 2, hit_node_ids: [] }],
          model: 'fixture-model',
          session_title: '合成证据',
          session_id: 'fixture-a',
          duration_ms: 12,
          steps: 1,
        })
        return true
      }
      return false
    })
    const input = page.getByPlaceholder(/^问点什么/)
    await input.fill('How did action enter the model?')
    await page.getByRole('button', { name: '发送', exact: true }).click()
    await expect(page.getByText('Actions enter the latent transition directly.', { exact: true })).toBeVisible()
    await expect(page.getByText(/论文原文 · data\/wiki\/papers\/0001-fixture\.md/)).toBeVisible()
    await expect(page.getByText(/p\.7 · 3\. Method · chars 120-165/)).toBeVisible()
    await expect(page.getByText('candidate-only', { exact: false })).toHaveCount(0)
    assert.equal(asks, 1)
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
        if (path === '/api/wiki/search/stats') { await json(route, searchIndexStatus); return true }
        if (path === '/api/wiki/ask/evidence-status') { await json(route, evidenceStatus); return true }
        if (path === '/api/wiki/outputs/render') { exports++; await exportReply.promise; await json(route, { rel_path: 'fixture-old-export.md' }); return true }
        return false
      })
      await expect.poll(() => indexes).toBe(1)
      await page.getByTitle('关闭', { exact: true }).click()
      await page.getByRole('button', { name: 'Open test drawer' }).click()
      await expect(page.getByText(/index\.md.*77 论文/)).toBeVisible()
      const olderResponse = page.waitForResponse(response => response.url().endsWith('/wiki/index/status'))
      oldIndex.release()
      await olderResponse
      await settled(page)
      await expect(page.getByText(/index\.md.*77 论文/)).toBeVisible()
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
  await test('Ask separates index statuses, adopts POST snapshots and retains them on refresh failure', async () => {
    let refreshes = 0
    let searchStatusReads = 0
    const refreshedSearchIndexStatus = {
      ...searchIndexStatus,
      total: 3,
      by_kind: { paper: 2, concept: 1 },
      db_size: 40960,
      estimated_tokens: 680,
      source: {
        ...searchIndexStatus.source,
        files: 3,
        by_kind: { paper: 2, concept: 1 },
        bytes: 5120,
        estimated_tokens: 680,
      },
    }
    const page = await newPage('ask', async (route, path, request) => {
      if (path === '/api/wiki/index/status') { await json(route, indexStatus); return true }
      if (path === '/api/wiki/search/stats') { searchStatusReads++; await json(route, searchIndexStatus); return true }
      if (path === '/api/wiki/ask/evidence-status') { await json(route, evidenceStatus); return true }
      if (path === '/api/wiki/reindex' && request.method() === 'POST') {
        refreshes++
        if (refreshes === 1) {
          await json(route, { indexed: 3, paper: 2, concept: 1, db_size: 40960, duration_ms: 4, replaced_previous: true, summary: refreshedSearchIndexStatus })
          return true
        }
        await json(route, { detail: '全文搜索索引刷新失败；旧索引仍可用。 Synthetic fixture failure.' }, 500)
        return true
      }
      return false
    })
    const overview = page.getByTestId('wiki-overview-status')
    const search = page.getByTestId('wiki-search-status')
    const evidence = page.getByTestId('ask-evidence-status')
    await expect(overview).toContainText('Wiki 总览')
    await expect(overview).toContainText('index.md · 1.0 KB · ~240 tokens · 1 ms')
    await expect(search).toContainText('全文搜索')
    await expect(search).toContainText('FTS5 · 2 篇文档 · 32.0 KB · ~520 tokens · 2 ms')
    await expect(evidence).toContainText('原文证据')
    await expect(evidence).toContainText('已启用 · fixture-generation-v1')
    await expect(page.getByTestId('wiki-index-statuses')).toContainText('Wiki 目录 · 2 个文件 · 4.0 KB · ~520 tokens · 1 ms')

    const evidenceDir = process.env.KNOWRA_WIKI_INDEX_EVIDENCE_DIR
    if (evidenceDir) {
      await page.getByTestId('wiki-index-statuses').screenshot({
        path: join(evidenceDir, 'after-index-statuses.png'),
        animations: 'disabled',
      })
    }

    await page.getByRole('button', { name: '刷新全文索引', exact: true }).click()
    await expect.poll(() => refreshes).toBe(1)
    await expect(page.getByText('全文搜索索引刷新完成', { exact: true })).toBeVisible()
    await expect(search).toContainText('FTS5 · 3 篇文档 · 40.0 KB · ~680 tokens')
    assert.equal(searchStatusReads, 1, 'Successful POST summary must avoid a second status GET')

    await page.getByRole('button', { name: '刷新全文索引', exact: true }).click()
    await expect.poll(() => refreshes).toBe(2)
    await expect(page.getByText('全文搜索索引刷新失败', { exact: true })).toBeVisible()
    await expect(page.getByText(/旧索引仍可用/)).toBeVisible()
    await expect(search).toContainText('FTS5 · 3 篇文档 · 40.0 KB')
    assert.equal(refreshes, 2, 'Each click must issue exactly one full-text refresh')
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
    await expect(page.getByText('Synthetic abstract', { exact: true })).toBeVisible()
    const extractDetails = page.getByTestId('review-extract-details-toggle')
    await expect(extractDetails).toBeVisible()
    await expect(extractDetails).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByText('原文覆盖', { exact: true })).toHaveCount(0)
    await extractDetails.click()
    await expect(extractDetails).toHaveAttribute('aria-expanded', 'true')
    await expect(page.getByText('原文覆盖', { exact: true })).toBeVisible()
    await expect(page.getByText(/已遍历全部 18 页；42 个可抽取文本块分 7 批/)).toBeVisible()
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

  await test('Review hides extraction details until expanded and resets on paper switch', async () => {
    const alpha = paper(101, 'Fixture Alpha', 'LLM')
    alpha.extraction = { title: 'Fixture Alpha', abstract_summary: 'Synthetic abstract', core_contribution: 'Visible core contribution' }
    alpha.raw_llm_response = JSON.stringify(alpha.extraction)
    alpha.source_evidence = {
      manifest_id: 'fix-101', source_pages: 18, attempted_pages: 18, ok_pages: 17, empty_pages: 1,
      failed_pages: 0, source_characters: 1000, source_chunks: 42, page_iteration_complete: true, visual_coverage: 'unsupported',
    }
    const beta = paper(102, 'Fixture Beta', 'VLM')
    beta.extraction_coverage = {
      ...beta.extraction_coverage,
      page_iteration_complete: false, attempted_pages: 10, failed_pages: 2, failed_page_numbers: [4, 9], ok_pages: 8,
    }
    const papers = [alpha, beta]
    const page = await newPage('review', async (route, path) => {
      if (path === '/api/papers') { await json(route, papers); return true }
      if (path === '/api/paper-categories') { await json(route, { categories: [] }); return true }
      if (path === '/api/status') { await json(route, { running: false, current: null }); return true }
      if (path === '/api/papers/101/source-evidence') {
        await json(route, {
          manifest_id: 'fix-101', paper_id: '101', source_sha256: 'fixture', source_pages: 18,
          page: {
            page_number: 1, status: 'ok', text_characters: 19, parser_used: 'fixture', error_type: null,
            chunks: [{ evidence_id: 'e1', page_number: 1, page_char_start: 0, page_char_end: 19, section_heading: 'Intro', section_kind: 'body', text: 'Fixture source text' }],
          },
        })
        return true
      }
      const match = path.match(/^\/api\/papers\/(101|102)(\/first_page)?$/)
      if (!match) return false
      if (match[2]) await route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>' })
      else await json(route, papers.find(item => item.id === Number(match[1])))
      return true
    })
    await expect(page.getByRole('heading', { name: 'Fixture Alpha', exact: true })).toBeVisible()
    await expect(page.getByText('Visible core contribution', { exact: true })).toBeVisible()
    await expect(page.getByText('Synthetic abstract', { exact: true })).toBeVisible()
    const toggle = page.getByTestId('review-extract-details-toggle')
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByTestId('review-extract-details-panel')).toHaveCount(0)
    await expect(page.getByText('原文覆盖', { exact: true })).toHaveCount(0)
    await expect(page.getByText('原文对照', { exact: true })).toHaveCount(0)
    await expect(page.getByText('逐页核对原文', { exact: true })).toHaveCount(0)
    await expect(page.getByTestId('review-extract-details-hint')).toHaveCount(0)
    await toggle.click()
    const panel = page.getByTestId('review-extract-details-panel')
    await expect(panel).toBeVisible()
    await expect(panel.getByText(/处理阶段：/)).toBeVisible()
    await expect(panel.getByText('原文覆盖', { exact: true })).toBeVisible()
    await expect(panel.getByText('原文对照', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '逐页核对原文' })).toBeVisible()
    await page.getByRole('button', { name: '逐页核对原文' }).click()
    await expect(page.getByText('Fixture source text', { exact: true })).toBeVisible()
    await expect(page.getByText('Visible core contribution', { exact: true })).toBeVisible()
    await toggle.click()
    await expect(page.getByTestId('review-extract-details-panel')).toHaveCount(0)
    await expect(page.getByText('Fixture source text', { exact: true })).toHaveCount(0)
    await toggle.click()
    await expect(page.getByTestId('review-extract-details-panel')).toBeVisible()
    await page.getByTestId('review-paper-list').getByRole('button', { name: /Fixture Beta/ }).click()
    await expect(page.getByRole('heading', { name: 'Fixture Beta', exact: true })).toBeVisible()
    await expect(page.getByTestId('review-extract-details-toggle')).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByTestId('review-extract-details-panel')).toHaveCount(0)
    await expect(page.getByTestId('review-extract-details-hint')).toContainText('原文覆盖不完整')
    await expect(page.getByTestId('review-extract-details-hint')).toContainText('失败页 4, 9')
    await expect(page.getByText('Synthetic abstract', { exact: true })).toBeVisible()
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
  await test('Review titles preserve status contrast in both themes, selections, groups and zoom levels', async () => {
    const papers = [
      paper(101, 'Synthetic Active Completed', 'World models', 'completed'),
      paper(102, 'Synthetic Learning Paper', 'World models', 'learning'),
      paper(103, 'Synthetic Completed Paper', 'World models', 'completed'),
      paper(104, 'Synthetic Not Started Paper', 'World models', 'not_started'),
    ]
    const page = await newPage('review', async (route, path) => {
      if (path === '/api/papers') { await json(route, papers); return true }
      if (path === '/api/paper-categories') { await json(route, { categories: [{ name: 'World models', builtin: false, removable: true, count: papers.length }] }); return true }
      if (path === '/api/status') { await json(route, { running: false, current: null }); return true }
      const match = path.match(/^\/api\/papers\/(101|102|103|104)(\/first_page)?$/)
      if (!match) return false
      if (match[2]) await route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>' })
      else await json(route, papers.find(item => item.id === Number(match[1])))
      return true
    }, { theme: 'light' })
    const list = page.getByTestId('review-paper-list')
    const title = name => list.getByText(name, { exact: true })
    const metrics = async locator => locator.evaluate(element => {
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = 1
      const context = canvas.getContext('2d', { willReadFrequently: true })
      const parse = value => {
        context.clearRect(0, 0, 1, 1)
        context.fillStyle = value
        context.fillRect(0, 0, 1, 1)
        const [red, green, blue, alpha] = context.getImageData(0, 0, 1, 1).data
        return { rgb: [red, green, blue], alpha: alpha / 255 }
      }
      const over = (front, back) => front.rgb.map((value, index) => value * front.alpha + back[index] * (1 - front.alpha))
      const ancestors = []
      for (let node = element; node; node = node.parentElement) ancestors.push(node)
      let background = [255, 255, 255]
      for (const node of ancestors.reverse()) background = over(parse(getComputedStyle(node).backgroundColor), background)
      const foreground = parse(getComputedStyle(element).color).rgb
      const luminance = rgb => {
        const linear = rgb.map(channel => {
          const value = channel / 255
          return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
        })
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
      }
      const lighter = Math.max(luminance(foreground), luminance(background))
      const darker = Math.min(luminance(foreground), luminance(background))
      return { color: getComputedStyle(element).color, background, contrast: (lighter + 0.05) / (darker + 0.05) }
    })
    const names = ['Synthetic Active Completed', 'Synthetic Learning Paper', 'Synthetic Completed Paper', 'Synthetic Not Started Paper']
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
    await page.evaluate(() => { document.documentElement.style.zoom = '125%' })
    for (const name of names) await expect(title(name)).toBeVisible()
    const light = Object.fromEntries(await Promise.all(names.map(async name => [name, await metrics(title(name))])))
    const evidenceDir = process.env.KNOWRA_REVIEW_EVIDENCE_DIR
    const evidenceName = process.env.KNOWRA_REVIEW_EVIDENCE_NAME
    if (evidenceDir && evidenceName) {
      await page.screenshot({ path: join(evidenceDir, `${evidenceName}.png`), animations: 'disabled' })
      await writeFile(join(evidenceDir, `${evidenceName}-metrics.json`), JSON.stringify({ theme: 'light', zoom: 1.25, metrics: light }, null, 2) + '\n')
    }
    for (const result of Object.values(light)) assert.ok(result.contrast >= 4.5, `Light title contrast ${result.contrast.toFixed(2)} must be at least 4.5`)
    await page.getByRole('button', { name: '按团队分组', exact: true }).click()
    for (const name of names) await expect(title(name)).toBeVisible()
    await page.evaluate(() => { document.documentElement.style.zoom = '80%' })
    await title('Synthetic Learning Paper').click()
    await expect(page.getByRole('heading', { name: 'Synthetic Learning Paper', exact: true })).toBeVisible()
    assert.ok((await metrics(title('Synthetic Learning Paper'))).contrast >= 4.5)
    await page.evaluate(() => {
      localStorage.setItem('knowra.theme', 'dark')
      window.dispatchEvent(new StorageEvent('storage', { key: 'knowra.theme', newValue: 'dark' }))
    })
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
    const dark = await Promise.all(names.map(name => metrics(title(name))))
    for (const result of dark) assert.ok(result.contrast >= 4.5, `Dark title contrast ${result.contrast.toFixed(2)} must be at least 4.5`)
  })

  await test('Background tasks panel closes on outside press and keeps inner actions', async () => {
    const jobs = [
      { job_id: 'compile-1', kind: 'compile', channel: 'compile', status: 'failed', progress: { label: 'Wiki 编译失败' }, error: 'Synthetic compile failure', result: null, worker_online: true },
      { job_id: 'scan-1', kind: 'scan', channel: 'scan', status: 'completed', progress: { label: '扫描完成' }, error: null, result: null, worker_online: true },
    ]
    let resumes = 0
    const page = await newPage('tasks', async (route, path, request) => {
      if (path === '/api/jobs') { await json(route, jobs); return true }
      if (path === '/api/jobs/compile-1/resume' && request.method() === 'POST') {
        resumes++
        jobs[0] = { ...jobs[0], status: 'queued', error: null, progress: { label: '已恢复' } }
        await json(route, jobs[0])
        return true
      }
      return false
    })
    const toggle = page.getByRole('button', { name: /后台任务/ })
    const panel = page.getByTestId('background-tasks-panel')
    const outside = page.getByTestId('tasks-outside')
    await expect(page.getByTestId('background-tasks')).toBeVisible()
    await expect(panel).toHaveCount(0)
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')

    await toggle.click()
    await expect(panel).toBeVisible()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await panel.getByText('Wiki 编译失败', { exact: true }).click()
    await expect(panel).toBeVisible()
    await panel.locator('div.max-h-72').evaluate(element => { element.scrollTop = 24 })
    await expect(panel).toBeVisible()
    await panel.getByRole('button', { name: '从检查点恢复' }).click()
    await expect.poll(() => resumes).toBe(1)
    await expect(panel).toBeVisible()
    await expect(panel.getByText('已恢复', { exact: true })).toBeVisible()

    await toggle.click()
    await expect(panel).toHaveCount(0)
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')

    await toggle.click()
    await expect(panel).toBeVisible()
    await outside.click()
    await expect(panel).toHaveCount(0)

    for (let index = 0; index < 5; index++) {
      await toggle.click()
      await expect(panel).toBeVisible()
      await toggle.click()
      await expect(panel).toHaveCount(0)
    }

    await toggle.click()
    await expect(panel).toBeVisible()
    await outside.dispatchEvent('pointerdown', { pointerType: 'touch', bubbles: true, cancelable: true, composed: true })
    await expect(panel).toHaveCount(0)

    await toggle.click()
    await expect(panel).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(panel).toHaveCount(0)

    await toggle.click()
    await expect(panel).toBeVisible()
    await outside.click()
    await expect(panel).toHaveCount(0)
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })


  await test('Library cards show first page, process actions and full categories', async () => {
    const tinyPng = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64')
    const openaiRoute = {
      provider_id: 'openai', provider_type: 'openai', provider_label: 'OpenAI',
      model_id: 'openai/gpt-4o', model_label: 'GPT-4o', upstream_model: 'gpt-4o',
      runtime_model_name: 'gpt-4o', surface: 'openai_file_search', file_search_api: 'assistants',
      requires_file_search_confirmation: true, supported: true, unsupported_reason: null,
      route_fingerprint: 'fp-openai-fixture', has_custom_base_url: false,
      pricing_url: '/tests/lifecycle-harness.html', file_search_guide_url: '/tests/lifecycle-harness.html',
    }
    const codexRoute = {
      ...openaiRoute,
      provider_id: 'codex-cli', provider_type: 'codex_cli', provider_label: 'Codex',
      model_id: 'codex-cli/gpt-6-astra', model_label: 'Codex', surface: 'codex_cli_hierarchy',
      file_search_api: null, requires_file_search_confirmation: false,
      route_fingerprint: 'fp-codex-fixture',
    }
    const longCategory = '三维重建-静态'
    const englishCategory = 'Foundation-Model Evaluation Benchmarks'
    const records = [
      {
        id: 11, filename: '2604.18484v2(1).pdf', filepath: 'fixture/unprocessed.pdf',
        title: 'Pending library fixture：开放世界具身智能中的长程规划与多模态对齐研究以及额外补充使标题超过三行 FoundationModelEvaluationWithoutAnySpacesToForceWrap', authors: ['Synthetic Author'], num_pages: 48,
        processed: false, processed_at: null, processing_status: 'scanning', error: null,
        paper_category: null, paper_category_model: longCategory, paper_category_override: null,
        paper_category_source: 'model', paper_team: null, paper_team_model: null, paper_team_override: null,
        paper_team_source: 'none', learning_status: 'not_started', year: 2026, created_at: '2026-01-01',
        has_first_page_image: false,
      },
      {
        id: 12, filename: 'failed.pdf', filepath: 'fixture/failed.pdf',
        title: 'Failed library fixture', authors: [], num_pages: 2,
        processed: false, processed_at: null, processing_status: 'failed', error: 'Synthetic extract failure',
        paper_category: null, paper_category_model: null, paper_category_override: englishCategory,
        paper_category_source: 'manual', paper_team: null, paper_team_model: null, paper_team_override: null,
        paper_team_source: 'none', learning_status: 'not_started', year: 2026, created_at: '2026-01-01',
        has_first_page_image: false,
      },
      {
        id: 13, filename: 'done.pdf', filepath: 'fixture/done.pdf',
        title: 'Completed library fixture', authors: ['Done Author'], num_pages: 6,
        processed: true, processed_at: '2026-01-02T00:00:00', processing_status: 'completed', error: null,
        paper_category: 'LLM', paper_category_model: 'LLM', paper_category_override: 'LLM',
        paper_category_source: 'manual', paper_team: null, paper_team_model: null, paper_team_override: null,
        paper_team_source: 'none', learning_status: 'not_started', year: 2026, created_at: '2026-01-01',
        has_first_page_image: true,
      },
      {
        id: 14, filename: 'missing-cover.pdf', filepath: 'fixture/missing-cover.pdf',
        title: 'Cover-missing library fixture', authors: [], num_pages: 1,
        processed: false, processed_at: null, processing_status: 'scanning', error: null,
        paper_category: null, paper_category_model: '未设置', paper_category_override: null,
        paper_category_source: 'none', paper_team: null, paper_team_model: null, paper_team_override: null,
        paper_team_source: 'none', learning_status: 'not_started', year: 2026, created_at: '2026-01-01',
        has_first_page_image: false,
      },
    ]
    const processPosts = []
    const categoryPuts = []
    let extractRoute = openaiRoute
    const firstPageHits = []
    const page = await newPage('library', async (route, path, request) => {
      if (path === '/api/papers' && request.method() === 'GET') { await json(route, records); return true }
      if (path === '/api/paper-categories') { await json(route, { categories: ['LLM', 'VLM', 'VLA', '三维重建-静态', '三维重建-动态', '世界模型', '其他'].map(name => ({ name, builtin: true, removable: false, count: 1 })) }); return true }
      if (path === '/api/status') {
        await json(route, { running: false, total: 0, done: 0, errors: 0, current: '' })
        return true
      }
      if (path === '/api/papers/extract-route') { await json(route, extractRoute); return true }
      const firstPage = path.match(/^\/api\/papers\/(\d+)\/first_page$/)
      if (firstPage) {
        firstPageHits.push(firstPage[1])
        const id = Number(firstPage[1])
        if (id === 14) { await route.fulfill({ status: 404, body: 'missing' }); return true }
        await route.fulfill({ status: 200, contentType: 'image/png', body: tinyPng })
        const row = records.find(item => item.id === id)
        if (row) row.has_first_page_image = true
        return true
      }
      const category = path.match(/^\/api\/papers\/(\d+)\/category$/)
      if (category && request.method() === 'PUT') {
        const id = Number(category[1])
        const body = JSON.parse(request.postData() || '{}')
        const row = records.find(item => item.id === id)
        categoryPuts.push({ id, category: body.category ?? null })
        if (row) {
          if (body.category == null) {
            row.paper_category_override = null
            row.paper_category_source = row.paper_category_model ? 'model' : 'none'
            row.paper_category = row.paper_category_model
          } else {
            row.paper_category_override = body.category
            row.paper_category_source = 'manual'
            row.paper_category = body.category
          }
        }
        await json(route, row || { id, ...body })
        return true
      }
      const process = path.match(/^\/api\/papers\/(\d+)\/(process|retry)$/)
      if (process && request.method() === 'POST') {
        processPosts.push({ id: Number(process[1]), action: process[2], body: request.postData() || '' })
        const row = records.find(item => item.id === Number(process[1]))
        if (row) row.processing_status = 'queued'
        await json(route, { running: true, total: 1, done: 0, errors: 0, current: row?.filename || '' })
        return true
      }
      return false
    })

    const pendingCard = page.getByTestId('paper-grid-card').filter({ hasText: 'Pending library fixture' })
    const failedCard = page.getByTestId('paper-grid-card').filter({ hasText: 'Failed library fixture' })
    const doneCard = page.getByTestId('paper-grid-card').filter({ hasText: 'Completed library fixture' })
    const missingCard = page.getByTestId('paper-grid-card').filter({ hasText: 'Cover-missing library fixture' })

    await expect(pendingCard.getByTestId('paper-cover-image')).toBeVisible()
    await expect(failedCard.getByTestId('paper-cover-image')).toBeVisible()
    await expect(doneCard.getByTestId('paper-cover-image')).toBeVisible()
    await expect(missingCard.getByTestId('paper-cover-fallback')).toBeVisible()
    assert.ok(firstPageHits.includes('11'), 'unprocessed papers must request a first-page image')
    assert.ok(firstPageHits.includes('13'), 'processed papers still request a first-page image')

    await expect(pendingCard.getByTestId('paper-card-process')).toHaveText(/立即处理/)
    await expect(failedCard.getByTestId('paper-card-process')).toHaveText(/重试处理/)
    await expect(doneCard.getByTestId('paper-card-process')).toHaveCount(0)

    const longTitle = records[0].title
    const pendingTitle = pendingCard.getByTestId('paper-card-title')
    const shortTitle = doneCard.getByTestId('paper-card-title')
    await expect(pendingTitle).toHaveAttribute('title', longTitle)
    await expect(pendingTitle).toHaveAttribute('aria-label', longTitle)
    await expect(pendingTitle).toContainText('Pending library fixture')
    await expect(shortTitle).toHaveAttribute('title', 'Completed library fixture')
    const titleMetrics = await pendingTitle.evaluate(node => {
      const style = getComputedStyle(node)
      const lineHeight = parseFloat(style.lineHeight)
      return {
        webkitLineClamp: style.webkitLineClamp,
        overflow: style.overflow,
        clientHeight: node.clientHeight,
        scrollHeight: node.scrollHeight,
        lineHeight,
        maxLines: lineHeight ? node.clientHeight / lineHeight : 0,
      }
    })
    assert.equal(titleMetrics.webkitLineClamp, '3', `expected 3-line clamp, got ${JSON.stringify(titleMetrics)}`)
    assert.ok(titleMetrics.scrollHeight > titleMetrics.clientHeight + 1, `long title should ellipsis, got ${JSON.stringify(titleMetrics)}`)
    assert.ok(titleMetrics.maxLines <= 3.2, `title block taller than 3 lines: ${JSON.stringify(titleMetrics)}`)
    const shortMetrics = await shortTitle.evaluate(node => ({ clientHeight: node.clientHeight, scrollHeight: node.scrollHeight }))
    assert.ok(shortMetrics.scrollHeight <= shortMetrics.clientHeight + 1, `short title should not clip, got ${JSON.stringify(shortMetrics)}`)
    const titleBottom = await pendingTitle.evaluate(node => node.getBoundingClientRect().bottom)
    const authorsTop = await pendingCard.locator('p', { hasText: 'Synthetic Author' }).evaluate(node => node.getBoundingClientRect().top)
    assert.ok(authorsTop >= titleBottom - 0.5, `title overlaps authors: titleBottom=${titleBottom} authorsTop=${authorsTop}`)

    const pendingCategory = pendingCard.getByTestId('paper-card-category-label')
    const failedCategory = failedCard.getByTestId('paper-card-category-label')
    await expect(pendingCategory).toHaveText(new RegExp(`跟随 ·\\s*${longCategory}`))
    await expect(failedCategory).toHaveText(englishCategory)
    await expect(pendingCard.locator('select')).toHaveCount(0)
    await expect(failedCard.locator('select')).toHaveCount(0)
    const inheritCopies = await pendingCard.evaluate((card, phrase) => {
      const visible = card.innerText.replace(/\s+/g, ' ')
      return visible.split(phrase).length - 1
    }, `跟随 · ${longCategory}`)
    assert.equal(inheritCopies, 1, 'inherited category text must appear once, not as a label plus a second control')
    const categoryFont = await pendingCategory.evaluate(node => parseFloat(getComputedStyle(node).fontSize))
    assert.ok(categoryFont <= 12.5, `category font should match card meta, got ${categoryFont}px`)

    const noOverflow = async (locator, label) => {
      const box = await locator.evaluate(node => {
        const card = node.closest('[data-testid="paper-grid-card"], [data-testid="paper-list-row"]')
        if (!card) return { ok: false, reason: 'no card' }
        const cardBox = card.getBoundingClientRect()
        const nodeBox = node.getBoundingClientRect()
        return {
          ok: node.scrollWidth <= node.clientWidth + 1 && nodeBox.right <= cardBox.right + 1 && card.scrollWidth <= card.clientWidth + 1,
          scrollWidth: node.scrollWidth, clientWidth: node.clientWidth, cardScroll: card.scrollWidth, cardClient: card.clientWidth,
        }
      })
      assert.ok(box.ok, `${label} overflow ${JSON.stringify(box)}`)
    }
    await noOverflow(pendingTitle, 'long title')
    await noOverflow(pendingCategory, 'long Chinese category')
    await noOverflow(failedCategory, 'long English category')

    const shotDir = process.env.KNOWRA_LIBRARY_CARD_EVIDENCE_DIR || join(root, 'tmp', 'library-card-visual')
    await mkdir(shotDir, { recursive: true })
    await pendingCard.screenshot({ path: join(shotDir, 'grid-long-title-compact-category.png'), animations: 'disabled' })


    await pendingCard.getByTestId('paper-card-process').click()
    await expect(page.getByRole('dialog', { name: /将通过 OpenAI API 进行论文抽取/ })).toBeVisible()
    await page.getByRole('dialog').locator('footer').getByRole('button', { name: '取消' }).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    assert.equal(processPosts.length, 0)
    await expect(page.getByTestId('opened-review')).toHaveText('')

    await pendingCard.getByTestId('paper-card-process').click()
    await page.getByRole('button', { name: '继续抽取' }).click()
    await expect.poll(() => processPosts.length).toBe(1)
    assert.equal(processPosts[0].id, 11)
    assert.equal(processPosts[0].action, 'process')
    assert.match(processPosts[0].body, /fp-openai-fixture/)
    await expect(page.getByTestId('opened-review')).toHaveText('')

    extractRoute = codexRoute
    await failedCard.getByTestId('paper-card-process').click()
    await expect.poll(() => processPosts.length).toBe(2)
    assert.equal(processPosts[1].id, 12)
    assert.equal(processPosts[1].action, 'retry')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(page.getByTestId('opened-review')).toHaveText('')

    await pendingCategory.click()
    const menu = page.getByTestId('paper-card-category-menu')
    await expect(menu).toBeVisible()
    await expect(menu.getByRole('option', { name: `跟随 · ${longCategory}` })).toBeVisible()
    await menu.getByRole('option', { name: 'LLM', exact: true }).click()
    await expect.poll(() => categoryPuts.length).toBe(1)
    assert.equal(categoryPuts[0].id, 11)
    assert.equal(categoryPuts[0].category, 'LLM')
    await expect(menu).toHaveCount(0)
    await expect(pendingCategory).toHaveText('LLM')
    await expect(pendingCategory).not.toHaveText(/跟随/)
    await expect(page.getByTestId('opened-review')).toHaveText('')
    await pendingCategory.click()
    await page.getByTestId('paper-card-category-menu').getByRole('option', { name: `跟随 · ${longCategory}` }).click()
    await expect.poll(() => categoryPuts.length).toBe(2)
    assert.equal(categoryPuts[1].category, null)
    await expect(pendingCategory).toHaveText(new RegExp(`跟随 ·\\s*${longCategory}`))
    await pendingCard.screenshot({ path: join(shotDir, 'grid-category-inherited.png'), animations: 'disabled' })

    await page.setViewportSize({ width: 390, height: 800 })
    await expect(pendingTitle).toHaveAttribute('title', longTitle)
    await expect(pendingCategory).toContainText(longCategory)
    await noOverflow(pendingTitle, 'narrow long title')
    await noOverflow(pendingCategory, 'narrow long Chinese category')
    await noOverflow(failedCategory, 'narrow long English category')
    // A 390x800 window can clip a tall cover card during element screenshots
    // because the app root is viewport-sized with overflow hidden. Use a taller
    // frame only for the full-card evidence image; overflow was already checked at 800.
    await page.setViewportSize({ width: 390, height: 1400 })
    await pendingCard.scrollIntoViewIfNeeded()
    await pendingCard.screenshot({ path: join(shotDir, 'grid-narrow-390.png'), animations: 'disabled' })
    await page.setViewportSize({ width: 390, height: 800 })

    await page.getByTitle('列表视图').click()
    await expect(page.getByTestId('paper-list-row')).toHaveCount(4)
    const pendingRow = page.getByTestId('paper-list-row').filter({ hasText: 'Pending library fixture' })
    const doneRow = page.getByTestId('paper-list-row').filter({ hasText: 'Completed library fixture' })
    await expect(pendingRow.getByTestId('paper-card-process')).toBeVisible()
    await expect(doneRow.getByTestId('paper-card-process')).toHaveCount(0)
    await expect(pendingRow.getByTestId('paper-card-category-label')).toContainText(longCategory)
    await expect(pendingRow.getByTestId('paper-card-title')).toHaveAttribute('title', longTitle)
    const listTitleMetrics = await pendingRow.getByTestId('paper-card-title').evaluate(node => ({
      webkitLineClamp: getComputedStyle(node).webkitLineClamp,
      clientHeight: node.clientHeight,
      scrollHeight: node.scrollHeight,
      lineHeight: parseFloat(getComputedStyle(node).lineHeight),
    }))
    assert.equal(listTitleMetrics.webkitLineClamp, '2')
    assert.ok(listTitleMetrics.scrollHeight > listTitleMetrics.clientHeight + 1, `list title should ellipsis, got ${JSON.stringify(listTitleMetrics)}`)
    await noOverflow(pendingRow.getByTestId('paper-card-title'), 'list long title')
    await noOverflow(pendingRow.getByTestId('paper-card-category-label'), 'list long category')
    await pendingRow.screenshot({ path: join(shotDir, 'list-long-title-compact-category.png'), animations: 'disabled' })
  })

  await test('Library cards delete a paper after confirmation and leave cancel as a no-op', async () => {
    const tinyPng = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64')
    const records = [
      {
        id: 21, filename: 'keep.pdf', filepath: 'fixture/keep.pdf',
        title: 'Kept library fixture', authors: ['Keep Author'], num_pages: 3,
        processed: true, processed_at: '2026-01-02T00:00:00', processing_status: 'completed', error: null,
        paper_category: 'LLM', paper_category_model: 'LLM', paper_category_override: 'LLM',
        paper_category_source: 'manual', paper_team: null, paper_team_model: null, paper_team_override: null,
        paper_team_source: 'none', learning_status: 'not_started', year: 2026, created_at: '2026-01-01',
        has_first_page_image: true,
      },
      {
        id: 22, filename: 'remove.pdf', filepath: 'fixture/remove.pdf',
        title: 'Delete-me library fixture', authors: ['Gone Author'], num_pages: 8,
        processed: false, processed_at: null, processing_status: 'scanning', error: null,
        paper_category: null, paper_category_model: 'VLM', paper_category_override: null,
        paper_category_source: 'model', paper_team: null, paper_team_model: null, paper_team_override: null,
        paper_team_source: 'none', learning_status: 'not_started', year: 2026, created_at: '2026-01-01',
        has_first_page_image: true,
      },
    ]
    const deletes = []
    const processPosts = []
    const page = await newPage('library', async (route, path, request) => {
      if (path === '/api/papers' && request.method() === 'GET') { await json(route, records); return true }
      if (path === '/api/paper-categories') { await json(route, { categories: ['LLM', 'VLM'].map(name => ({ name, builtin: true, removable: false, count: 1 })) }); return true }
      if (path === '/api/status') { await json(route, { running: false, total: 0, done: 0, errors: 0, current: '' }); return true }
      if (path === '/api/papers/extract-route') {
        await json(route, {
          provider_id: 'codex-cli', provider_type: 'codex_cli', provider_label: 'Codex',
          model_id: 'codex-cli/gpt-6-astra', model_label: 'Codex', surface: 'codex_cli_hierarchy',
          requires_file_search_confirmation: false, supported: true, unsupported_reason: null,
          route_fingerprint: 'fp-codex-fixture', has_custom_base_url: false,
          pricing_url: '/tests/lifecycle-harness.html', file_search_guide_url: '/tests/lifecycle-harness.html',
          file_search_api: null,
        })
        return true
      }
      const firstPage = path.match(/^\/api\/papers\/(\d+)\/first_page$/)
      if (firstPage) {
        await route.fulfill({ status: 200, contentType: 'image/png', body: tinyPng })
        return true
      }
      const deleted = path.match(/^\/api\/papers\/(\d+)$/)
      if (deleted && request.method() === 'DELETE') {
        const id = Number(deleted[1])
        deletes.push(id)
        const index = records.findIndex(item => item.id === id)
        const row = index >= 0 ? records.splice(index, 1)[0] : null
        await json(route, { status: 'deleted', paper_id: String(id), title: row?.title || null, already_deleted: false })
        return true
      }
      const process = path.match(/^\/api\/papers\/(\d+)\/(process|retry)$/)
      if (process && request.method() === 'POST') {
        processPosts.push({ id: Number(process[1]), action: process[2] })
        await json(route, { running: true, total: 1, done: 0, errors: 0, current: '' })
        return true
      }
      return false
    })

    const target = page.getByTestId('paper-grid-card').filter({ hasText: 'Delete-me library fixture' })
    const kept = page.getByTestId('paper-grid-card').filter({ hasText: 'Kept library fixture' })
    await expect(target).toBeVisible()
    await expect(kept).toBeVisible()
    await target.getByTestId('paper-card-actions-trigger').click()
    await expect(page.getByTestId('paper-card-actions-menu')).toBeVisible()
    await page.getByTestId('paper-card-delete').click()
    const dialog = page.getByTestId('paper-delete-dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText('Delete-me library fixture')
    await expect(dialog).toContainText('不可恢复')
    await expect(dialog).toContainText('原始 PDF')
    await page.getByTestId('paper-delete-cancel').click()
    await expect(dialog).toHaveCount(0)
    assert.equal(deletes.length, 0)
    await expect(target).toBeVisible()
    await expect(page.getByTestId('opened-review')).toHaveText('')

    await target.click()
    await expect(page.getByTestId('paper-detail-delete')).toBeVisible()
    await expect(page.getByTestId('opened-review')).toHaveText('')
    await page.getByTestId('paper-detail-delete').click()
    await expect(page.getByTestId('paper-delete-dialog')).toBeVisible()
    await expect(page.getByTestId('paper-delete-confirm')).toBeEnabled()
    await page.getByTestId('paper-delete-confirm').click()
    await expect.poll(() => deletes.length).toBe(1)
    assert.equal(deletes[0], 22)
    await expect(page.getByTestId('paper-delete-dialog')).toHaveCount(0)
    await expect(page.getByTestId('paper-grid-card').filter({ hasText: 'Delete-me library fixture' })).toHaveCount(0)
    await expect(page.getByTestId('paper-detail-delete')).toHaveCount(0)
    const deleteSuccess = page.getByTestId('paper-delete-success-notice')
    await expect(deleteSuccess).toBeVisible()
    await expect(deleteSuccess).toContainText('已永久删除')
    await expect(deleteSuccess).toContainText('Delete-me library fixture')
    await expect(kept).toBeVisible()
    await expect(page.getByTestId('opened-review')).toHaveText('')

    await kept.click()
    await expect(page.getByTestId('opened-review')).toHaveText('21')

    await page.getByTitle('列表视图').click()
    const keptRow = page.getByTestId('paper-list-row').filter({ hasText: 'Kept library fixture' })
    await expect(keptRow).toBeVisible()
    await expect(page.getByTestId('paper-list-row').filter({ hasText: 'Delete-me library fixture' })).toHaveCount(0)
    await keptRow.getByTestId('paper-card-actions-trigger').click()
    await expect(page.getByTestId('paper-card-actions-menu')).toBeVisible()
    await expect(page.getByTestId('opened-review')).toHaveText('21')
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
  if (tests.length !== 12 || tests.some(test => test.status !== 'passed') || violations.length || !cleanupConfirmed) process.exitCode = 1
}
