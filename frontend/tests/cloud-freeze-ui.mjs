/** Isolated real-component acceptance. All API traffic is mocked; no backend.
 * Run only through scripts/check_cloud_freeze.py, never against a running app.
 */
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { createHash } from 'node:crypto'
import { readFile, realpath, stat, writeFile, mkdir } from 'node:fs/promises'
import { join, resolve, extname, sep } from 'node:path'
import { chromium, expect } from '@playwright/test'

const root = process.cwd()
const mode = process.argv[2]
assert.ok(['before', 'after'].includes(mode))
const staticRoot = await realpath(join(root, 'cloud-freeze-dist'))
const evidence = join(root, 'cloud-freeze-evidence')
await mkdir(evidence)
const executablePath = process.env.KNOWRA_BROWSER_EXECUTABLE
if (!executablePath) throw new Error('An installed browser is required; never download one')
const requests = [], externalAttempts = [], violations = [], tests = [], screenshots = [], storageChecks = []
let context
let cleanupConfirmed = false
const server = createServer(async (request, response) => {
  try {
    if (request.method !== 'GET') throw new Error('Static GET only')
    const url = new URL(request.url, 'http://127.0.0.1')
    const file = await realpath(resolve(staticRoot, '.' + decodeURIComponent(url.pathname)))
    if (!file.startsWith(staticRoot + sep) || !(await stat(file)).isFile()) throw new Error('Outside static fixture')
    const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf' }[extname(file)] || 'application/octet-stream'
    response.writeHead(200, { 'content-type': mime })
    response.end(await readFile(file))
  } catch {
    response.writeHead(404)
    response.end('Static fixture only; no backend exists')
  }
})
await new Promise((done, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', done) })
const port = server.address().port
const origin = `http://127.0.0.1:${port}`
let closing
function close() {
  return closing ||= (async () => {
    let error
    try { await context?.close() } catch (reason) { error = reason }
    finally {
      server.closeAllConnections()
      await new Promise((done, reject) => server.close(reason => reason ? reject(reason) : done()))
    }
    cleanupConfirmed = !error && !server.listening
    if (error) throw error
  })()
}
for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => { void close().finally(() => process.exit(128)) })
const json = (route, value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) })
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex')
const protectedKeys = ['knowra.cloud.supabaseUrl', 'knowra.cloud.supabaseAnonKey', 'knowra.cloud.baseUrl', 'knowra.cloud.session', 'knowra.cloud.deviceId', 'knowra.cloud.lastSyncAt', 'knowra.sync.pendingSession', 'knowra:ask-drawer:sessions', 'knowra.synthetic.research-note']
function seedState({ fresh = false, malformed = false } = {}) {
  const values = {
    'knowra.locale': 'zh',
    'knowra.cloud.supabaseUrl': 'https://auth.example.invalid',
    'knowra.cloud.supabaseAnonKey': 'synthetic-publishable-value',
    'knowra.cloud.baseUrl': 'https://cloud.example.invalid',
    'knowra.cloud.session': JSON.stringify({ access_token: 'synthetic-access-token', refresh_token: 'synthetic-refresh-token', expires_at: Date.now() + 120000, user: { id: 'synthetic-user', email: 'fixture@example.invalid', display_name: '合成账号' } }),
    'knowra.cloud.deviceId': 'synthetic-device',
    'knowra.cloud.lastSyncAt': '2026-10-01T00:00:00Z',
    'knowra.sync.pendingSession': malformed ? '{synthetic malformed pending' : JSON.stringify({ sync_session_id: 'synthetic-old-session', expires_at: '2020-01-01T00:00:00Z', uploaded: [{ rel_path: 'synthetic.md', content_hash: 'synthetic-content-hash' }] }),
    'knowra:ask-drawer:sessions': JSON.stringify({ activeSessionId: 'synthetic-chat', sessions: [{ id: 'synthetic-chat', title: '合成笔记', question: '保留的合成草稿', turns: [] }] }),
    'knowra.synthetic.research-note': 'Synthetic local note must remain byte-for-byte unchanged.',
  }
  if (fresh) for (const key of Object.keys(values)) if (key.startsWith('knowra.cloud.') || key.startsWith('knowra.sync.')) delete values[key]
  return values
}
const feed = {
  profile: { version: 1, current_focus: '合成研究课题', paper_count: 3, dimensions: { domain: { LLM: 1 }, method: { '合成方法': 1 } } },
  batch: { id: 'synthetic-batch', status: 'completed', error: null, slot: '2026-10-26T01:00:00Z', count: 1, profile_version: 1, created_at: '2026-10-26T01:00:00Z', completed_at: '2026-10-26T01:01:00Z' },
  latest_batch: null, job: null,
  items: [{ arxiv_id: 'synthetic-paper', title: '合成论文：本地推荐在云暂停后继续工作', abstract: '仅用于隔离验收的合成摘要。', authors: ['Synthetic Author'], published: '2026-10-20', primary_category: 'cs.AI', reason: '依据本机合成知识库的研究方向。', score: 1, ai: false, historical: false, lane: 'long_term', matched_terms: [], matched_teams: [], sources: [], in_library: true }],
  workers: [{ node_id: 'desktop-local', health: 'ready', last_seen_at: '2026-10-26T01:00:00Z' }], worker_status: 'online',
  metrics: { viewed: 1, mature_viewed: 0, adopted: 0, exposed: 1, adoption_rate_14d: null, target: 0.3 }, pending_imports: [], budget: { limit_cny: 0, reserved_cny: 0 },
}
feed.latest_batch = feed.batch
const job = { job_id: 'synthetic-pipeline', kind: 'pipeline', channel: 'pipeline', status: 'completed', progress: { label: '合成本地任务已完成' }, error: null, result: {} }
const defaultConfig = { openai_api_key: '', scan_directory: 'synthetic-library', vlm_model: 'synthetic-existing-model', embedding_model: 'synthetic-embedding', wiki_compile_model: 'synthetic-model', available_models: [], available_embedding_models: [], available_wiki_compile_models: [], available_model_gateway_models: [], similarity_threshold: 0.7, use_first_page_image: false, model_gateway: { providers: [], models: [], task_bindings: {}, task_specs: [], available_provider_types: [] } }

