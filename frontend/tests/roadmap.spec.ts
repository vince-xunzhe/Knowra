import { test, expect, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import type { DomainAgentRun, DomainBoard, DomainPaper, DomainSnapshot, DomainState } from '../src/api/domains'
import type { RoadmapImportJob, RoadmapReport } from '../src/api/roadmaps'

const papers: DomainPaper[] = [
  {
    id: 'roadmap-transformer', title: 'Attention Is All You Need', year: '2017', category: 'LLM',
    eligible: true, reason: null, incomplete: false,
    sections: { core_contribution: 'A foundational attention architecture.', abstract_summary: 'The verified source abstract.', problem: '', motivation: '' },
    roadmap: { arxivId: '1706.03762', doi: null, sourceUrl: 'https://arxiv.org/abs/1706.03762', reason: 'A foundational attention architecture.', branch: 'Architecture', libraryPaperId: 'library-transformer', libraryStatus: 'in_library', downloadStatus: null, downloadError: null, reviewReady: false },
  },
  {
    id: 'roadmap-bert', title: 'BERT: Pre-training of Deep Bidirectional Transformers', year: '2018', category: 'LLM',
    eligible: true, reason: null, incomplete: false,
    sections: { core_contribution: 'Bidirectional pretraining.', abstract_summary: 'A verified pretraining abstract.', problem: '', motivation: '' },
    roadmap: { arxivId: '1810.04805', doi: null, sourceUrl: 'https://arxiv.org/abs/1810.04805', reason: 'Bidirectional pretraining.', branch: 'Pretraining', libraryPaperId: null, libraryStatus: 'not_imported', downloadStatus: null, downloadError: null, reviewReady: false },
  },
  {
    id: 'roadmap-classic', title: 'A classic journal paper without an arXiv copy', year: '1986', category: 'LLM',
    eligible: true, reason: null, incomplete: false,
    sections: { core_contribution: 'A historical foundation.', abstract_summary: '', problem: '', motivation: '' },
    roadmap: { arxivId: null, doi: '10.1038/323533a0', sourceUrl: 'https://doi.org/10.1038/323533a0', reason: 'A historical foundation.', branch: 'Foundations', libraryPaperId: null, libraryStatus: 'no_arxiv', downloadStatus: null, downloadError: null, reviewReady: false },
  },
]

function board(withPapers: boolean): DomainBoard {
  return {
    id: 'roadmap-llm', name: 'LLM', category: 'LLM', viewport: { x: 30, y: 30, zoom: 1 },
    nodes: withPapers ? papers.map((p, i) => ({ id: `node-${i}`, kind: 'paper', paperId: p.id, x: i === 1 ? 410 : 40, y: i === 2 ? 370 : 80, width: 280, height: 160, groupId: null, locked: false, text: '' })) : [],
    edges: withPapers ? [{ id: 'evolution', source: 'node-0', target: 'node-1', label: '基于', locked: false,
      analysis: { relation: 'builds_on', certainty: 'inferred', reason: 'Pretraining builds on attention architectures.',
        evidence: [{ paperId: 'roadmap-bert', source: 'text', locator: 'https://arxiv.org/abs/1810.04805', page: null, quote: 'A verified pretraining abstract.' }] },
    }] : [],
  }
}

async function setup(page: Page, populated = false) {
  let state: DomainState = { activeId: 'roadmap-llm', boards: [board(populated), { ...board(false), id: 'roadmap-vlm', name: 'VLM', category: 'VLM' }] }
  let revision = 1
  let candidates = populated ? structuredClone(papers) : []
  let runs: DomainAgentRun[] = []
  let importJob: RoadmapImportJob | null = null
  let importPosts = 0
  let researchPosts = 0
  let domainWrites = 0
  let appliedReport: RoadmapReport | null = populated ? { boardId: 'roadmap-llm', runId: 'previous-research', status: 'applied', markdown: '# Saved research report\n\n## Evidence gaps\n\nSome full texts were unavailable.' } : null
  let previewReport: RoadmapReport | null = null
  let reportFailures = 0
  const browserErrors: string[] = []
  page.on('pageerror', e => browserErrors.push(e.message))
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (!path.startsWith('/api/')) return route.continue()
    const method = route.request().method()
    let json: unknown = {}
    if (path === '/api/roadmaps') {
      if (method === 'PUT') {
        const body = route.request().postDataJSON() as DomainSnapshot
        if (body.revision !== revision) return route.fulfill({ status: 409, json: { detail: 'Conflict' } })
        state = body.state; revision++; json = { revision }
      } else json = { state, revision }
    } else if (path === '/api/roadmaps/papers') json = candidates
    else if (path === '/api/roadmaps/report') {
      if (reportFailures > 0) {
        reportFailures--
        return route.fulfill({ status: 503, json: { detail: 'Report temporarily unavailable' } })
      }
      json = url.searchParams.get('boardId') === 'roadmap-vlm' ? null : (runs[0]?.status === 'preview' ? previewReport : appliedReport)
    }
    else if (path === '/api/roadmaps/agent/runs') {
      if (method === 'POST') {
        researchPosts++
        const body = route.request().postDataJSON()
        const run: DomainAgentRun = { id: `research-${researchPosts}`, boardId: body.boardId, status: 'pending', jobId: 'research-job', error: null, proposal: null, progress: { label: '核验论文来源' } }
        runs = [run]; json = run
      } else json = runs
    } else if (path.startsWith('/api/roadmaps/agent/runs/') && path.endsWith('/apply')) {
      const before = structuredClone(state.boards[0])
      state.boards[0] = { ...runs[0].proposal!.board, viewport: before.viewport }
      revision++; runs = [{ ...runs[0], status: 'applied', proposal: null }]
      if (previewReport) appliedReport = { ...previewReport, status: 'applied' }
      json = { state, revision, before }
    } else if (path.startsWith('/api/roadmaps/agent/runs/') && path.endsWith('/discard')) {
      runs = [{ ...runs[0], status: 'discarded', proposal: null }]; json = runs[0]
      candidates = populated ? structuredClone(papers) : []
    } else if (path === '/api/roadmaps/imports') {
      if (method === 'POST') {
        importPosts++
        const body = route.request().postDataJSON()
        expect(body.boardId).toBe('roadmap-llm')
        expect(body.revision).toBe(revision)
        importJob = { job_id: `import-${importPosts}`, kind: 'roadmap_import', channel: 'roadmap_import:roadmap-llm', status: 'queued', error: null, result: null, progress: { label: '等待下载', total: 2, done: 0, imported: 0, skipped: 0, failed: 0 } }
        candidates[1].roadmap!.downloadStatus = 'queued'
        candidates[1].roadmap!.downloadError = null
      }
      json = url.searchParams.get('boardId') === 'roadmap-vlm' ? null : importJob
    } else if (path === '/api/domains') {
      if (method === 'PUT') domainWrites++
      json = { state: { activeId: 'domain-llm', boards: [{ ...board(false), id: 'domain-llm' }] }, revision: 1 }
    } else if (path === '/api/domains/papers' || path === '/api/domains/agent/runs') json = []
    else if (path === '/api/graph') json = { nodes: [], edges: [] }
    else if (path === '/api/papers') json = [{ id: 'library-transformer', title: papers[0].title, processed: false, filename: 'transformer.pdf', authors: [], paper_category: 'LLM', status: 'pending' }]
    else if (path === '/api/papers/library-transformer') json = { id: 'library-transformer', title: papers[0].title, processed: false, filename: 'transformer.pdf', authors: [], paper_category: 'LLM', knowledge_nodes: [], extraction: null, notes: '', chat: { messages: [] } }
    else if (path === '/api/paper-categories') json = { categories: [{ name: 'LLM' }, { name: 'VLM' }] }
    else if (path === '/api/paper-teams') json = { teams: [] }
    else if (path === '/api/status') json = { running: false, total: 0, done: 0 }
    else if (path === '/api/jobs') json = []
    else if (path === '/api/promotion/counts') json = { summary: { counts: { pending: 0, promoted: 0, rejected: 0 }, by: { llm: 0, user: 0, heuristic: 0 } } }
    else if (path === '/api/wiki/freshness') json = { papers: { total: 0 }, concepts: { total: 0 } }
    else if (path === '/api/wiki/lint/job' || path === '/api/wiki/status') json = { running: false, phase: 'idle' }
    else if (path === '/api/prompt/preferences') json = { locale: 'zh' }
    await route.fulfill({ json })
  })
  await page.goto('/')
  await page.getByRole('navigation').getByRole('button', { name: '识途', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '领域', exact: true })).toHaveValue('roadmap-llm')
  return {
    state: () => state, errors: () => browserErrors, importPosts: () => importPosts, domainWrites: () => domainWrites,
    failReportOnce: () => { reportFailures++ },
    savedReport(markdown: string) {
      appliedReport = { boardId: 'roadmap-llm', runId: 'previous-research', status: 'applied', markdown }
    },
    preview(reason?: string, markdown = '# Preview research report\n\n## Screening\n\nSelected sources.') {
      candidates = structuredClone(papers)
      if (reason) candidates[0].roadmap!.reason = reason
      runs = [{ ...runs[0], status: 'preview', proposal: { board: board(true), added: 1, removed: 0, protected: 0, rejected: 0, warnings: [], unconnected: [] } }]
      previewReport = { boardId: 'roadmap-llm', runId: runs[0].id, status: 'preview', markdown }
    },
    fullTextEvidence(withPage = true) {
      const analysis = state.boards[0].edges[0].analysis!
      analysis.certainty = 'explicit'
      analysis.evidence[0] = { ...analysis.evidence[0], locator: `https://arxiv.org/pdf/1810.04805${withPage ? '#page=3' : '#fulltext'}`, page: withPage ? 3 : null, quote: 'An excerpt supporting the relationship.' }
    },
    finishImport(failed: boolean) {
      const progress = { total: 2, done: 2, imported: failed ? 0 : 1, skipped: 1, failed: failed ? 1 : 0 }
      importJob = { ...importJob!, status: 'completed', progress, result: progress }
      const meta = candidates[1].roadmap!
      meta.downloadStatus = failed ? 'failed' : null
      meta.downloadError = failed ? 'Temporary arXiv error' : null
      if (!failed) { meta.libraryPaperId = 'library-bert'; meta.libraryStatus = 'in_library' }
    },
  }
}

