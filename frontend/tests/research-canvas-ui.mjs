// Invoked only by scripts/check_research_canvas.py against its disposable API.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { chromium } from 'playwright-core'

const [web, api, executable, output] = process.argv.slice(2)
const result = { steps: [], images: [], pageErrors: [], blockedRequests: [], failedResponses: [] }
const browser = await chromium.launch({ executablePath: executable, headless: true,
  args: ['--disable-background-networking', '--no-first-run'] })
for (const sig of ['SIGTERM', 'SIGINT']) process.once(sig, async () => {
  await browser.close(); process.exit(128 + (sig === 'SIGTERM' ? 15 : 2))
})

function pngMetadata(bytes) {
  assert.equal(bytes.subarray(0, 8).toString('hex'), '89504e470d0a1a0a')
  let width, height, density
  for (let offset = 8; offset < bytes.length;) {
    const length = bytes.readUInt32BE(offset)
    const name = bytes.toString('ascii', offset + 4, offset + 8)
    if (name === 'IHDR') { width = bytes.readUInt32BE(offset + 8); height = bytes.readUInt32BE(offset + 12) }
    if (name === 'pHYs') density = { x: bytes.readUInt32BE(offset + 8), y: bytes.readUInt32BE(offset + 12), unit: bytes[offset + 16] }
    offset += length + 12
  }
  assert(width > 0 && height > 0)
  assert.deepEqual(density, { x: 11811, y: 11811, unit: 1 })
  assert(width <= 8192 && height <= 8192 && width * height <= 24_000_000)
  return { width, height, density, bytes: bytes.length }
}