async function newPage(options = {}) {
  const page = await context.newPage()
  page.setDefaultTimeout(10000)
  const seed = seedState(options)
  page.on('pageerror', error => violations.push({ kind: 'pageerror', message: error.message }))
  page.on('popup', popup => { violations.push({ kind: 'popup' }); void popup.close() })
  await page.addInitScript(({ values, keys }) => {
    localStorage.clear()
    for (const [key, value] of Object.entries(values)) localStorage.setItem(key, value)
    window.__protectedMutations = []
    const originalSet = Storage.prototype.setItem
    const originalRemove = Storage.prototype.removeItem
    const originalClear = Storage.prototype.clear
    Storage.prototype.setItem = function(key, value) { if (this === localStorage && keys.includes(key)) window.__protectedMutations.push({ operation: 'set', key }); return originalSet.call(this, key, value) }
    Storage.prototype.removeItem = function(key) { if (this === localStorage && keys.includes(key)) window.__protectedMutations.push({ operation: 'remove', key }); return originalRemove.call(this, key) }
    Storage.prototype.clear = function() { if (this === localStorage) window.__protectedMutations.push({ operation: 'clear' }); return originalClear.call(this) }
  }, { values: seed, keys: protectedKeys })
  await page.routeWebSocket(/.*/, ws => { violations.push({ kind: 'websocket' }); ws.close() })
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname
    if (url.origin !== origin) {
      externalAttempts.push({ category: path.startsWith('/auth/') ? 'auth' : path.includes('signed-upload') ? 'upload' : 'cloud', method: request.method(), mode })
      // Baseline UI evidence uses explicitly synthetic endpoints and response
      // bodies. These routes never make an actual outbound connection.
      if (mode === 'before' && url.origin === 'https://auth.example.invalid' && path === '/auth/v1/token') return json(route, { access_token: 'synthetic-fresh-token', refresh_token: 'synthetic-refresh-token', expires_in: 3600, user: { id: 'synthetic-user', email: 'fixture@example.invalid', user_metadata: { display_name: '合成账号' } } })
      if (mode === 'before' && url.origin === 'https://cloud.example.invalid' && path === '/api/cloud/me') return json(route, { user_id: 'synthetic-user', email: 'fixture@example.invalid', stats: { papers: 3, nodes: 4, concepts: 4, edges: 2, wiki_files: 3, wiki_size_bytes: 2048, last_desktop_sync_at: '2026-10-01T00:00:00Z' } })
      violations.push({ kind: 'external-request-blocked' })
      return route.abort()
    }
    if (path.startsWith('/api/')) {
      requests.push({ path, method: request.method(), mode })
      if (options.handle && await options.handle(route, path, request)) return
      if (path === '/api/recommendations/personal' && request.method() === 'GET') return json(route, feed)
      if (path === '/api/recommendations/personal/batches') return json(route, { batches: [feed.batch], retention_days: 90, next_offset: null })
      if (path === '/api/recommendations/personal/events') return json(route, { ok: true })
      if (path === '/api/recommendations/personal/focus') return json(route, { ok: true })
      if (path === '/api/recommendations/personal/refresh') return json(route, { id: 'synthetic-local-job', status: 'queued' })
      if (path === '/api/recommendations/worker') return json(route, { running: true })
      if (path === '/api/recommendations/worker/start-local') return json(route, { running: true })
      if (path === '/api/jobs/latest') return json(route, null)
      if (path === '/api/config') return json(route, request.method() === 'POST' ? { ...defaultConfig, vlm_model: request.postDataJSON().vlm_model } : defaultConfig)
      if (path === '/api/config/model_gateway/providers/synthetic-provider/test') return json(route, { result: { provider_id: 'synthetic-provider', status: 'ok', message: 'Synthetic response only', tested_at: '2026-10-28T00:00:00Z' }, config: defaultConfig })
      if (mode === 'before' && path === '/api/sync/local_snapshot') return json(route, { counts: { papers: 3, knowledge_nodes: 4, knowledge_edges: 2, wiki_files: 3 }, tables: { papers: [], knowledge_nodes: [], knowledge_edges: [], wiki_files: [] }, wiki_bodies: {}, deletions: {} })
      violations.push({ kind: 'unmocked-api', path, method: request.method() })
      return route.abort()
    }
    if (path === '/favicon.ico') return route.fulfill({ status: 204 })
    if (request.method() === 'GET' && (path === '/tests/cloud-freeze-harness.html' || path.startsWith('/assets/'))) return route.continue()
    violations.push({ kind: 'unexpected-static-path' })
    return route.abort()
  })
  await page.goto(`${origin}/tests/cloud-freeze-harness.html`)
  await expect(page.getByRole('heading', { name: '设置 · 云同步' })).toBeVisible()
  return { page, seed }
}