test('research starts from an empty field, previews source-backed papers, and applies an editable roadmap', async ({ page }) => {
  const app = await setup(page)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  await roadmap.getByRole('button', { name: '生成路线图', exact: true }).click()
  await expect(roadmap.getByText('核验论文来源', { exact: false })).toBeVisible()
  await expect(roadmap.getByRole('button', { name: '添加独立文本' })).toBeDisabled()
  app.preview()
  await expect(roadmap.getByText('路线图预览', { exact: false })).toBeVisible()
  await expect(roadmap.getByRole('heading', { name: papers[1].title, exact: true })).toBeVisible()
  await expect(roadmap.getByRole('button', { name: '一键下载 arXiv' })).toBeDisabled()
  await roadmap.getByRole('button', { name: '应用方案', exact: true }).click()
  await expect(roadmap.getByRole('button', { name: '添加独立文本' })).toBeEnabled()
  await expect(roadmap.getByTestId('roadmap-imported-count')).toHaveText('共 3 篇 · 已入库 1 篇 · 非 arXiv 1 篇')
  const externalPaper = roadmap.locator('[data-node-id="node-2"]')
  await expect(externalPaper.getByText('非 arXiv · 仅来源')).toBeVisible()
  await expect(externalPaper.getByRole('link')).toHaveAttribute('href', papers[2].roadmap!.sourceUrl)
  await expect(externalPaper.getByRole('button', { name: `打开完整回顾：${papers[2].title}`, exact: true })).toBeDisabled()
  await externalPaper.getByRole('heading').click()
  await roadmap.getByRole('textbox', { name: '卡片备注', exact: true }).fill('Keep this historical foundation')
  await expect.poll(() => app.state().boards[0].nodes[2].text).toBe('Keep this historical foundation')
  await roadmap.getByRole('button', { name: '撤销', exact: true }).click()
  await expect(roadmap.getByRole('textbox', { name: '卡片备注', exact: true })).toHaveValue('')
  await roadmap.getByRole('button', { name: '锁定连线', exact: true }).click()
  await expect(roadmap.getByText('关系为 AI 推断；请结合所列证据与报告中的全文缺口判断。')).toBeVisible()
  await roadmap.getByText('证据 1 · 来源元数据', { exact: true }).click()
  await expect(roadmap.locator('.domain-edge-evidence').getByRole('link', { name: '查看论文来源' })).toHaveAttribute('href', papers[1].roadmap!.sourceUrl)
  await expect(roadmap.locator('.domain-edge-evidence').getByRole('button', { name: '打开完整回顾' })).toHaveCount(0)
  expect(app.domainWrites()).toBe(0)
  expect(app.errors()).toEqual([])
})

