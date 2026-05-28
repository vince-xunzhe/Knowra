// Headless-chromium driver that captures the 9 screenshots referenced
// by README.md and the pitch deck. Assumes:
//
//   - Backend on http://localhost:8000  (FastAPI / uvicorn)
//   - Frontend on http://localhost:5173 (Vite dev server)
//
// Each capture goes through the real React UI: navigation by clicking
// the left nav, drawers/modals opened by clicking the actual buttons.
// Sleeps between steps give animations + recharts + Cytoscape time to
// settle before we trigger the screenshot — these timeouts are
// conservative to favor "no flicker in the screenshot" over speed.

const path = require('path')
const fs = require('fs')
const { chromium } = require('playwright')

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173'
const OUT_DIR = path.resolve(__dirname, '..', 'docs', 'screenshots')

const VIEWPORT = { width: 1920, height: 1080 }
// Slight DPR > 1 gives sharper text/icons without bloating file size.
const DEVICE_SCALE_FACTOR = 1.5

fs.mkdirSync(OUT_DIR, { recursive: true })

async function sleep(ms) { await new Promise(r => setTimeout(r, ms)) }

async function clickNavByLabel(page, label) {
  // The left nav renders one button per page; each one's title attr
  // is the Chinese label, and there's a visible text node too.
  await page.locator(`nav button[title="${label}"]`).click()
}

async function clickByText(page, text, options = {}) {
  // First visible match. Some buttons appear in multiple places (e.g.
  // "全屏阅读" / "展开 PDF") — caller can narrow via `nth` if needed.
  const locator = page.getByText(text, { exact: false }).first()
  await locator.click(options)
}

async function captureAt(page, filename, opts = {}) {
  const file = path.join(OUT_DIR, filename)
  await page.screenshot({ path: file, fullPage: !!opts.fullPage })
  console.log(`  ✓ ${filename} (${fs.statSync(file).size} bytes)`)
}

async function main() {
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: DEVICE_SCALE_FACTOR,
  })
  const page = await context.newPage()

  // Suppress favicon / asset warnings in stdout.
  page.on('pageerror', err => console.warn('[page error]', err.message))

  console.log('→ navigating to', FRONTEND)
  await page.goto(FRONTEND, { waitUntil: 'domcontentloaded' })
  // Wait for the React tree + initial /api/* calls to finish.
  await page.waitForLoadState('networkidle')
  // Cytoscape needs a couple of layout passes before the canvas settles.
  await sleep(2500)

  // ── 01 知识 default ─────────────────────────────────────────────
  console.log('\n[01] 知识 · default')
  await captureAt(page, '01-graph-default.png')

  // ── 02 PipelineConsole expanded ────────────────────────────────
  // By default the "next step" stage is auto-expanded (often 筛选).
  // To get a screenshot that shows a different stage's controls, we
  // force-expand 编译 — clicking on a non-next-step stage overrides
  // the auto-expansion and reveals its CompileActions panel (paper
  // page + concept page rows with their compile CTAs).
  console.log('[02] 知识 · pipeline console expanded')
  try {
    await page.locator('section >> text=编译').first().click({ timeout: 3000 })
    await sleep(700)
  } catch (e) {
    console.warn('  · could not toggle 编译 stage:', e.message)
  }
  await captureAt(page, '02-graph-pipeline-console.png')
  // Click again to collapse so the next steps don't inherit this state.
  try {
    await page.locator('section >> text=编译').first().click({ timeout: 2000 })
    await sleep(400)
  } catch {}

  // ── 03 Ask drawer ──────────────────────────────────────────────
  console.log('[03] 知识 · Ask drawer')
  // Click the "Ask · 跨论文提问" button at the bottom of PipelineConsole.
  await page.locator('button:has-text("Ask")').first().click()
  await sleep(1500) // drawer slide + scroll into view
  await captureAt(page, '03-graph-ask-drawer.png')
  // The drawer's backdrop is a div with `onClick={onClose}` — easiest way
  // to close cleanly (Esc isn't bound). Locate by its distinctive class.
  await page.locator('div.bg-black\\/30.pointer-events-auto').click({ force: true })
  await sleep(600)

  // ── 04 Wiki lint modal ─────────────────────────────────────────
  console.log('[04] 知识 · wiki lint modal')
  // Make sure 健检 stage is expanded so the button is visible.
  try {
    await page.locator('section >> text=健检').first().click({ timeout: 3000 })
    await sleep(500)
  } catch (e) {
    console.warn('  · could not toggle 健检 stage:', e.message)
  }
  // The button label is either "运行健康检查" (first time) or
  // "查看 / 重跑健康检查" (report already exists). Match the prefix.
  const lintBtn = page.locator('button:has-text("健康检查")').first()
  await lintBtn.click()
  // Lint can take time to render the modal contents (it loads the
  // existing report). Give it a moment.
  await sleep(2500)
  await captureAt(page, '04-graph-lint-modal.png')
  // The lint modal doesn't bind Esc — close via its X button (the
  // first <button> inside the modal header, marked ml-auto).
  await page.locator('header:has-text("Wiki 健康检查") button').first().click()
  await sleep(600)

  // ── 05 Papers ──────────────────────────────────────────────────
  console.log('[05] 资料 · library')
  await clickNavByLabel(page, '资料')
  await page.waitForLoadState('networkidle')
  await sleep(1200)
  await captureAt(page, '05-papers.png')

  // ── 06 Review default ──────────────────────────────────────────
  console.log('[06] 回顾 · default')
  await clickNavByLabel(page, '回顾')
  await page.waitForLoadState('networkidle')
  await sleep(1500)
  await captureAt(page, '06-review-default.png')

  // ── 07 Review · PDF side panel open ───────────────────────────
  console.log('[07] 回顾 · PDF side panel')
  // Click the green "展开 PDF" button on the first-page preview block.
  const pdfBtn = page.locator('button:has-text("展开 PDF")').first()
  await pdfBtn.scrollIntoViewIfNeeded()
  await pdfBtn.click()
  // PDF needs time to load the worker + render at least the first page
  // before the screenshot is meaningful.
  await sleep(4500)
  await captureAt(page, '07-review-pdf-open.png')
  // Close
  await page.keyboard.press('Escape')
  await sleep(400)

  // ── 08 Dashboard ───────────────────────────────────────────────
  console.log('[08] 看板 · dashboard')
  await clickNavByLabel(page, '看板')
  await page.waitForLoadState('networkidle')
  // Recharts animates in; wait for radar + line + pies to settle.
  await sleep(2500)
  await captureAt(page, '08-dashboard.png')

  // ── 09 Settings ────────────────────────────────────────────────
  console.log('[09] 设置 · settings')
  await clickNavByLabel(page, '设置')
  await page.waitForLoadState('networkidle')
  await sleep(1200)
  // The "任务模型" section is collapsed by default — clicking its
  // "展开" toggle reveals the per-task model bindings, which is the
  // most informative thing on this page.
  try {
    // Locate the SettingGroup whose header contains "任务模型", then
    // its 展开 button.
    await page.locator('section:has-text("任务模型") button:has-text("展开")').first().click({ timeout: 3000 })
    await sleep(800) // settle expansion + lazy-loaded provider chips
  } catch (e) {
    console.warn('  · could not expand 任务模型:', e.message)
  }
  await captureAt(page, '09-settings.png')

  await browser.close()
  console.log('\n→ all screenshots saved to', OUT_DIR)
}

main().catch(err => {
  console.error('FATAL', err)
  process.exit(1)
})
