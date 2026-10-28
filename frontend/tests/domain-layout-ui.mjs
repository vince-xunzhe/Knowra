/** Run only through scripts/check_domain_layout.py. No backend, real API or data. */
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { readFile, realpath, stat, writeFile } from 'node:fs/promises'
import { extname, join, resolve, sep } from 'node:path'
import { chromium, expect } from '@playwright/test'
import { createLayoutFixture } from './domain-layout-fixture.mjs'

const root = process.cwd(), output = join(root, 'evidence')
const staticRoot = await realpath(join(root, 'layout-dist'))
const baseline = process.env.KNOWRA_LAYOUT_BASELINE === '1'
const violations = [], tests = [], requests = [], screenshots = []
const report = { fixture: '23 synthetic papers; 42 synthetic relations; cycle, separate component, locked node/edge, fixed group and note',
  baseline, tests, violations, requests, screenshots, metrics: {} }
let context
const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://127.0.0.1')
    const file = await realpath(resolve(staticRoot, '.' + decodeURIComponent(url.pathname)))
    if (!file.startsWith(staticRoot + sep) || !(await stat(file)).isFile()) throw new Error('Outside static fixture')
    const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2' }[extname(file)] || 'application/octet-stream'
    res.writeHead(200, { 'content-type': mime }); res.end(await readFile(file))
  } catch { res.writeHead(404); res.end('Static synthetic fixture only; no backend exists') }
})
await new Promise((done, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', done) })
const origin = `http://127.0.0.1:${server.address().port}`
let closing, cleanupConfirmed = false
function close() {
  return closing ||= (async () => {
    let browserError
    try { await context?.close() } catch (error) { browserError = error }
    finally {
      server.closeAllConnections()
      await new Promise((done, reject) => server.close(error => error ? reject(error) : done()))
    }
    cleanupConfirmed = !browserError && !server.listening
    if (browserError) throw browserError
  })()
}
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { void close().finally(() => process.exit(128)) })
const json = (route, value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) })
const settled = page => page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))))
const saved = page => expect(page.locator('.domain-save.saved')).toBeVisible()
const preview = page => page.getByTestId('domain-layout-preview')
const button = (page, name) => page.getByRole('button', { name, exact: true })
const dense = fixture => fixture.state.boards.find(board => board.id === 'dense')
const semantics = board => ({ ...board, viewport: undefined,
  nodes: board.nodes.map(({ x: _x, y: _y, ...node }) => node) })

async function domNodes(page) {
  return page.locator('[data-node-id]').evaluateAll(elements => Object.fromEntries(elements.map(element => [element.dataset.nodeId, {
    x: Number.parseFloat(element.style.left), y: Number.parseFloat(element.style.top),
    width: element.offsetWidth, height: element.offsetHeight,
  }])))
}
const positions = nodes => Object.fromEntries(Object.entries(nodes).map(([id, node]) => [id, { x: node.x, y: node.y }]))
async function metrics(page, edges) {
  return page.evaluate(edges => {
    const nodes = [...document.querySelectorAll('[data-node-id]')].filter(element => element.dataset.kind !== 'group').map(element => ({
      id: element.dataset.nodeId, x: parseFloat(element.style.left), y: parseFloat(element.style.top), width: element.offsetWidth, height: element.offsetHeight,
    }))
    let overlaps = 0, edgeNodeHits = 0, pathLength = 0
    const hitPairs = []
    for (let i = 0; i < nodes.length; i++) for (const b of nodes.slice(i + 1)) {
      const a = nodes[i]
      if (a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y) overlaps++
    }
    for (const edge of edges) {
      const path = document.querySelector(`[data-edge-id="${edge.id}"] .domain-edge-line`)
      if (!path) continue
      const length = path.getTotalLength(); pathLength += length
      for (const node of nodes.filter(n => n.id !== edge.source && n.id !== edge.target)) {
        let hit = false
        for (let i = 1; i < 220; i++) {
          const p = path.getPointAtLength(length * i / 220)
          if (p.x > node.x + 1 && p.x < node.x + node.width - 1 && p.y > node.y + 1 && p.y < node.y + node.height - 1) { hit = true; break }
        }
        if (hit) { edgeNodeHits++; hitPairs.push([edge.id, node.id]) }
      }
    }
    return { nodeOverlaps: overlaps, edgeNodeHits, sampledPointsPerPath: 220, totalPathLength: Math.round(pathLength), hitPairs }
  }, edges)
}
async function screenshot(page, filename) {
  await page.screenshot({ path: join(output, filename), fullPage: true })
  screenshots.push(filename)
}