test('bulk import exposes partial failures, retries only missing papers, and preserves editable canvas', async ({ page }) => {
  const app = await setup(page, true)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  await roadmap.getByRole('button', { name: '一键下载 arXiv', exact: true }).click()
  await expect(roadmap.getByRole('button', { name: '正在下载入库', exact: true })).toBeDisabled()
  await roadmap.locator('[data-node-id="node-1"] h3').click()
  await roadmap.getByRole('textbox', { name: '卡片备注', exact: true }).fill('Editable while downloading')
  app.finishImport(true)
  await expect(roadmap.getByText('本次下载：入库 0 篇 · 已有 1 篇 · 失败 1 篇')).toBeVisible()
  await expect(roadmap.locator('[data-node-id="node-1"]').getByText('下载失败')).toBeVisible()
  await roadmap.getByRole('button', { name: '一键下载 arXiv', exact: true }).click()
  await expect.poll(app.importPosts).toBe(2)
  app.finishImport(false)
  await expect(roadmap.getByTestId('roadmap-imported-count')).toHaveText('共 3 篇 · 已入库 2 篇 · 非 arXiv 1 篇')
  await expect(roadmap.getByRole('button', { name: '一键下载 arXiv', exact: true })).toBeDisabled()
  await expect(roadmap.getByRole('textbox', { name: '卡片备注', exact: true })).toHaveValue('Editable while downloading')
  expect(app.state().boards[0].nodes[1].text).toBe('Editable while downloading')
  expect(app.state().boards[0].nodes[1].x).toBe(410)
  await roadmap.getByRole('button', { name: `查看调研信息：${papers[0].title}`, exact: true }).click()
  await expect(roadmap.getByTestId('roadmap-review-popover').getByText('已入库，尚未生成论文回顾；可在资料页处理。')).toBeVisible()
  const request = page.waitForRequest('**/api/papers/library-transformer')
  await roadmap.getByTestId('roadmap-review-popover').getByRole('button', { name: '打开完整回顾', exact: false }).click()
  await request
  await expect(page.getByRole('heading', { name: papers[0].title, exact: true })).toBeVisible()
  await page.getByRole('button', { name: '返回识途', exact: true }).click()
  await expect(roadmap.locator('[data-node-id="node-1"]')).toContainText('Editable while downloading')
  await roadmap.getByRole('button', { name: '收起研究工作区', exact: true }).click()
  await page.screenshot({ path: '/tmp/knowra-shitu-roadmap.png' })
  expect(app.errors()).toEqual([])
})

