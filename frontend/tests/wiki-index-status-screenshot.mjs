/** Synthetic before/after screenshot check for Ask's two index statuses. */
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { readFile, realpath, stat, writeFile } from 'node:fs/promises'
import { dirname, extname, join, resolve, sep } from 'node:path'
import { chromium, expect } from '@playwright/test'

const root = process.cwd()
const staticRoot = await realpath(join(root, 'lifecycle-dist'))
const browserExecutable = process.env.KNOWRA_BROWSER_EXECUTABLE
const outputPath = process.env.KNOWRA_WIKI_INDEX_SCREENSHOT
const variant = process.env.KNOWRA_WIKI_INDEX_VARIANT
if (!browserExecutable || !outputPath || !['before', 'after'].includes(variant)) {
  throw new Error('Set browser, screenshot path and before/after variant')
}

const requests = []
const violations = []
const indexStatus = { exists: true, healthy: true, path: 'fixture/index.md', size: 1024, estimated_tokens: 240, read_latency_ms: 1, current_papers: 2, current_concepts: 0, stale: false }
const searchStatus = { exists: true, healthy: true, total: 2, by_kind: { paper: 2, concept: 0 }, db_path: 'fixture/wiki_search.sqlite', db_size: 32768, indexed_characters: 1800, estimated_tokens: 520, stale: false, status_latency_ms: 2,
  source: { files: 2, by_kind: { paper: 2, concept: 0 }, bytes: 4096, characters: 1800, estimated_tokens: 520, token_estimate_method: 'fixture heuristic', unreadable_files: 0, latest_modified_ns: 0, measurement_ms: 1 } }
const sessions = { activeSessionId: 'fixture', sessions: [{ id: 'fixture', title: 'Synthetic index status', question: '', createdAt: '2026-01-01', updatedAt: '2026-01-01', turns: [] }] }
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf' }
const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url, 'http://127.0.0.1')
    const file = await realpath(resolve(staticRoot, '.' + decodeURIComponent(url.pathname)))
    if (!file.startsWith(staticRoot + sep) || !(await stat(file)).isFile()) throw new Error('outside fixture')
    response.writeHead(200, { 'content-type': mime[extname(file)] || 'application/octet-stream' })
    response.end(await readFile(file))
  } catch {
    response.writeHead(404)
    response.end('fixture not found')
  }
})

let context
try {
  await new Promise((done, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', done) })
  const origin = `http://127.0.0.1:${server.address().port}`
  context = await chromium.launchPersistentContext(join(root, 'tmp', `wiki-index-${variant}`), {
    executablePath: browserExecutable,
    headless: true,
    viewport: { width: 1800, height: 1100 },
    serviceWorkers: 'block',
    acceptDownloads: false,
    args: ['--disable-background-networking', '--disable-component-update', '--no-first-run', '--disable-sync', '--no-proxy-server', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'],
  })
  const page = await context.newPage()
  page.on('pageerror', error => violations.push(String(error)))
  await page.addInitScript(value => {
    localStorage.clear()
    localStorage.setItem('knowra.locale', 'zh')
    localStorage.setItem('knowra.theme', 'dark')
    localStorage.setItem('knowra:ask-drawer:sessions', JSON.stringify(value))
  }, sessions)
  await page.route('**/*', async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) { violations.push(`external request: ${url.origin}`); return route.abort() }
    if (url.pathname.startsWith('/api/')) {
      requests.push(`${request.method()} ${url.pathname}`)
      if (url.pathname === '/api/wiki/index/status') return route.fulfill({ contentType: 'application/json', body: JSON.stringify(indexStatus) })
      if (url.pathname === '/api/wiki/search/stats') return route.fulfill({ contentType: 'application/json', body: JSON.stringify(searchStatus) })
      if (url.pathname === '/api/wiki/reindex') return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: '全文搜索索引刷新失败；旧索引仍可用。 Synthetic fixture failure.' }) })
      violations.push(`unmocked API: ${request.method()} ${url.pathname}`)
      return route.abort()
    }
    if (url.pathname === '/tests/lifecycle-harness.html' || url.pathname.startsWith('/assets/')) return route.continue()
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 })
    violations.push(`unexpected path: ${url.pathname}`)
    return route.abort()
  })
  await page.goto(`${origin}/tests/lifecycle-harness.html?component=ask`)

  let statusText
  let failureRetained = null
  if (variant === 'after') {
    const statuses = page.getByTestId('wiki-index-statuses')
    await expect(statuses).toContainText('Wiki 总览')
    await expect(statuses).toContainText('全文搜索')
    await statuses.screenshot({ path: outputPath, animations: 'disabled' })
    statusText = await statuses.innerText()
    await page.getByRole('button', { name: '刷新全文索引', exact: true }).click()
    await expect(page.getByText(/旧索引仍可用/)).toBeVisible()
    await expect(page.getByTestId('wiki-search-status')).toContainText('FTS5 · 2 篇文档 · 32.0 KB')
    failureRetained = true
  } else {
    const text = page.getByText(/index\.md · 1\.0 KB/)
    await expect(text).toBeVisible()
    const row = text.locator('..')
    await row.screenshot({ path: outputPath, animations: 'disabled' })
    statusText = await row.innerText()
  }
  assert.deepEqual(violations, [])
  await writeFile(
    join(dirname(outputPath), `${variant}-browser-result.json`),
    JSON.stringify({ variant, statusText, failureRetained, requests, violations }, null, 2) + '\n',
  )
} finally {
  await context?.close()
  server.closeAllConnections()
  if (server.listening) await new Promise((done, reject) => server.close(error => error ? reject(error) : done()))
}