async function newPage(options = {}) {
  const fixture = createLayoutFixture()
  const control = { fixture, puts: [], gets: 0, runs: [], nextSaveFailure: 0, pauseLoad: null, savedBodies: [] }
  if (options.runs) control.runs = options.runs
  const page = await context.newPage()
  page.setDefaultTimeout(10_000)
  page.on('pageerror', error => violations.push(String(error)))
  page.on('popup', popup => { violations.push('Unexpected popup'); void popup.close() })
  await page.addInitScript(() => {
    localStorage.clear(); localStorage.setItem('knowra.locale', 'zh')
    localStorage.setItem('knowra.theme', 'dark')
  })
  await page.routeWebSocket(/.*/, ws => { violations.push('Unexpected WebSocket'); ws.close() })
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url())
    if (url.origin !== origin) { violations.push(`Blocked external request: ${url.origin}`); return route.abort() }
    if (url.pathname.startsWith('/api/')) {
      requests.push(`${request.method()} ${url.pathname}`)
      if (url.pathname === '/api/domains' && request.method() === 'GET') {
        control.gets++
        if (control.pauseLoad) await control.pauseLoad
        return json(route, { state: fixture.state, revision: fixture.revision })
      }
      if (url.pathname === '/api/domains' && request.method() === 'PUT') {
        const data = request.postDataJSON(); control.puts.push(data)
        if (control.nextSaveFailure) {
          const code = control.nextSaveFailure; control.nextSaveFailure = 0
          return json(route, { detail: 'Synthetic controlled save failure' }, code)
        }
        if (data.revision !== fixture.revision) return json(route, { detail: 'Synthetic revision conflict' }, 409)
        fixture.state = structuredClone(data.state); fixture.revision++
        control.savedBodies.push(structuredClone(data.state))
        return json(route, { revision: fixture.revision })
      }
      if (url.pathname === '/api/domains/papers' && request.method() === 'GET') return json(route, fixture.papers)
      if (url.pathname === '/api/domains/agent/runs' && request.method() === 'GET') return json(route, control.runs)
      violations.push(`Unmocked API blocked: ${request.method()} ${url.pathname}`); return route.abort()
    }
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 })
    if (url.pathname === '/tests/domain-layout-harness.html' || url.pathname.startsWith('/assets/')) return route.continue()
    violations.push(`Unexpected path blocked: ${url.pathname}`); return route.abort()
  })
  await page.goto(`${origin}/tests/domain-layout-harness.html`)
  await saved(page)
  await expect(page.locator('[data-node-id]')).toHaveCount(25)
  await expect(page.locator('[data-edge-id]')).toHaveCount(42)
  await settled(page)
  return { page, control }
}

async function test(name, action) {
  const started = Date.now()
  try { await action(); assert.deepEqual(violations, []); tests.push({ name, status: 'passed', ms: Date.now() - started }) }
  catch (error) {
    tests.push({ name, status: 'failed', error: String(error), ms: Date.now() - started })
    for (const page of context.pages()) await screenshot(page, 'failure.png').catch(() => {})
    throw error
  } finally {
    for (const page of context.pages()) await page.close()
    console.log(JSON.stringify(tests.at(-1)))
  }
}