test('roadmap and domain sessions keep separate persistence, panel preferences and SVG identifiers', async ({ page }) => {
  const app = await setup(page, true)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  await roadmap.getByRole('button', { name: '收起研究工作区', exact: true }).click()
  await roadmap.getByRole('button', { name: '缩小', exact: true }).click()
  await expect.poll(() => app.state().boards[0].viewport.zoom).toBe(0.8)
  await page.getByRole('navigation').getByRole('button', { name: '索骥', exact: true }).click()
  const domain = page.getByRole('region', { name: '索骥', exact: true })
  await expect(domain.getByRole('button', { name: '收起研究工作区', exact: true })).toBeVisible()
  await expect(domain.getByTestId('domain-canvas').locator('[data-kind="paper"]')).toHaveCount(0)
  await expect(page.locator('#domain-arrow')).toHaveCount(1)
  await expect(page.locator('#roadmap-arrow')).toHaveCount(1)
  await page.getByRole('navigation').getByRole('button', { name: '识途', exact: true }).click()
  await expect(roadmap.getByRole('button', { name: '展开研究工作区', exact: true })).toBeVisible()
  await expect(roadmap.locator('.domain-view-tools')).toContainText('80%')
  await roadmap.locator('[data-node-id="node-1"] h3').click()
  await expect(roadmap.getByRole('button', { name: '收起研究工作区', exact: true })).toBeVisible()
  await roadmap.getByRole('combobox', { name: '领域', exact: true }).selectOption('roadmap-vlm')
  await expect(roadmap.getByTestId('roadmap-canvas').locator('[data-kind="paper"]')).toHaveCount(0)
  await expect(roadmap.getByRole('button', { name: '生成路线图', exact: true })).toBeEnabled()
  await roadmap.getByRole('combobox', { name: '领域', exact: true }).selectOption('roadmap-llm')
  await expect(roadmap.locator('.domain-view-tools')).toContainText('80%')
  expect(app.domainWrites()).toBe(0)
  expect(app.errors()).toEqual([])
})