async function verifyPreserved(page, seed, label) {
  const actual = await page.evaluate(keys => ({ values: Object.fromEntries(keys.map(key => [key, localStorage.getItem(key)])), mutations: window.__protectedMutations }), protectedKeys)
  const expected = Object.fromEntries(protectedKeys.map(key => [key, seed[key] ?? null]))
  assert.deepEqual(actual.values, expected, 'Protected synthetic state must remain byte-for-byte unchanged')
  assert.deepEqual(actual.mutations, [], 'Freezing must not write, remove or clear protected local state')
  storageChecks.push({ label, key_count: protectedKeys.length, unchanged: true, mutation_count: 0, sha256: hash(expected) })
}
async function screenshot(page, filename) {
  // Never expand connection settings or render token/session payloads.
  const visible = await page.locator('body').innerText()
  assert.ok(!visible.includes('synthetic-access-token') && !visible.includes('synthetic-refresh-token') && !visible.includes('synthetic-publishable-value'))
  await page.screenshot({ path: join(evidence, filename), fullPage: false })
  screenshots.push({ filename, sha256: createHash('sha256').update(await readFile(join(evidence, filename))).digest('hex'), width: 1680, height: 1050 })
}
async function test(name, action) {
  const start = Date.now()
  try { await action(); assert.deepEqual(violations, []); tests.push({ name, status: 'passed', ms: Date.now() - start }) }
  catch (error) { tests.push({ name, status: 'failed', message: error.message, ms: Date.now() - start }); throw error }
  finally { for (const page of context.pages()) await page.close() }
  console.log(JSON.stringify(tests.at(-1)))
}