let page
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', acceptDownloads: true })
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url())
    if (url.protocol === 'data:' || url.protocol === 'blob:') return route.continue()
    if ((url.origin === web || url.origin === api) &&
        (['GET', 'HEAD'].includes(request.method()) ||
         (request.method() === 'PUT' && ['/api/domains', '/api/roadmaps'].includes(url.pathname)))) return route.continue()
    result.blockedRequests.push({ url: request.url(), method: request.method() })
    return route.abort()
  })
  page = await context.newPage()
  page.setDefaultTimeout(15_000)
  page.on('pageerror', error => result.pageErrors.push(error.message))
  page.on('response', response => { if (response.status() >= 400) result.failedResponses.push({ url: response.url(), status: response.status() }) })
  const downloads = []
  page.on('download', download => downloads.push(download))
  const harness = mode => `${web}/tests/research-canvas-harness.html?mode=${mode}`
  const saved = () => page.locator('.domain-save.saved').waitFor()
  const liveState = async mode => {
    const response = await context.request.get(`${api}/api/${mode === 'domain' ? 'domains' : 'roadmaps'}`)
    assert.equal(response.status(), 200)
    return (await response.json()).state
  }
  const openImport = async () => {
    await page.getByRole('button', { name: '导入论文', exact: true }).first().click()
    await page.locator('.domain-import-list > label').first().waitFor()
    return page.getByRole('dialog', { name: '导入论文', exact: true })
  }
  const downloadButton = () => page.locator('[data-testid$="-download-graph"]')
  const downloadImage = async (button, filename, repeated = false, afterStart) => {
    const before = downloads.length
    const event = page.waitForEvent('download')
    if (repeated) await button.evaluate(element => { element.click(); element.click(); element.click() })
    else await button.click()
    if (afterStart) await afterStart()
    const file = await event
    const target = path.join(output, filename)
    await file.saveAs(target)
    await button.waitFor({ state: 'visible' })
    await button.evaluate(element => new Promise(resolve => {
      const check = () => element.disabled ? requestAnimationFrame(check) : resolve()
      check()
    }))
    assert.equal(downloads.length - before, 1, 'Repeated clicks must produce one in-flight export')
    const bytes = await fs.readFile(target)
    const metadata = { ...pngMetadata(bytes), sha256: createHash('sha256').update(bytes).digest('hex'), downloadName: file.suggestedFilename() }
    const pixels = await page.evaluate(async encoded => {
      const image = new Image()
      image.src = `data:image/png;base64,${encoded}`
      await image.decode()
      const canvas = document.createElement('canvas')
      canvas.width = 800; canvas.height = Math.max(1, Math.round(image.height / image.width * 800))
      const ctx = canvas.getContext('2d')
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height)
      const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data
      const colors = new Set()
      let transparent = 0, light = 0
      for (let i = 0; i < data.length; i += 4) {
        colors.add(`${data[i]},${data[i + 1]},${data[i + 2]}`)
        if (data[i + 3] < 255) transparent++
        if (data[i] + data[i + 1] + data[i + 2] > 450) light++
      }
      return { decodedWidth: image.width, decodedHeight: image.height, colors: colors.size, transparent, light,
        corner: Array.from(data.slice(0, 4)) }
    }, bytes.toString('base64'))
    assert.equal(pixels.decodedWidth, metadata.width)
    assert.equal(pixels.decodedHeight, metadata.height)
    assert.equal(pixels.transparent, 0, 'Export must include an opaque background')
    assert(pixels.colors > 100 && pixels.light > 100, 'Decoded image must contain rendered content')
    result.images.push({ file: target, ...metadata, ...pixels })
    return { ...metadata, ...pixels }
  }

  await page.goto(harness('domain'), { waitUntil: 'networkidle' })
  await saved()
  await page.getByRole('button', { name: '导入论文', exact: true }).first().waitFor()
  let dialog = await openImport()
  const rows = dialog.locator('.domain-import-list > label')
  assert.equal(await rows.count(), 5)
  assert.equal(await rows.locator('[type=checkbox]:disabled').count(), 3)
  assert.equal(await rows.filter({ hasText: '同名论文' }).count(), 2)
  assert.equal(await rows.filter({ hasText: '同名论文' }).locator('[type=checkbox]:disabled').count(), 1)
  assert.equal(await rows.filter({ has: page.locator('[type=checkbox]:disabled') }).filter({ hasText: '已导入' }).count(), 2)
  await page.screenshot({ path: path.join(output, 'domain-import-status.png'), fullPage: true })
  await dialog.getByRole('checkbox', { name: '全选', exact: true }).check()
  assert.equal(await rows.locator('input:checked').count(), 2)
  result.steps.push('Real local Paper.id identity: same-title different IDs remain independently selectable; existing references and unavailable reviews disabled; select-all includes only two eligible new papers')

  let injected = false
  await page.route('**/api/domains', async route => {
    if (!injected && route.request().method() === 'PUT') {
      injected = true
      return route.fulfill({ status: 503, json: { detail: 'Synthetic one-shot save outage' } })
    }
    return route.fallback()
  })
  await dialog.getByRole('button', { name: '导入到画布', exact: true }).evaluate(element => { element.click(); element.click() })
  await page.locator('.domain-save.error').waitFor()
  assert(injected)
  assert.equal((await liveState('domain')).boards.find(b => b.id === 'domain-llm').nodes.filter(n => n.kind === 'paper').length, 2)
  assert.equal(await page.locator('[data-node-id]').count(), 6)
  dialog = await openImport()
  assert.equal(await dialog.locator('[data-imported="true"]').count(), 4)
  assert.equal(await dialog.getByRole('button', { name: '导入到画布', exact: true }).isDisabled(), true)
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
  await page.locator('.domain-save.error').getByRole('button', { name: '重试', exact: true }).click()
  await saved()
  const state = await liveState('domain')
  const imported = state.boards.find(b => b.id === 'domain-llm').nodes.filter(n => n.kind === 'paper')
  assert.deepEqual(imported.map(n => n.paperId).sort(), ['paper-a', 'paper-b', 'paper-c', 'paper-d'])
  result.steps.push('A one-shot 503 leaves pending local imports visible, prevents duplicate resubmission and preserves the old DB; existing save retry commits all four references exactly once through the real API')
  await page.reload({ waitUntil: 'networkidle' })
  await saved()
  dialog = await openImport()
  assert.equal(await dialog.locator('[data-imported="true"]').count(), 4)
  assert.equal(await dialog.locator('input[type=checkbox]:enabled').count(), 0)
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
  result.steps.push('A fresh editor reload reads persisted imported states from SQLite')

  await page.getByRole('button', { name: '缩小', exact: true }).click()
  await page.getByRole('button', { name: '平移画布', exact: true }).click()
  const canvas = page.getByTestId('domain-canvas'), area = await canvas.boundingBox()
  await page.mouse.move(area.x + area.width - 90, area.y + 140)
  await page.mouse.down(); await page.mouse.move(area.x + area.width - 210, area.y + 220, { steps: 5 }); await page.mouse.up()
  await page.getByRole('button', { name: '选择与框选', exact: true }).click()
  await page.locator('[data-node-id="domain-a"]').click({ position: { x: 70, y: 50 } })
  await saved()
  const beforeExport = await canvas.locator('.domain-world').getAttribute('style')
  const domainImage = await downloadImage(downloadButton(), 'domain-complete.png', true)
  assert(domainImage.width > 4000 && domainImage.height > 2000, 'Whole graph must exceed the viewport with sufficient pixels')
  assert.equal(await canvas.locator('.domain-world').getAttribute('style'), beforeExport)
  await page.screenshot({ path: path.join(output, 'domain-export-ui.png'), fullPage: true })
  result.steps.push('Selected, panned and zoomed nonempty canvas exported as full opaque PNG; current viewport unchanged; repeated clicks generated one file')

  await page.getByRole('button', { name: '放大', exact: true }).click()
  await page.getByRole('button', { name: '平移画布', exact: true }).click()
  await page.mouse.move(area.x + area.width - 90, area.y + 160)
  await page.mouse.down(); await page.mouse.move(area.x + area.width - 150, area.y + 240, { steps: 5 }); await page.mouse.up()
  await saved()
  const movedImage = await downloadImage(downloadButton(), 'domain-different-viewport.png')
  assert.equal(movedImage.sha256, domainImage.sha256, 'Pan and zoom must not alter the exported full graph pixels')
  result.steps.push('Changing zoom and pan produces a byte-identical full PNG (SHA-256 equality)')

  await page.evaluate(() => {
    Object.defineProperty(document.fonts, 'ready', { configurable: true, value: new Promise(resolve => { window.releaseFixtureFont = resolve }) })
  })
  const racedImage = await downloadImage(downloadButton(), 'domain-switch-during-export.png', false, async () => {
    await page.getByRole('combobox', { name: '领域', exact: true }).selectOption('domain-large')
    await page.evaluate(() => { window.releaseFixtureFont(); delete document.fonts.ready; delete window.releaseFixtureFont })
  })
  assert.equal(racedImage.sha256, domainImage.sha256, 'Board change during font readiness must preserve the clicked graph snapshot')
  assert(racedImage.downloadName.includes('LLM'))
  result.steps.push('Deferred font readiness plus immediate board switching preserves the original LLM graph pixels and filename')

  await page.getByRole('combobox', { name: '领域', exact: true }).selectOption('domain-empty')
  await saved()
  assert.equal(await downloadButton().isDisabled(), true)
  result.steps.push('Empty graph export is disabled')
  await page.getByRole('combobox', { name: '领域', exact: true }).selectOption('domain-large')
  await saved()
  await downloadImage(downloadButton(), 'domain-large.png')
  result.steps.push('120-node graph exported within both 8192-pixel edge and 24-million-pixel allocation caps')
  await page.getByRole('combobox', { name: '领域', exact: true }).selectOption('domain-extreme')
  await saved()
  const beforeFailure = downloads.length
  await downloadButton().click()
  await page.getByRole('alert').filter({ hasText: '图的范围过大' }).waitFor()
  assert.equal(downloads.length, beforeFailure)
  assert.equal(await downloadButton().isEnabled(), true)
  result.steps.push('Extreme node span fails with actionable feedback and releases export busy state')

  await page.goto(harness('roadmap'), { waitUntil: 'networkidle' })
  await saved()
  const roadmapImage = await downloadImage(downloadButton(), 'roadmap-complete.png')
  assert(roadmapImage.width > 4000)
  await page.screenshot({ path: path.join(output, 'roadmap-export-ui.png'), fullPage: true })
  result.steps.push('Real roadmap catalog and canvas export include topic, branch, milestone, long Chinese paper labels and connecting paths')
  await page.getByRole('tab', { name: '研报', exact: true }).click()
  const mermaid = page.getByRole('button', { name: '下载流程图（高清 PNG）', exact: true })
  await mermaid.waitFor()
  const reportBackground = await page.locator('.roadmap-diagram').first().evaluate(element => getComputedStyle(element).backgroundColor)
  const reportImage = await downloadImage(mermaid, 'roadmap-report-diagram.png', true)
  assert.deepEqual(reportImage.corner, [...reportBackground.match(/[\d.]+/g).slice(0, 3).map(Number), 255],
    'Mermaid rules must not replace the original report background with node fills')
  await page.screenshot({ path: path.join(output, 'roadmap-report-export-ui.png'), fullPage: true })
  result.steps.push('Saved report from real API renders Mermaid Chinese flowchart and downloads a decoded 300-DPI PNG with corner pixels matching the original background')
  assert.deepEqual(result.pageErrors, [])
  assert.deepEqual(result.blockedRequests, [])
  assert.equal(result.failedResponses.length, 1)
  assert.equal(result.failedResponses[0].status, 503)
  result.success = true
} catch (error) {
  result.success = false
  result.error = error.stack || String(error)
  if (page) await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => undefined)
  process.exitCode = 1
} finally {
  await browser.close()
  await fs.writeFile(path.join(output, 'browser-result.json'), JSON.stringify(result, null, 2) + '\n')
  if (!result.success) console.error(result.error)
}