test('discarding new research restores saved descriptions and leaves the existing roadmap intact', async ({ page }) => {
  const app = await setup(page, true)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  const before = structuredClone(app.state().boards[0])
  await roadmap.getByRole('button', { name: '重新调研', exact: true }).click()
  await expect(roadmap.getByText('核验论文来源', { exact: false })).toBeVisible()
  app.preview('A proposed replacement explanation')
  await expect(roadmap.getByText('路线图预览', { exact: false })).toBeVisible()
  await roadmap.locator('[data-node-id="node-0"] h3').click()
  await expect(roadmap.getByText('A proposed replacement explanation', { exact: true })).toBeVisible()
  await roadmap.getByRole('button', { name: '放弃方案', exact: true }).click()
  await expect(roadmap.getByText(papers[0].roadmap!.reason, { exact: true })).toBeVisible()
  await expect(roadmap.getByText('A proposed replacement explanation', { exact: true })).toHaveCount(0)
  expect(app.state().boards[0]).toEqual(before)
  expect(app.errors()).toEqual([])
})

test('inline research reports refresh previews, expand safely, download Markdown, and restore applied reports after discard', async ({ page }) => {
  const app = await setup(page, true)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  const report = roadmap.getByRole('tabpanel', { name: '总体调研报告', exact: true })
  const reportTab = roadmap.getByRole('tab', { name: '总体调研报告', exact: true })
  const canvasTab = roadmap.getByRole('tab', { name: '路线图', exact: true })
  const dialog = page.getByRole('dialog', { name: '调研报告', exact: true })
  await expect(canvasTab).toHaveAttribute('aria-selected', 'true')
  await reportTab.click()
  await expect(report).toBeVisible()
  await expect(report.getByRole('heading', { name: 'Saved research report', exact: true })).toBeVisible()
  await expect(report.getByText('Some full texts were unavailable.', { exact: true })).toBeVisible()
  await expect(dialog).toHaveCount(0)
  await report.getByRole('button', { name: '展开报告', exact: true }).click()
  await expect(dialog.getByRole('heading', { name: 'Saved research report', exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(report.getByRole('button', { name: '展开报告', exact: true })).toBeFocused()
  await canvasTab.click()
  await roadmap.getByRole('button', { name: '重新调研', exact: true }).click()
  await expect(roadmap.getByText('核验论文来源', { exact: false })).toBeVisible()
  await reportTab.click()
  await expect(report.getByRole('heading', { name: 'Saved research report', exact: true })).toBeVisible()
  const markdown = '# LLM 技术方向总体调研报告\n\n## 1. 范围与问题\n\n梳理注意力架构与预训练的发展脉络，区分论文作者的结论与调研推断。\n\n## 2. 检索与来源核验\n\n[Verified source](https://arxiv.org/abs/1810.04805)\n\n[Unsafe](javascript:alert%281%29)\n\n<img src="https://example.com/tracker.png">\n\n## 3. 初筛与入选理由\n\n| 论文 | 年份 | 来源 | 阅读覆盖 |\n| --- | --- | --- | --- |\n| Attention Is All You Need | 2017 | arXiv | 摘要与正文 |\n| BERT | 2018 | arXiv | 摘要与正文 |\n| 经典期刊论文 | 1986 | DOI | 仅摘要 |\n\n## 4. 关键论文全文评估\n\n经典期刊论文全文未获取，未据此推断具体实验结果。\n\n## 5. 关系与证据等级\n\n架构继承关系需要正文证据支持，时间顺序本身不能证明继承。\n\n## 6. 发展分支与路线图\n\n按架构设计、预训练方法整理分支，允许保留独立节点。\n\n## 7. 缺口与后续阅读\n\nFull text unavailable for a historical paper.\n' + Array.from({ length: 12 }, (_, i) => `\n\n### 论文评估记录 ${i + 1}\n\n记录研究问题、作者贡献、证据定位与未能核实的结论。`).join('')
  app.preview(undefined, markdown)
  await expect(report.getByRole('heading', { name: 'LLM 技术方向总体调研报告', exact: true })).toBeVisible()
  await expect(report.getByText('预览报告 · 应用方案后保存', { exact: true })).toBeVisible()
  await expect(report.getByRole('link', { name: 'Verified source' })).toHaveAttribute('href', papers[1].roadmap!.sourceUrl)
  await expect(report.getByRole('link', { name: 'Unsafe', exact: true })).toHaveCount(0)
  await expect(report.locator('img')).toHaveCount(0)
  await expect(report.getByRole('table')).toBeVisible()
  await expect.poll(() => report.locator('.roadmap-report-body').evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true)
  await page.screenshot({ path: '/tmp/knowra-shitu-inline-report.png' })
  const downloadPromise = page.waitForEvent('download')
  await report.getByRole('button', { name: '下载 Markdown', exact: true }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('LLM-research.md')
  expect(await readFile((await download.path())!, 'utf8')).toBe(markdown)
  await report.getByRole('button', { name: '展开报告', exact: true }).click()
  await expect(dialog.getByRole('heading', { name: 'LLM 技术方向总体调研报告', exact: true })).toBeVisible()
  await expect(dialog.getByText('预览报告 · 应用方案后保存', { exact: true })).toBeVisible()
  await expect(dialog.getByRole('link', { name: 'Verified source' })).toHaveAttribute('href', papers[1].roadmap!.sourceUrl)
  await expect(dialog.getByRole('link', { name: 'Unsafe', exact: true })).toHaveCount(0)
  await expect(dialog.locator('img')).toHaveCount(0)
  await expect.poll(() => dialog.locator('.roadmap-report-body').evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true)
  await page.screenshot({ path: '/tmp/knowra-shitu-research-report.png' })
  const expandedDownloadPromise = page.waitForEvent('download')
  await dialog.getByRole('button', { name: '下载 Markdown', exact: true }).click()
  const expandedDownload = await expandedDownloadPromise
  expect(await readFile((await expandedDownload.path())!, 'utf8')).toBe(markdown)
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
  await canvasTab.click()
  await roadmap.getByRole('button', { name: '放弃方案', exact: true }).click()
  await reportTab.click()
  await expect(report.getByRole('heading', { name: 'Saved research report', exact: true })).toBeVisible()
  await expect(report.getByText('已应用方案的调研报告', { exact: true })).toBeVisible()
  await canvasTab.click()
  await roadmap.getByRole('button', { name: '重新调研', exact: true }).click()
  await expect(roadmap.getByText('核验论文来源', { exact: false })).toBeVisible()
  app.preview(undefined, markdown)
  await expect(roadmap.getByText('路线图预览', { exact: false })).toBeVisible()
  await roadmap.getByRole('button', { name: '应用方案', exact: true }).click()
  await reportTab.click()
  await expect(report.getByText('已应用方案的调研报告', { exact: true })).toBeVisible()
  await expect(report.getByRole('heading', { name: 'LLM 技术方向总体调研报告', exact: true })).toBeVisible()
  expect(app.errors()).toEqual([])
})

test('report view handles empty categories and retries loading failures without showing another field’s report', async ({ page }) => {
  const app = await setup(page, true)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  const report = roadmap.getByRole('tabpanel', { name: '总体调研报告', exact: true })
  await roadmap.getByRole('tab', { name: '总体调研报告', exact: true }).click()
  await expect(report.getByRole('heading', { name: 'Saved research report', exact: true })).toBeVisible()
  await roadmap.getByRole('combobox', { name: '领域', exact: true }).selectOption('roadmap-vlm')
  await expect(report.getByRole('heading', { name: 'VLM · 总体调研报告', exact: true })).toBeVisible()
  await expect(report.getByText('暂无调研报告。请先生成路线图，完成后可查看和下载。', { exact: true })).toBeVisible()
  await expect(report.getByRole('heading', { name: 'Saved research report', exact: true })).toHaveCount(0)
  await expect(report.getByRole('button', { name: '展开报告', exact: true })).toBeDisabled()
  await expect(report.getByRole('button', { name: '下载 Markdown', exact: true })).toBeDisabled()
  app.failReportOnce()
  await roadmap.getByRole('combobox', { name: '领域', exact: true }).selectOption('roadmap-llm')
  await expect(report.getByRole('alert')).toContainText('调研报告加载失败，请重试。')
  await expect(report.getByRole('button', { name: '下载 Markdown', exact: true })).toBeDisabled()
  await report.getByRole('button', { name: '重试', exact: true }).click()
  await expect(report.getByRole('heading', { name: 'Saved research report', exact: true })).toBeVisible()
  await expect(report.getByRole('button', { name: '下载 Markdown', exact: true })).toBeEnabled()
  await roadmap.getByRole('tab', { name: '路线图', exact: true }).click()
  await expect(roadmap.getByTestId('roadmap-canvas')).toBeVisible()
  expect(app.errors()).toEqual([])
})

test('reading the report preserves the mounted canvas, selection and undo history while disabling canvas shortcuts', async ({ page }) => {
  const app = await setup(page, true)
  app.savedReport('# A long research report\n\n' + Array.from({ length: 35 }, (_, i) => `## Paper ${i + 1}\n\nResearch evidence and limitations.\n\n`).join(''))
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  const report = roadmap.getByRole('tabpanel', { name: '总体调研报告', exact: true })
  const canvas = roadmap.getByTestId('roadmap-canvas')
  const node = canvas.locator('[data-node-id="node-1"]')
  const reportTab = roadmap.getByRole('tab', { name: '总体调研报告', exact: true })
  const canvasTab = roadmap.getByRole('tab', { name: '路线图', exact: true })
  await roadmap.getByRole('button', { name: '缩小', exact: true }).click()
  await expect.poll(() => app.state().boards[0].viewport.zoom).toBe(0.8)
  await node.getByRole('heading').click()
  await roadmap.getByRole('textbox', { name: '卡片备注', exact: true }).fill('Preserve this research note')
  await expect.poll(() => app.state().boards[0].nodes[1].text).toBe('Preserve this research note')
  const before = structuredClone(app.state().boards[0])
  const originalBounds = await node.boundingBox()
  await reportTab.click()
  await expect(report.getByRole('heading', { name: 'A long research report', exact: true })).toBeVisible()
  await expect(canvas).toHaveCount(1)
  await expect(canvas).toBeHidden()
  await expect(roadmap.getByRole('button', { name: '收起研究工作区', exact: true })).toHaveCount(0)
  const scrollBody = report.locator('.roadmap-report-body')
  await scrollBody.focus()
  await page.keyboard.press('Space')
  await expect.poll(() => scrollBody.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
  for (const key of ['Delete', 'Backspace', 'Control+z', 'Meta+z', 'Escape']) {
    await page.keyboard.press(key)
    await expect(canvas.locator('[data-kind="paper"]')).toHaveCount(3)
    await expect(node).toHaveAttribute('data-selected', 'true')
    await expect(node).toContainText('Preserve this research note')
  }
  await reportTab.focus()
  await page.keyboard.press('ArrowLeft')
  await expect(canvasTab).toBeFocused()
  await expect(canvasTab).toHaveAttribute('aria-selected', 'true')
  await expect(canvas).toBeVisible()
  await expect(node).toHaveAttribute('data-selected', 'true')
  await expect(roadmap.getByRole('textbox', { name: '卡片备注', exact: true })).toHaveValue('Preserve this research note')
  await expect(roadmap.locator('.domain-view-tools')).toContainText('80%')
  expect(await node.boundingBox()).toEqual(originalBounds)
  expect(app.state().boards[0]).toEqual(before)
  await roadmap.getByRole('button', { name: '撤销', exact: true }).click()
  await expect(roadmap.getByRole('textbox', { name: '卡片备注', exact: true })).toHaveValue('')
  expect(app.errors()).toEqual([])
})

for (const withPage of [true, false]) test(`full-text-supported connections show explicit evidence ${withPage ? 'with a PDF page' : 'without invented page numbers'}`, async ({ page }) => {
  const app = await setup(page, true)
  app.fullTextEvidence(withPage)
  await page.reload()
  await page.getByRole('navigation').getByRole('button', { name: '识途', exact: true }).click()
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  await roadmap.getByRole('button', { name: '锁定连线', exact: true }).click()
  await expect(roadmap.getByRole('heading', { name: '连线解读 · 原文明示', exact: true })).toBeVisible()
  await roadmap.getByText(withPage ? '证据 1 · 原文 · PDF 第 3 页' : '证据 1 · 原文全文 · 页码未知', { exact: true }).click()
  await expect(roadmap.locator('.domain-edge-evidence').getByRole('link', { name: '查看论文来源' })).toHaveAttribute('href', `https://arxiv.org/pdf/1810.04805${withPage ? '#page=3' : '#fulltext'}`)
  await expect(roadmap.getByText('An excerpt supporting the relationship.', { exact: true })).toBeVisible()
  expect(app.errors()).toEqual([])
})
