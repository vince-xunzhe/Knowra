/** Run only through scripts/check_domain_highlight.py; no backend or real data. */
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { readFile, realpath, stat, writeFile } from 'node:fs/promises'
import { extname, join, resolve, sep } from 'node:path'
import { chromium, expect } from '@playwright/test'
import { createHighlightFixture } from './domain-highlight-fixture.mjs'
import { createLayoutFixture } from './domain-layout-fixture.mjs'

const root = process.cwd(), output = join(root, 'evidence')
const staticRoot = await realpath(join(root, 'layout-dist'))
const baseline = process.env.KNOWRA_LAYOUT_BASELINE === '1'
const violations = [], tests = [], requests = [], screenshots = []
const report = { fixture: '7 synthetic papers; 6 relations (incoming, outgoing, reciprocal, unrelated); locked node/edge; isolated node; second domain reuses node IDs',
  baseline, tests, violations, requests, screenshots, animation: {} }
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
const button = (page, name) => page.getByRole('button', { name, exact: true })
const node = (page, id) => page.locator(`[data-node-id="${id}"]`)
const board = control => control.fixture.state.boards.find(item => item.id === control.fixture.state.activeId)
const saved = page => expect(page.locator('.domain-save.saved')).toBeVisible()
const highlighted = page => page.locator('[data-edge-id][data-highlighted="true"]')
const incident = (control, id) => board(control).edges.filter(edge => edge.source === id || edge.target === id).map(edge => edge.id).sort()
const geometry = page => page.locator('[data-node-id], [data-edge-id]').evaluateAll(elements => elements.map(element => ({
  node: element.dataset.nodeId, edge: element.dataset.edgeId,
  style: element.dataset.nodeId ? element.getAttribute('style') : undefined,
  path: element.querySelector('.domain-edge-line')?.getAttribute('d'),
})))
const semantics = value => ({ ...value, viewport: undefined, nodes: value.nodes.map(({ x: _x, y: _y, ...item }) => item) })
async function focusNode(page, id, options = {}) {
  await node(page, id).click({ position: { x: 70, y: 60 }, ...options })
}
async function expectEdges(page, ids) {
  await expect.poll(() => highlighted(page).evaluateAll(elements => elements.map(element => element.dataset.edgeId).sort())).toEqual([...ids].sort())
}
async function animationState(page) {
  return highlighted(page).evaluateAll(elements => elements.map(element => ({ id: element.dataset.edgeId,
    animations: element.getAnimations({ subtree: true }).map(animation => ({
      name: animation.animationName, state: animation.playState, currentTime: animation.currentTime,
      duration: animation.effect.getTiming().duration, iterations: animation.effect.getTiming().iterations,
    })),
    paths: [...element.querySelectorAll('.domain-edge-focus-line')].map(path => ({
      opacity: getComputedStyle(path).opacity, stroke: getComputedStyle(path).stroke, strokeWidth: getComputedStyle(path).strokeWidth,
      pointerEvents: getComputedStyle(path).pointerEvents,
    })),
  })))
}
async function screenshot(page, filename) {
  await page.screenshot({ path: join(output, filename), fullPage: true })
  screenshots.push(filename)
}
async function noWrite(control, page, initial, originalGeometry) {
  await page.waitForTimeout(750)
  assert.equal(control.puts.length, 0, 'UI focus must never enqueue a save')
  assert.deepEqual(control.fixture.state, initial, 'All synthetic records remain byte-for-byte equal')
  assert.deepEqual(await geometry(page), originalGeometry, 'Node layout and routed paths remain unchanged')
}
async function drag(page, id, dx, dy) {
  const bounds = await node(page, id).boundingBox()
  await page.mouse.move(bounds.x + 70, bounds.y + 60)
  await page.mouse.down()
  await page.mouse.move(bounds.x + 70 + dx, bounds.y + 60 + dy, { steps: 6 })
  await page.mouse.up()
}
async function blank(page) {
  const rect = await page.getByTestId('domain-canvas').boundingBox()
  await page.mouse.click(rect.x + rect.width - 25, rect.y + 180)
}
async function newPage(options = {}) {
  const fixture = options.dense ? createLayoutFixture() : createHighlightFixture(), api = options.roadmap ? '/api/roadmaps' : '/api/domains'
  if (options.dense) fixture.state.boards[0].viewport = { x: 80, y: 80, zoom: 0.39 }
  const control = { fixture, puts: [], gets: 0 }
  const page = await context.newPage()
  page.setDefaultTimeout(10_000)
  if (options.reducedMotion) await page.emulateMedia({ reducedMotion: options.reducedMotion })
  page.on('pageerror', error => violations.push(String(error)))
  page.on('popup', popup => { violations.push('Unexpected popup'); void popup.close() })
  await page.addInitScript(() => {
    localStorage.clear(); localStorage.setItem('knowra.locale', 'zh'); localStorage.setItem('knowra.theme', 'dark')
  })
  await page.routeWebSocket(/.*/, ws => { violations.push('Unexpected WebSocket'); ws.close() })
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url())
    if (url.origin !== origin) { violations.push(`Blocked external request: ${url.origin}`); return route.abort() }
    if (url.pathname.startsWith('/api/')) {
      requests.push(`${request.method()} ${url.pathname}`)
      if (url.pathname === api && request.method() === 'GET') {
        control.gets++; return json(route, { state: fixture.state, revision: fixture.revision })
      }
      if (url.pathname === api && request.method() === 'PUT') {
        const data = request.postDataJSON(); control.puts.push(data)
        if (data.revision !== fixture.revision) return json(route, { detail: 'Synthetic revision conflict' }, 409)
        fixture.state = structuredClone(data.state); fixture.revision++
        return json(route, { revision: fixture.revision })
      }
      if (url.pathname === api + '/papers' && request.method() === 'GET') return json(route, fixture.papers)
      if (url.pathname === api + '/agent/runs' && request.method() === 'GET') return json(route, options.runs ?? [])
      if (options.roadmap && url.pathname === api + '/imports' && request.method() === 'GET') return json(route, null)
      violations.push(`Unmocked API blocked: ${request.method()} ${url.pathname}`); return route.abort()
    }
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 })
    if (url.pathname === '/tests/domain-highlight-harness.html' || url.pathname.startsWith('/assets/')) return route.continue()
    violations.push(`Unexpected path blocked: ${url.pathname}`); return route.abort()
  })
  await page.goto(`${origin}/tests/domain-highlight-harness.html${options.roadmap ? '?roadmap=1' : ''}`)
  await saved(page)
  await expect(page.locator('[data-node-id]')).toHaveCount(options.dense ? 25 : 7)
  await expect(page.locator('[data-edge-id]')).toHaveCount(options.dense ? 42 : 6)
  await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))))
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
    await test('Fetched main: selecting the same synthetic paper does not highlight its incident edges', async () => {
      const { page, control } = await newPage()
      const initial = structuredClone(control.fixture.state), positions = await geometry(page)
      await focusNode(page, 'n1'); await expectEdges(page, [])
      await screenshot(page, 'before-baseline.png')
      await noWrite(control, page, initial, positions)
    })
  } else {
    await test('Incoming/outgoing/reciprocal edges pulse briefly then stay highlighted; data and layout remain identical', async () => {
      const { page, control } = await newPage({ reducedMotion: 'no-preference' })
      const initial = structuredClone(control.fixture.state), positions = await geometry(page)
      await screenshot(page, 'before-current.png')
      await focusNode(page, 'n1'); await expectEdges(page, incident(control, 'n1'))
      report.animation.started = await animationState(page)
      assert(report.animation.started.every(edge => edge.animations.some(animation => animation.state === 'running' && animation.iterations === 2 && animation.duration === 1400)))
      assert(report.animation.started.every(edge => edge.paths.every(path => path.pointerEvents === 'none')))
      await page.waitForTimeout(650)
      report.animation.midPulse = await animationState(page)
      await screenshot(page, 'after-pulse.png')
      await page.waitForTimeout(2400)
      report.animation.settled = await animationState(page)
      assert(report.animation.settled.every(edge => edge.animations.every(animation => animation.state !== 'running')))
      assert(report.animation.midPulse.every((edge, index) => edge.paths.some((path, pathIndex) =>
        path.opacity !== report.animation.settled[index].paths[pathIndex].opacity &&
        path.strokeWidth !== report.animation.settled[index].paths[pathIndex].strokeWidth)), 'Pulse visibly changes opacity and width before returning to static')
      assert(report.animation.settled.every(edge => edge.paths.length > 0 && edge.paths.every(path => Number(path.opacity) > 0)))
      await expectEdges(page, incident(control, 'n1')); await screenshot(page, 'after-static.png')
      await noWrite(control, page, initial, positions)
    })

    await test('Repeated switching/reselecting, Shift selection, double-click, isolated paper and blank cancellation never save', async () => {
      const { page, control } = await newPage()
      const initial = structuredClone(control.fixture.state), positions = await geometry(page)
      for (const id of ['n1', 'n2', 'n0', 'n1', 'n1', 'n2', 'n1']) {
        await focusNode(page, id); await expectEdges(page, incident(control, id))
        assert((await animationState(page)).every(edge => edge.animations.some(animation => animation.state === 'running' && animation.currentTime < 600)))
      }
      await focusNode(page, 'n2', { modifiers: ['Shift'] })
      await expect(page.locator('[data-node-id][data-selected="true"]')).toHaveCount(2)
      await expectEdges(page, incident(control, 'n2'))
      await blank(page); await expectEdges(page, [])
      await node(page, 'n1').dblclick({ position: { x: 70, y: 60 } })
      await expectEdges(page, incident(control, 'n1'))
      await focusNode(page, 'n6'); await expectEdges(page, [])
      await focusNode(page, 'n1'); await blank(page); await expectEdges(page, [])
      await noWrite(control, page, initial, positions)
    })

    await test('A 1–4px pointer tremor is still a no-write click; a drag beyond threshold and back never activates another paper', async () => {
      const { page, control } = await newPage()
      const initial = structuredClone(control.fixture.state), positions = await geometry(page)
      for (const pixels of [1, 2, 3, 4]) {
        await drag(page, 'n1', pixels, 0)
        await expectEdges(page, incident(control, 'n1'))
      }
      const locked = await node(page, 'n3').boundingBox()
      await page.mouse.move(locked.x + 60, locked.y + 60); await page.mouse.down()
      await page.mouse.move(locked.x + 85, locked.y + 60, { steps: 4 })
      await page.mouse.move(locked.x + 60, locked.y + 60, { steps: 4 }); await page.mouse.up()
      await expectEdges(page, incident(control, 'n1'))
      await noWrite(control, page, initial, positions)
    })

    await test('Reduced motion is static immediately; Enter selects and Escape clears without intercepting inner buttons', async () => {
      const { page, control } = await newPage({ reducedMotion: 'reduce' })
      const initial = structuredClone(control.fixture.state), positions = await geometry(page)
      await node(page, 'n1').focus(); await page.keyboard.press('Enter'); await expectEdges(page, incident(control, 'n1'))
      report.animation.reducedMotion = await animationState(page)
      assert(report.animation.reducedMotion.every(edge => edge.animations.length === 0))
      await screenshot(page, 'after-reduced-motion.png')
      await page.keyboard.press('Escape'); await expectEdges(page, [])
      await node(page, 'n2').focus(); await page.keyboard.press('Enter'); await expectEdges(page, incident(control, 'n2'))
      await node(page, 'n2').locator('.domain-review-plus').focus(); await page.keyboard.press('Enter')
      await expect(page.getByTestId('domain-review-popover')).toBeVisible()
      await button(page.getByTestId('domain-review-popover'), '关闭').click()
      await noWrite(control, page, initial, positions)
    })

    await test('Domain/page changes clear transient focus even when IDs are reused; reload never persists focus', async () => {
      const { page, control } = await newPage()
      const initialBoards = structuredClone(control.fixture.state.boards)
      await focusNode(page, 'n1')
      await page.getByRole('combobox', { name: '领域', exact: true }).selectOption('other'); await expectEdges(page, [])
      await expect(page.locator('[data-edge-id]')).toHaveCount(1)
      await focusNode(page, 'n1'); await expectEdges(page, ['other-edge'])
      await page.getByRole('combobox', { name: '领域', exact: true }).selectOption('highlight'); await expectEdges(page, [])
      await saved(page)
      assert.deepEqual(control.fixture.state.boards, initialBoards)
      const writes = control.puts.length
      await focusNode(page, 'n1'); await button(page, '离开合成页面').click(); await button(page, '返回合成页面').click()
      await expectEdges(page, [])
      await focusNode(page, 'n1'); await page.reload(); await saved(page); await expectEdges(page, [])
      assert.equal(control.puts.length, writes, 'Page visibility and focus do not save')
    })

    await test('Zoom/pan and dragging preserve focus; dragging another card does not retarget; locked nodes remain fixed', async () => {
      const { page, control } = await newPage()
      const initial = structuredClone(board(control))
      await focusNode(page, 'n1'); await button(page, '缩小').click(); await saved(page)
      await expectEdges(page, incident(control, 'n1'))
      await focusNode(page, 'n1')
      await page.keyboard.down('Space'); await drag(page, 'n2', 45, 35); await page.keyboard.up('Space'); await saved(page)
      const afterPaperPan = structuredClone(board(control).viewport)
      const canvas = await page.getByTestId('domain-canvas').boundingBox()
      await page.keyboard.down('Space')
      await page.mouse.move(canvas.x + canvas.width - 80, canvas.y + 160); await page.mouse.down()
      await page.mouse.move(canvas.x + canvas.width - 45, canvas.y + 180, { steps: 4 }); await page.mouse.up()
      await page.keyboard.up('Space'); await saved(page)
      assert.equal(board(control).viewport.x, afterPaperPan.x + 35)
      assert.equal(board(control).viewport.y, afterPaperPan.y + 20)
      await expectEdges(page, incident(control, 'n1'))
      assert.deepEqual(board(control).nodes, initial.nodes)
      await drag(page, 'n2', 60, 25); await saved(page); await expectEdges(page, incident(control, 'n1'))
      assert.notDeepEqual(board(control).nodes.find(item => item.id === 'n2'), initial.nodes.find(item => item.id === 'n2'))
      assert.deepEqual(semantics(board(control)), semantics(initial))
      const lockedBefore = structuredClone(board(control).nodes.find(item => item.id === 'n3'))
      await focusNode(page, 'n3'); await expectEdges(page, incident(control, 'n3'))
      const writes = control.puts.length
      await drag(page, 'n3', 50, 35); await page.waitForTimeout(750)
      assert.deepEqual(board(control).nodes.find(item => item.id === 'n3'), lockedBefore)
      assert.equal(control.puts.length, writes)
      await screenshot(page, 'after-zoom-drag-lock.png')
    })

    await test('Layout preview permits inspection without mutation; compare/cancel keep original geometry and apply/undo preserve relations', async () => {
      const { page, control } = await newPage()
      const initial = structuredClone(board(control)), positions = await geometry(page)
      await focusNode(page, 'n1'); await button(page, '整理布局').click()
      await expect(page.getByTestId('domain-layout-preview')).toBeVisible()
      await focusNode(page, 'n1'); await expectEdges(page, incident(control, 'n1'))
      await screenshot(page, 'after-layout-preview.png')
      await button(page, '查看原布局').click(); assert.deepEqual(await geometry(page), positions)
      await expectEdges(page, incident(control, 'n1'))
      await button(page, '查看整理结果').click(); await button(page, '取消预览').click()
      assert.deepEqual(await geometry(page), positions)
      assert.equal(control.puts.length, 0)
      await button(page, '整理布局').click(); await button(page, '应用布局').click(); await saved(page)
      assert.deepEqual(semantics(board(control)), semantics(initial))
      await button(page, '撤销').click(); await saved(page)
      assert.deepEqual(board(control), initial)
    })

    await test('Highlight does not block relation evidence, lock keyboard controls or explicit connection mode', async () => {
      const { page, control } = await newPage()
      const initial = structuredClone(board(control))
      await focusNode(page, 'n1')
      const hitPoint = await page.locator('[data-edge-id="e0"] .domain-edge-hit').evaluate(path => {
        for (const fraction of [.2, .3, .4, .6, .7, .8]) {
          const point = path.getPointAtLength(path.getTotalLength() * fraction).matrixTransform(path.getScreenCTM())
          const hit = document.elementFromPoint(point.x, point.y)
          if (hit?.classList.contains('domain-edge-hit') && hit.closest('[data-edge-id]')?.getAttribute('data-edge-id') === 'e0') return { x: point.x, y: point.y }
        }
        return null
      })
      assert(hitPoint, 'A highlighted edge must remain directly pointer-hittable')
      await page.mouse.click(hitPoint.x, hitPoint.y)
      await expect(page.locator('[data-edge-id="e0"]')).toHaveClass(/selected-edge/)
      await page.locator('.domain-evidence-reference').click()
      await expect(page.locator('.domain-edge-evidence blockquote')).toContainText('Synthetic evidence only')
      assert.equal(control.puts.length, 0)
      await screenshot(page, 'after-evidence.png')
      const lock = page.locator('[data-edge-id="e2"] .domain-edge-lock')
      await expect(lock).toHaveAttribute('aria-pressed', 'true')
      await lock.focus(); await page.keyboard.press('Enter'); await saved(page)
      await expect(lock).toHaveAttribute('aria-pressed', 'false')
      await lock.focus(); await page.keyboard.press('Space'); await saved(page)
      assert.deepEqual(board(control), initial)
      await focusNode(page, 'n1')
      await node(page, 'n0').getByRole('button', { name: '连接论文', exact: true }).click()
      await expect(page.locator('.domain-link-hint')).toBeVisible()
      await focusNode(page, 'n4'); await saved(page)
      const added = board(control).edges.filter(edge => !initial.edges.some(original => original.id === edge.id))
      assert.equal(added.length, 1); assert.equal(added[0].source, 'n0'); assert.equal(added[0].target, 'n4')
      assert.deepEqual(board(control).edges.filter(edge => edge.id !== added[0].id), initial.edges)
      assert.deepEqual(board(control).nodes, initial.nodes)
      await expect(page.locator('.domain-link-hint')).toHaveCount(0)
    })

    await test('Dense 23-paper graph at 39% zoom highlights every connected edge without changing routes or records', async () => {
      const { page, control } = await newPage({ dense: true })
      const initial = structuredClone(control.fixture.state), positions = await geometry(page)
      await screenshot(page, 'dense-before-39-percent.png')
      await node(page, 'n6').locator('h3').click()
      await expectEdges(page, incident(control, 'n6'))
      await page.waitForTimeout(3000)
      await screenshot(page, 'dense-after-39-percent.png')
      await noWrite(control, page, initial, positions)
      await button(page, '放大').click(); await button(page, '放大').click(); await saved(page)
      await expectEdges(page, incident(control, 'n6'))
      await screenshot(page, 'dense-after-61-percent.png')
      assert.deepEqual(board(control).nodes, initial.boards[0].nodes)
      assert.deepEqual(board(control).edges, initial.boards[0].edges)
    })

    await test('Pending Agent remains read-only while allowing highlighting; shared Roadmap mode has no incident highlight', async () => {
      const { page, control } = await newPage({ runs: [{ id: 'synthetic-pending', boardId: 'highlight', status: 'pending',
        jobId: 'synthetic-job', error: null, progress: { label: '合成代连任务' }, proposal: null }] })
      const initial = structuredClone(control.fixture.state), positions = await geometry(page)
      await expect(button(page, '取消生成')).toBeVisible()
      await focusNode(page, 'n1'); await expectEdges(page, incident(control, 'n1'))
      await noWrite(control, page, initial, positions)
      await page.close()
      const roadmap = await newPage({ roadmap: true })
      const roadmapInitial = structuredClone(roadmap.control.fixture.state), roadmapGeometry = await geometry(roadmap.page)
      await focusNode(roadmap.page, 'n1'); await expectEdges(roadmap.page, [])
      assert.equal(await roadmap.page.locator('.domain-edge-focus-line').count(), 0)
      await noWrite(roadmap.control, roadmap.page, roadmapInitial, roadmapGeometry)
    })
  }
} finally {
  await close()
  report.cleanupConfirmed = cleanupConfirmed
  report.staticPort = Number(new URL(origin).port)
  report.networkScope = 'Only random loopback static origin; API responses are in-memory browser mocks; no backend/database'
  await writeFile(join(output, 'results.json'), JSON.stringify(report, null, 2) + '\n')
  if (!cleanupConfirmed) throw new Error('Browser/static fixture cleanup not confirmed')
}