try {
  context = await chromium.launchPersistentContext(join(root, 'tmp/layout-profile'), {
    executablePath: process.env.KNOWRA_BROWSER_EXECUTABLE, headless: true,
    viewport: { width: 1800, height: 1100 }, locale: 'zh-CN', serviceWorkers: 'block', acceptDownloads: false,
    env: { PATH: process.env.PATH, HOME: join(root, 'tmp'), TMPDIR: join(root, 'tmp') },
    args: ['--disable-background-networking', '--disable-component-update', '--no-first-run', '--disable-sync',
      '--no-proxy-server', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'],
  })
  await context.route('**/*', route => { violations.push('Unexpected context request blocked'); return route.abort() })
  await context.routeWebSocket(/.*/, ws => { violations.push('Unexpected context WebSocket'); ws.close() })

  if (baseline) {
    await test('Fetched-main baseline rendered against the identical synthetic fixture', async () => {
      const { page, control } = await newPage()
      report.metrics.baseline = await metrics(page, dense(control.fixture).edges)
      await screenshot(page, 'before-baseline.png')
      assert.equal(control.puts.length, 0)
    })
  } else {
    await test('Preview/compare/pan/cancel preserve the original state without any write', async () => {
      const { page, control } = await newPage()
      const initial = await domNodes(page), viewport = await page.locator('.domain-world').getAttribute('style')
      report.metrics.before = await metrics(page, dense(control.fixture).edges)
      await screenshot(page, 'before-current.png')
      await button(page, '整理布局').click(); await expect(preview(page)).toBeVisible()
      const arranged = await domNodes(page)
      assert.notDeepEqual(positions(arranged), positions(initial))
      await expect(page.locator('[data-edge-id]')).toHaveCount(42)
      await screenshot(page, 'after-preview.png')
      await button(page, '查看原布局').click()
      assert.deepEqual(positions(await domNodes(page)), positions(initial))
      await button(page, '查看整理结果').click()
      assert.deepEqual(positions(await domNodes(page)), positions(arranged))
      await button(page, '缩小').click()
      await page.getByTestId('domain-canvas').hover({ position: { x: 600, y: 160 } })
      await page.mouse.wheel(50, 100)
      await page.waitForTimeout(800)
      assert.equal(control.puts.length, 0, 'Preview and viewport inspection must not save')
      await button(page, '取消预览').click(); await expect(preview(page)).toHaveCount(0)
      assert.deepEqual(await domNodes(page), initial)
      assert.equal(await page.locator('.domain-world').getAttribute('style'), viewport)
      assert.equal(control.puts.length, 0)
      await button(page, '整理布局').click(); await expect(preview(page)).toBeVisible()
      const search = page.getByRole('textbox', { name: '搜索画布中的论文', exact: true })
      await search.fill('Synthetic')
      await search.press('Escape'); await expect(preview(page)).toHaveCount(0)
      assert.deepEqual(await domNodes(page), initial)
    })

    await test('Apply preserves all semantics/locks; one undo restores layout; repeat is stable', async () => {
      const { page, control } = await newPage()
      const initial = structuredClone(dense(control.fixture)), originalPositions = positions(await domNodes(page))
      await button(page, '整理布局').click(); await expect(preview(page)).toBeVisible()
      const arrangedPositions = positions(await domNodes(page))
      // Applying from comparison still applies the arranged proposal.
      await button(page, '查看原布局').click()
      await button(page, '应用布局').evaluate(element => { element.click(); element.click() })
      await expect(preview(page)).toHaveCount(0); await saved(page)
      assert.deepEqual(positions(await domNodes(page)), arrangedPositions)
      const applied = structuredClone(dense(control.fixture))
      assert.deepEqual(semantics(applied), semantics(initial))
      for (const node of initial.nodes.filter(node => node.locked || node.groupId || node.kind !== 'paper')) {
        assert.deepEqual(applied.nodes.find(item => item.id === node.id), node)
      }
      assert.equal(control.puts.length, 1, 'Repeated apply must make one save')
      report.metrics.after = await metrics(page, applied.edges)
      assert.equal(report.metrics.after.nodeOverlaps, 0, 'No arranged paper/text rectangles overlap')
      assert.equal(report.metrics.after.edgeNodeHits, 0, 'Routed edges avoid non-endpoint cards')
      await screenshot(page, 'after-applied.png')
      await page.locator('[data-edge-id="e3"] .domain-edge-hit').dispatchEvent('pointerdown')
      await expect(page.locator('[data-edge-id="e3"]')).toHaveClass(/selected-edge/)
      await expect(page.getByRole('textbox', { name: '连线说明', exact: true })).toHaveValue('人工对照')
      await expect(page.locator('[data-edge-id="e3"] .domain-edge-lock')).toHaveAttribute('aria-pressed', 'true')
      assert.equal(control.puts.length, 1, 'Inspecting a routed manual relation is read-only')
      await screenshot(page, 'after-edge-detail.png')
      await button(page, '撤销').click(); await saved(page)
      assert.deepEqual(positions(await domNodes(page)), originalPositions)
      assert.deepEqual(dense(control.fixture), initial)
      await button(page, '重做').click(); await saved(page)
      assert.deepEqual(dense(control.fixture), applied)
      const writes = control.puts.length
      await button(page, '整理布局').click(); await expect(preview(page)).toBeVisible()
      assert.deepEqual(positions(await domNodes(page)), arrangedPositions)
      await expect(button(page, '应用布局')).toBeDisabled()
      await button(page, '取消预览').click()
      await page.waitForTimeout(750)
      assert.equal(control.puts.length, writes)
      await page.reload(); await saved(page)
      assert.deepEqual(positions(await domNodes(page)), arrangedPositions)
      assert.deepEqual(dense(control.fixture).edges, initial.edges)
    })

    await test('Unsaved note survives preview cancellation and layout undo during save failure', async () => {
      const { page, control } = await newPage()
      const original = positions(await domNodes(page))
      await page.locator('[data-node-id="n6"]').click({ position: { x: 60, y: 40 } })
      const note = page.getByRole('textbox', { name: '卡片备注', exact: true })
      control.nextSaveFailure = 503
      await note.fill('合成未保存编辑：必须保留，不应被整理取消覆盖。')
      await expect(page.locator('.domain-save.error')).toBeVisible()
      const writes = control.puts.length
      await button(page, '整理布局').click(); await expect(preview(page)).toBeVisible()
      await button(page, '取消预览').click()
      await expect(note).toHaveValue('合成未保存编辑：必须保留，不应被整理取消覆盖。')
      assert.deepEqual(positions(await domNodes(page)), original)
      await page.getByRole('combobox', { name: '领域', exact: true }).selectOption('other')
      await page.getByRole('combobox', { name: '领域', exact: true }).selectOption('dense')
      await page.locator('[data-node-id="n6"]').click({ position: { x: 60, y: 40 } })
      await expect(note).toHaveValue('合成未保存编辑：必须保留，不应被整理取消覆盖。')
      await button(page, '整理布局').click(); await button(page, '应用布局').click()
      await expect(preview(page)).toHaveCount(0)
      assert.notDeepEqual(positions(await domNodes(page)), original)
      await button(page, '撤销').click()
      assert.deepEqual(positions(await domNodes(page)), original)
      await expect(note).toHaveValue('合成未保存编辑：必须保留，不应被整理取消覆盖。')
      await page.waitForTimeout(750)
      assert.equal(control.puts.length, writes, 'Blocked autosave must not silently retry')
      await page.locator('.domain-save.error').getByRole('button', { name: '重试', exact: true }).click()
      await saved(page)
      assert.equal(dense(control.fixture).nodes.find(node => node.id === 'n6').text, '合成未保存编辑：必须保留，不应被整理取消覆盖。')
    })

    await test('A failed layout save keeps the applied canvas; retry persists it without data loss', async () => {
      const { page, control } = await newPage()
      const original = structuredClone(dense(control.fixture))
      await button(page, '整理布局').click(); await expect(preview(page)).toBeVisible()
      const arranged = positions(await domNodes(page))
      control.nextSaveFailure = 503
      await button(page, '应用布局').click(); await expect(page.locator('.domain-save.error')).toBeVisible()
      assert.deepEqual(dense(control.fixture), original)
      assert.deepEqual(positions(await domNodes(page)), arranged)
      await screenshot(page, 'after-save-failure.png')
      await page.locator('.domain-save.error').getByRole('button', { name: '重试', exact: true }).click(); await saved(page)
      assert.deepEqual(positions(Object.fromEntries(dense(control.fixture).nodes.map(node => [node.id, node]))), arranged)
      assert.deepEqual(semantics(dense(control.fixture)), semantics(original))
    })

    await test('Revision conflict never reloads or retries automatically; local layout remains undoable', async () => {
      const { page, control } = await newPage()
      const original = positions(await domNodes(page))
      await button(page, '整理布局').click(); await expect(preview(page)).toBeVisible()
      const arranged = positions(await domNodes(page))
      control.nextSaveFailure = 409
      await button(page, '应用布局').click(); await expect(page.locator('.domain-save.conflict')).toBeVisible()
      assert.deepEqual(positions(await domNodes(page)), arranged)
      const writes = control.puts.length
      await button(page, '撤销').click()
      assert.deepEqual(positions(await domNodes(page)), original)
      await page.waitForTimeout(750)
      assert.equal(control.puts.length, writes)
      await expect(page.locator('.domain-save.conflict')).toBeVisible()
    })

    await test('Field/page switches discard preview and never write its positions', async () => {
      const { page, control } = await newPage()
      const original = structuredClone(dense(control.fixture))
      const initial = positions(await domNodes(page))
      await button(page, '整理布局').click(); await expect(preview(page)).toBeVisible()
      await page.getByRole('combobox', { name: '领域', exact: true }).selectOption('other')
      await expect(preview(page)).toHaveCount(0); await saved(page)
      assert.deepEqual(dense(control.fixture), original)
      await page.getByRole('combobox', { name: '领域', exact: true }).selectOption('dense'); await saved(page)
      assert.deepEqual(positions(await domNodes(page)), initial)
      await button(page, '整理布局').click(); await expect(preview(page)).toBeVisible()
      const writes = control.puts.length
      await button(page, '离开合成页面').click(); await button(page, '返回合成页面').click()
      await expect(preview(page)).toHaveCount(0)
      assert.deepEqual(positions(await domNodes(page)), initial)
      await page.waitForTimeout(750)
      assert.equal(control.puts.length, writes)
      assert.deepEqual(dense(control.fixture), original)
    })

    await test('A newer refresh invalidates preview rather than overwriting the external canvas', async () => {
      const { page, control } = await newPage()
      await button(page, '整理布局').click(); await expect(preview(page)).toBeVisible()
      dense(control.fixture).nodes.find(node => node.id === 'n0').text = '合成外部更新：保留此备注'
      dense(control.fixture).nodes.find(node => node.id === 'n0').x = 777
      control.fixture.revision++
      const gets = control.gets
      await page.evaluate(() => window.dispatchEvent(new Event('focus')))
      await expect.poll(() => control.gets).toBeGreaterThan(gets)
      await expect(preview(page)).toHaveCount(0)
      await expect(page.locator('[data-node-id="n0"]')).toContainText('合成外部更新：保留此备注')
      assert.equal((await domNodes(page)).n0.x, 777)
      assert.equal(control.puts.length, 0)
    })

    await test('Pending Agent ownership prevents layout and produces no mutation', async () => {
      const { page, control } = await newPage({ runs: [{ id: 'synthetic-pending', boardId: 'dense', status: 'pending',
        jobId: 'synthetic-job', error: null, progress: { label: '合成代连任务' }, proposal: null }] })
      await expect(button(page, '取消生成')).toBeVisible()
      const arrange = button(page, '整理布局')
      assert(await arrange.count() === 0 || await arrange.isDisabled())
      assert.equal(control.puts.length, 0)
    })
  }
} finally {
  await close()
  report.cleanupConfirmed = cleanupConfirmed
  report.staticPort = Number(new URL(origin).port)
  report.networkScope = 'Only the random loopback static origin; all API requests fulfilled in browser routes'
  await writeFile(join(output, 'results.json'), JSON.stringify(report, null, 2) + '\n')
  if (!cleanupConfirmed) throw new Error('Browser/static fixture cleanup not confirmed')
}