try {
  context = await chromium.launchPersistentContext(join(root, 'tmp', `freeze-profile-${mode}`), {
    executablePath, headless: true, viewport: { width: 1680, height: 1050 }, locale: 'zh-CN',
    serviceWorkers: 'block', acceptDownloads: false,
    env: { PATH: process.env.PATH, HOME: join(root, 'tmp'), TMPDIR: join(root, 'tmp') },
    args: ['--disable-background-networking', '--disable-component-update', '--no-first-run', '--disable-sync', '--no-proxy-server', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'],
  })
  await context.route('**/*', route => { violations.push({ kind: 'unowned-context-request' }); return route.abort() })
  await context.routeWebSocket(/.*/, ws => { violations.push({ kind: 'context-websocket' }); ws.close() })
  if (mode === 'before') {
    await test('Baseline screenshot demonstrates existing cloud auth/status requests, fully mocked', async () => {
      const { page } = await newPage()
      await expect(page.getByRole('button', { name: '立即同步', exact: true })).toBeVisible()
      await expect(page.getByText('云端总量', { exact: true })).toBeVisible()
      await expect.poll(() => externalAttempts.filter(row => row.category === 'auth').length).toBeGreaterThan(0)
      await expect.poll(() => externalAttempts.filter(row => row.category === 'cloud').length).toBeGreaterThan(0)
      await screenshot(page, 'before-cloud-settings.png')
    })
  } else {
    await test('Existing login and expired pending sync survive bootstrap, tab events and timer advancement', async () => {
      const { page, seed } = await newPage()
      await expect(page.getByText('云同步与移动端暂时暂停', { exact: true })).toHaveCount(2)
      await expect(page.getByRole('button', { name: '立即同步', exact: true })).toHaveCount(0)
      await expect(page.getByRole('button', { name: '继续提交', exact: true })).toHaveCount(0)
      const state = await page.evaluate(() => window.cloudFreezeFixture.dormantState())
      assert.deepEqual(state, { session: null, pending: null, configured: false, config: { supabaseUrl: '', supabaseAnonKey: '', baseUrl: '' } })
      await page.clock.install()
      await page.evaluate(() => { window.dispatchEvent(new StorageEvent('storage', { key: 'knowra.cloud.session' })); window.dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange')) })
      await page.clock.fastForward(130000)
      await verifyPreserved(page, seed, 'old-login-expired-pending')
      assert.equal(requests.length, 0)
      assert.equal(externalAttempts.length, 0)
      await screenshot(page, 'after-cloud-paused.png')
    })
    await test('Every direct cloud/auth/sync/upload/remote worker entry rejects before network or local mutation', async () => {
      const { page, seed } = await newPage()
      const start = requests.length
      const probes = await page.evaluate(() => window.cloudFreezeFixture.directProbes())
      assert.equal(probes.length, 31)
      for (const probe of probes) assert.equal(probe.code, 'cloud_disabled', `${probe.name}: ${probe.status} ${probe.message}`)
      assert.equal(requests.length, start, 'No local exporter or remote-worker API may be reached')
      assert.equal(externalAttempts.length, 0, 'No fetch/XHR upload or cloud/auth request may be attempted')
      await verifyPreserved(page, seed, 'all-direct-entry-points')
    })
    await test('Fresh profiles and malformed pending state stay unchanged without creating device or login state', async () => {
      for (const options of [{ fresh: true }, { malformed: true }]) {
        const { page, seed } = await newPage(options)
        await expect(page.getByText('云同步与移动端暂时暂停', { exact: true })).toHaveCount(2)
        await page.evaluate(() => window.cloudFreezeFixture.dormantState())
        await verifyPreserved(page, seed, options.fresh ? 'fresh-profile' : 'malformed-pending')
        await page.close()
      }
    })
    await test('Page switches and repeated public recommendation clicks remain paused without retries', async () => {
      const { page, seed } = await newPage()
      await page.clock.install()
      for (let round = 0; round < 3; round++) {
        await page.getByRole('navigation', { name: '验收页面' }).getByRole('button', { name: '论文推荐', exact: true }).click()
        await expect(page.getByText(feed.items[0].title, { exact: true })).toBeVisible()
        await page.getByRole('button', { name: '完整推荐', exact: true }).dblclick()
        await expect(page.getByText('云同步与移动端暂时暂停', { exact: true })).toBeVisible()
        const before = requests.length
        await page.clock.fastForward(130000)
        assert.equal(requests.length, before, 'Paused recommendation UI cannot run polling or retries')
        await page.getByRole('navigation', { name: '验收页面' }).getByRole('button', { name: '云同步设置', exact: true }).click()
      }
      await verifyPreserved(page, seed, 'navigation-and-repeated-clicks')
      assert.equal(externalAttempts.length, 0)
    })
    await test('Local recommendations keep focus, feedback, worker start and refresh on local APIs', async () => {
      const { page, seed } = await newPage()
      await page.getByRole('navigation', { name: '验收页面' }).getByRole('button', { name: '论文推荐', exact: true }).click()
      await expect(page.getByText(feed.items[0].title, { exact: true })).toBeVisible()
      await screenshot(page, 'after-local-recommendations.png')
      await page.locator('summary').filter({ hasText: '科研品味 ·' }).click()
      await page.locator('#rec-focus').fill('修改后的合成课题')
      await page.getByRole('button', { name: '保存课题', exact: true }).click()
      await page.getByText('查看摘要与依据', { exact: true }).click()
      await expect(page.getByText(feed.items[0].abstract, { exact: true })).toBeVisible()
      await page.getByRole('button', { name: '更新精选', exact: true }).click()
      await expect(page.getByText('已提交本机精选任务，正在使用本地知识库生成。', { exact: true })).toBeVisible()
      assert.ok(requests.some(row => row.path === '/api/recommendations/personal/focus' && row.method === 'PUT'))
      assert.ok(requests.some(row => row.path === '/api/recommendations/personal/events' && row.method === 'POST'))
      assert.ok(requests.some(row => row.path === '/api/recommendations/worker/start-local' && row.method === 'POST'))
      assert.ok(requests.some(row => row.path === '/api/recommendations/personal/refresh' && row.method === 'POST'))
      await verifyPreserved(page, seed, 'local-recommendations')
    })
    await test('Local model configuration and provider-test clients remain available with synthetic responses', async () => {
      const { page, seed } = await newPage()
      const result = await page.evaluate(() => window.cloudFreezeFixture.localModelClients())
      assert.deepEqual(result, { initial: 'synthetic-existing-model', saved: 'synthetic-model', result: 'ok' })
      await verifyPreserved(page, seed, 'local-model-clients')
    })
    await test('User-triggered pipeline completes locally once despite same-tick double click', async () => {
      let starts = 0, polls = 0
      const { page, seed } = await newPage({ handle: async (route, path) => {
        if (path === '/api/pipeline/run') { starts++; await json(route, { ...job, status: 'running', progress: { label: '合成任务运行中' } }); return true }
        if (path === '/api/jobs/synthetic-pipeline') { polls++; await json(route, job); return true }
        return false
      } })
      await page.getByRole('navigation', { name: '验收页面' }).getByRole('button', { name: '本地全流程', exact: true }).click()
      const button = page.getByRole('button', { name: '全流程编排', exact: true })
      await button.evaluate(node => { node.click(); node.click() })
      await expect(page.getByText('扫描、处理、筛选、编译与健检已完成', { exact: true })).toBeVisible()
      assert.equal(starts, 1)
      assert.equal(polls, 1)
      assert.ok((await button.getAttribute('class')).includes('emerald'), 'Local completion must use success, not warning styling')
      await expect(page.getByText('同步已跳过：请先登录云端账号', { exact: true })).toHaveCount(0)
      assert.equal(await page.evaluate(() => window.cloudFreezeFixture.refreshCalls), 1)
      await verifyPreserved(page, seed, 'local-pipeline-complete')
      await screenshot(page, 'after-local-pipeline.png')
    })
    await test('Leaving a running pipeline and restoring it never resumes cloud synchronization', async () => {
      let starts = 0, completed = false
      const { page, seed } = await newPage({ handle: async (route, path) => {
        if (path === '/api/pipeline/run') { starts++; await json(route, { ...job, status: 'running', progress: { label: '合成任务运行中' } }); return true }
        if (path === '/api/jobs/synthetic-pipeline') { completed = true; await json(route, job); return true }
        if (path === '/api/jobs/latest') { await json(route, completed ? job : null); return true }
        return false
      } })
      await page.getByRole('navigation', { name: '验收页面' }).getByRole('button', { name: '本地全流程', exact: true }).click()
      await page.getByRole('button', { name: '全流程编排', exact: true }).click()
      await expect.poll(() => starts).toBe(1)
      await page.getByRole('navigation', { name: '验收页面' }).getByRole('button', { name: '云同步设置', exact: true }).click()
      await expect.poll(() => completed).toBe(true)
      await page.getByRole('navigation', { name: '验收页面' }).getByRole('button', { name: '本地全流程', exact: true }).click()
      await expect(page.getByText('本地流程已完成', { exact: true })).toBeVisible()
      assert.equal(starts, 1)
      await verifyPreserved(page, seed, 'pipeline-page-switch')
    })
    assert.equal(externalAttempts.length, 0)
    assert.ok(!requests.some(row => /\/sync\/|\/api\/cloud\/|\/worker\/start$/.test(row.path)))
  }
} catch (error) {
  console.error(error.message)
  process.exitCode = 1
} finally {
  try { await close() } catch (error) { console.error('Cleanup failed: ' + error.message); process.exitCode = 1 }
  const result = { mode, tests, requests, externalAttempts, violations, storageChecks, screenshots, cleanupConfirmed, fixture_port: port,
    boundary: 'Static loopback fixture; every business API mocked; all external requests and WebSockets blocked; no backend, database, task worker or model process' }
  await writeFile(join(evidence, 'browser-result.json'), JSON.stringify(result, null, 2) + '\n')
  if (tests.length !== (mode === 'before' ? 1 : 8) || tests.some(test => test.status !== 'passed') || violations.length || !cleanupConfirmed) process.exitCode = 1
}
