import { test, expect, type Locator, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { Buffer } from 'node:buffer'
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
  let domainState: DomainState = {
    activeId: 'domain-llm',
    boards: [
      { ...board(false), id: 'domain-llm' },
      { ...board(false), id: 'domain-vlm', name: 'VLM', category: 'VLM' },
    ],
  }
  let domainRevision = 1
  const categories = ['LLM', 'VLM']
  let revision = 1
  let candidates = populated ? structuredClone(papers) : []
  let runs: DomainAgentRun[] = []
  let importJob: RoadmapImportJob | null = null
  let importPosts = 0
  let lastImportPaperIds: string[] | undefined
  let researchPosts = 0
  let layoutPosts = 0
  let lastLayoutOrientation: string | undefined
  let lastResearchPolicy: string | undefined
  let lastResearchKeywords: string[] | undefined
  let domainWrites = 0
  let appliedReport: RoadmapReport | null = populated ? { boardId: 'roadmap-llm', runId: 'previous-research', status: 'applied', markdown: '# Saved research report\n\n## Evidence gaps\n\nSome full texts were unavailable.' } : null
  let previewReport: RoadmapReport | null = null
  let reportFailures = 0
  let exportFailures = 0
  let exportRequests = 0
  let saveAttempts = 0
  let zoomMinimum = 0.05
  let saveFailure: { status: number; detail: unknown } | null = null
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
        saveAttempts++
        const body = route.request().postDataJSON() as DomainSnapshot
        if (saveFailure) return route.fulfill({ status: saveFailure.status, json: { detail: saveFailure.detail } })
        const invalidBoard = body.state.boards.findIndex(b => b.viewport.zoom < zoomMinimum)
        if (invalidBoard >= 0) return route.fulfill({ status: 422, json: { detail: [{
          type: 'greater_than_equal', loc: ['body', 'state', 'boards', invalidBoard, 'viewport', 'zoom'],
          msg: `Input should be greater than or equal to ${zoomMinimum}`, input: body.state.boards[invalidBoard].viewport.zoom,
          ctx: { ge: zoomMinimum },
        }] } })
        if (body.revision !== revision) return route.fulfill({ status: 409, json: { detail: 'Conflict' } })
        state = body.state; revision++; json = { revision }
      } else json = { state, revision }
    } else if (path === '/api/roadmaps/papers') json = candidates
    else if (path === '/api/roadmaps/layout') {
      layoutPosts++
      const body = route.request().postDataJSON()
      expect(body.revision).toBe(revision)
      lastLayoutOrientation = body.orientation
      const before = structuredClone(state.boards[0])
      state.boards[0] = { ...before, nodes: before.nodes.map(node => ({
        ...node,
        ...(body.orientation === 'vertical'
          ? { x: node.roadmapRole === 'topic' ? 80 : node.roadmapRole === 'branch' ? 520 : node.x + 900 }
          : { y: node.roadmapRole === 'topic' ? 80 : node.roadmapRole === 'branch' ? 520 : node.y + 900 }),
      })) }
      revision++
      json = { state, revision, before }
    }
    else if (path === '/api/roadmaps/report') {
      if (reportFailures > 0) {
        reportFailures--
        return route.fulfill({ status: 503, json: { detail: 'Report temporarily unavailable' } })
      }
      json = url.searchParams.get('boardId') === 'roadmap-vlm' ? null : (runs[0]?.status === 'preview' ? previewReport : appliedReport)
    }
    else if (path === '/api/roadmaps/report/export') {
      exportRequests++
      expect(url.searchParams.get('boardId')).toBe('roadmap-llm')
      if (exportFailures > 0) {
        exportFailures--
        return route.fulfill({ status: 409, json: { detail: 'Cached figure unavailable' } })
      }
      return route.fulfill({ contentType: 'application/zip', body: Buffer.from('PK\u0003\u0004mock-report-and-figure') })
    }
    else if (/^\/api\/roadmaps\/report-assets\/[a-f0-9]{64}\.png$/.test(path)) {
      return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aS1sAAAAASUVORK5CYII=', 'base64') })
    }
    else if (path === '/api/roadmaps/agent/runs') {
      if (method === 'POST') {
        researchPosts++
        const body = route.request().postDataJSON()
        expect(body.revision).toBe(revision)
        lastResearchPolicy = body.relationPolicy
        lastResearchKeywords = structuredClone(
          state.boards.find(board => board.id === body.boardId)?.researchKeywords ?? [],
        )
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
        lastImportPaperIds = body.paperIds
        const selected = body.paperIds?.length ? candidates.filter(candidate => body.paperIds.includes(candidate.id))
          : candidates.filter(candidate => candidate.roadmap?.arxivId)
        importJob = { job_id: `import-${importPosts}`, kind: 'roadmap_import', channel: 'roadmap_import:roadmap-llm', status: 'queued', error: null, result: null, progress: { label: '等待下载', total: selected.length, done: 0, imported: 0, skipped: 0, failed: 0 } }
        for (const candidate of selected) {
          if (candidate.roadmap?.libraryStatus !== 'in_library') candidate.roadmap!.downloadStatus = 'queued'
          candidate.roadmap!.downloadError = null
        }
      }
      json = url.searchParams.get('boardId') === 'roadmap-vlm' ? null : importJob
    } else if (path === '/api/domains') {
      if (method === 'PUT') {
        domainWrites++
        const body = route.request().postDataJSON() as DomainSnapshot
        domainState = body.state
        domainRevision++
      }
      json = { state: domainState, revision: domainRevision }
    } else if (path === '/api/domains/papers' || path === '/api/domains/agent/runs') json = []
    else if (path === '/api/graph') json = { nodes: [], edges: [] }
    else if (path === '/api/papers') json = [{ id: 'library-transformer', title: papers[0].title, processed: false, filename: 'transformer.pdf', authors: [], paper_category: 'LLM', status: 'pending' }]
    else if (path === '/api/papers/library-transformer') json = { id: 'library-transformer', title: papers[0].title, processed: false, filename: 'transformer.pdf', authors: [], paper_category: 'LLM', knowledge_nodes: [], extraction: null, notes: '', chat: { messages: [] } }
    else if (path.startsWith('/api/paper-categories')) {
      if (method === 'POST') {
        const name = String(route.request().postDataJSON().name).trim()
        if (!categories.includes(name)) categories.push(name)
        if (!state.boards.some(item => item.category === name)) {
          state = {
            ...state,
            boards: [...state.boards, {
              ...board(false),
              id: `roadmap-${name}`,
              name,
              category: name,
            }],
          }
          revision++
        }
        if (!domainState.boards.some(item => item.category === name)) {
          domainState = {
            ...domainState,
            boards: [...domainState.boards, {
              ...board(false),
              id: `domain-${name}`,
              name,
              category: name,
            }],
          }
          domainRevision++
        }
      }
      json = {
        categories: categories.map(name => ({
          name,
          builtin: false,
          removable: true,
          count: name === 'LLM' ? 1 : 0,
        })),
        migrated: 0,
      }
    }
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
    state: () => state, errors: () => browserErrors, importPosts: () => importPosts,
    lastImportPaperIds: () => lastImportPaperIds, domainWrites: () => domainWrites,
    saveAttempts: () => saveAttempts, researchPosts: () => researchPosts, layoutPosts: () => layoutPosts,
    lastLayoutOrientation: () => lastLayoutOrientation,
    lastResearchPolicy: () => lastResearchPolicy,
    lastResearchKeywords: () => lastResearchKeywords,
    zoomMinimum: (minimum: number) => { zoomMinimum = minimum },
    conflict: () => { revision++ },
    failSave: (failure: typeof saveFailure) => { saveFailure = failure },
    failReportOnce: () => { reportFailures++ },
    failExportOnce: () => { exportFailures++ },
    exportRequests: () => exportRequests,
    savedReport(markdown: string, hasAssets = false) {
      appliedReport = { boardId: 'roadmap-llm', runId: 'previous-research', status: 'applied', markdown, hasAssets }
    },
    developmentTree() {
      state.boards[0] = {
        ...board(true), viewport: { x: 30, y: 0, zoom: 1 },
        nodes: [
          { id: 'topic', kind: 'text', roadmapRole: 'topic', text: '语言模型发展', x: 200, y: 70, width: 280, height: 140, paperId: null, groupId: null, locked: false },
          { id: 'branch', kind: 'text', roadmapRole: 'branch', text: '注意力架构', x: 200, y: 280, width: 280, height: 130, paperId: null, groupId: null, locked: false },
          ...board(true).nodes.map((n, i) => ({ ...n, x: i * 340 + 30, y: 490, roadmapRole: i === 0 ? 'milestone' as const : null })),
        ],
        edges: [
          ...board(true).edges.map(e => ({ ...e, label: '演进', analysis: { ...e.analysis!, relation: 'evolves_into' as const } })),
          { id: 'topic-branch', source: 'topic', target: 'branch', label: '研究分支', locked: false, roadmapStructure: true, analysis: null },
          { id: 'branch-paper', source: 'branch', target: 'node-0', label: '代表论文', locked: false, roadmapStructure: true, analysis: null },
        ],
      }
    },
    preview(reason?: string, markdown = '# Preview research report\n\n## Screening\n\nSelected sources.', proposalBoard = board(true)) {
      candidates = structuredClone(papers)
      if (reason) candidates[0].roadmap!.reason = reason
      runs = [{ ...runs[0], status: 'preview', proposal: { board: proposalBoard, added: 1, removed: 0, protected: 0, rejected: 0, warnings: [], unconnected: [] } }]
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

async function researchAgain(roadmap: Locator, choice: '清空并重新生成' | '保留历史关系' = '清空并重新生成') {
  await roadmap.getByRole('button', { name: '重新调研', exact: true }).click()
  const dialog = roadmap.getByRole('dialog', { name: '重新调研', exact: true })
  await expect(dialog.getByRole('heading', { name: '是否需要清空历史关系？', exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: choice, exact: false }).click()
}

test('roadmap navigation uses a mind-map icon and keeps the canvas close to the top', async ({ page }) => {
  await setup(page, true)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  const canvasTab = roadmap.getByRole('tab', { name: '路线图', exact: true })
  await expect(canvasTab.locator('svg.lucide-workflow')).toBeVisible()
  await expect(roadmap.getByRole('tab', { name: '研报', exact: true })).toBeVisible()

  const [headerBox, noticeBox, canvasBox] = await Promise.all([
    roadmap.locator('.domain-header').boundingBox(),
    roadmap.locator('.roadmap-research-notice').boundingBox(),
    roadmap.getByTestId('roadmap-canvas').boundingBox(),
  ])
  expect(headerBox).not.toBeNull()
  expect(noticeBox).not.toBeNull()
  expect(canvasBox).not.toBeNull()
  expect(headerBox!.height).toBeLessThanOrEqual(64)
  expect(noticeBox!.height).toBeLessThanOrEqual(32)
  expect(canvasBox!.y).toBeLessThanOrEqual(190)
})

test('a field created in roadmap is available globally, including empty library filters', async ({ page }) => {
  await setup(page)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  await roadmap.getByRole('button', { name: '新建领域', exact: true }).click()
  await roadmap.getByRole('textbox', { name: '领域名称', exact: true }).fill('具身智能数据')
  await roadmap.getByRole('button', { name: '保存', exact: true }).click()
  await expect(roadmap.getByRole('combobox', { name: '领域', exact: true }).locator('option:checked'))
    .toHaveText('具身智能数据')

  await page.getByRole('navigation').getByRole('button', { name: '索骥', exact: true }).click()
  await expect(page.getByRole('region', { name: '索骥', exact: true })
    .getByRole('combobox', { name: '领域', exact: true })
    .getByRole('option', { name: '具身智能数据', exact: true }))
    .toHaveCount(1)

  await page.getByRole('navigation').getByRole('button', { name: '资料', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '按论文大类筛选', exact: true })
    .getByRole('option', { name: '具身智能数据 · 0', exact: true }))
    .toHaveCount(1)
})

test('rerun asks whether to keep previous relationships before starting research', async ({ page }) => {
  const app = await setup(page, true)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  await roadmap.getByRole('button', { name: '重新调研', exact: true }).click()
  const dialog = roadmap.getByRole('dialog', { name: '重新调研', exact: true })
  await expect(dialog).toContainText('论文卡片、备注、文字和分组都会保留。已锁定连线始终受保护。')
  await expect(dialog.locator('.roadmap-research-choice-icon')).toHaveCount(2)
  await page.screenshot({ path: '/tmp/knowra-shitu-rerun-relations.png' })
  expect(app.researchPosts()).toBe(0)
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(app.researchPosts()).toBe(0)

  await researchAgain(roadmap, '保留历史关系')
  await expect(roadmap.getByText('核验论文来源', { exact: false })).toBeVisible()
  expect(app.researchPosts()).toBe(1)
  expect(app.lastResearchPolicy()).toBe('preserve')
})

test('focus keywords are saved with the field before roadmap research starts', async ({ page }) => {
  const app = await setup(page)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  const input = roadmap.getByRole('textbox', { name: '添加关注关键词', exact: true })

  await input.fill('3DGS')
  await input.press('Enter')
  await input.fill('4DGS')
  await input.press('Enter')

  await expect(roadmap.getByText('3DGS', { exact: true })).toBeVisible()
  await expect(roadmap.getByText('4DGS', { exact: true })).toBeVisible()
  await expect.poll(() => app.state().boards[0].researchKeywords).toEqual(['3DGS', '4DGS'])

  // Clicking research also commits the in-progress keyword through blur first.
  await input.fill('NeRF')
  await roadmap.getByRole('button', { name: '生成路线图', exact: true }).click()
  await expect.poll(app.researchPosts).toBe(1)
  expect(app.lastResearchKeywords()).toEqual(['3DGS', '4DGS', 'NeRF'])
  await expect(input).toBeDisabled()
  expect(app.errors()).toEqual([])
})

for (const recovery of ['research', 'alert'] as const) test(`an old backend rejecting 8% zoom recovers through ${recovery} retry without losing edits`, async ({ page }) => {
  const app = await setup(page, true)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  const status = roadmap.locator('.domain-save')
  const guidance = '当前后端仍使用旧缩放限制，请重启本地后端后重试保存；请保留本页。'
  app.zoomMinimum(0.15)
  await roadmap.locator('[data-node-id="node-0"] h3').click()
  const note = roadmap.getByRole('textbox', { name: '卡片备注', exact: true })
  await note.fill('Keep my unsaved research note')
  await roadmap.getByTestId('roadmap-canvas').dispatchEvent('wheel', {
    ctrlKey: true, deltaY: Math.log(1 / 0.08) / 0.008, clientX: 300, clientY: 300,
  })
  await expect(status).toContainText('保存失败，编辑仍保留在本页')
  await expect(status).toContainText(guidance)
  await expect(roadmap.getByText('8%', { exact: true })).toBeVisible()
  expect(app.saveAttempts()).toBe(1)
  expect(app.state().boards[0].nodes[0].text).toBe('')
  await researchAgain(roadmap)
  await expect(roadmap.getByRole('alert')).toContainText(guidance)
  expect(app.saveAttempts()).toBe(2)
  expect(app.researchPosts()).toBe(0)
  await expect(note).toHaveValue('Keep my unsaved research note')

  // The service now accepts the new schema; the same user action retries once.
  app.zoomMinimum(0.05)
  if (recovery === 'alert') {
    await roadmap.getByRole('alert').getByRole('button', { name: '重试', exact: true }).click()
    await expect(status).toContainText('已保存到本机')
    await expect(roadmap.getByRole('alert')).toHaveCount(0)
    expect(app.researchPosts()).toBe(0)
    expect(app.state().boards[0].nodes[0].text).toBe('Keep my unsaved research note')
  }
  await researchAgain(roadmap)
  await expect(roadmap.getByText('核验论文来源', { exact: false })).toBeVisible()
  await expect(status).toContainText('已保存到本机')
  await expect(status).not.toContainText(guidance)
  await expect(roadmap.getByRole('alert')).toHaveCount(0)
  expect(app.saveAttempts()).toBe(3)
  expect(app.researchPosts()).toBe(1)
  expect(app.state().boards[0].viewport.zoom).toBeCloseTo(0.08)
  expect(app.state().boards[0].nodes[0].text).toBe('Keep my unsaved research note')
  expect(app.errors()).toEqual([])
})

test('retrying research after a revision conflict never retries a write or replaces local edits', async ({ page }) => {
  const app = await setup(page, true)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  app.conflict()
  await roadmap.locator('[data-node-id="node-0"] h3').click()
  const note = roadmap.getByRole('textbox', { name: '卡片备注', exact: true })
  await note.fill('Retain the conflicting local edit')
  await expect(roadmap.locator('.domain-save')).toContainText('画布已在其他窗口更新，请重新加载')
  for (let attempt = 0; attempt < 2; attempt++) {
    await researchAgain(roadmap)
    await expect(roadmap.getByRole('alert')).toContainText('画布已在其他窗口更新，请重新加载')
    await expect(note).toHaveValue('Retain the conflicting local edit')
  }
  await roadmap.getByRole('alert').getByRole('button', { name: '重试', exact: true }).click()
  await expect(roadmap.getByRole('alert')).toHaveCount(0)
  await expect(roadmap.locator('.domain-save')).toContainText('画布已在其他窗口更新，请重新加载')
  await expect(note).toHaveValue('Retain the conflicting local edit')
  expect(app.saveAttempts()).toBe(1)
  expect(app.researchPosts()).toBe(0)
  expect(app.state().boards[0].nodes[0].text).toBe('')
  expect(app.errors()).toEqual([])
})

test('other save validation failures show a safe summary and explicit retry retains changes', async ({ page }) => {
  const app = await setup(page, true)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  const diagnostic = 'internal diagnostic that must not appear in the interface'
  app.failSave({ status: 422, detail: [{ loc: ['body', 'state'], msg: diagnostic }] })
  await roadmap.locator('[data-node-id="node-0"] h3').click()
  await roadmap.getByRole('textbox', { name: '卡片备注', exact: true }).fill('Preserved after validation failure')
  await expect(roadmap.locator('.domain-save')).toContainText('后端未接受画布数据，请保留本页并重试保存。')
  await expect(roadmap).not.toContainText(diagnostic)
  app.failSave(null)
  await roadmap.locator('.domain-save').getByRole('button', { name: '重试', exact: true }).click()
  await expect(roadmap.locator('.domain-save')).toContainText('已保存到本机')
  expect(app.state().boards[0].nodes[0].text).toBe('Preserved after validation failure')
  expect(app.errors()).toEqual([])
})

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

test('a selected arXiv paper can be collected into the library on its own', async ({ page }) => {
  const app = await setup(page, true)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })

  await roadmap.locator('[data-node-id="node-1"] h3').click()
  const collect = roadmap.getByRole('button', { name: '收集入库', exact: true })
  await expect(collect).toBeEnabled()
  await collect.click()

  await expect.poll(app.importPosts).toBe(1)
  expect(app.lastImportPaperIds()).toEqual(['roadmap-bert'])
  await expect(roadmap.getByRole('button', { name: '正在收集', exact: true })).toBeDisabled()

  app.finishImport(false)
  await expect(roadmap.getByText('已入库，尚未生成论文回顾；可在资料页处理。')).toBeVisible()
  await expect(roadmap.getByRole('button', { name: '收集入库', exact: true })).toHaveCount(0)

  await roadmap.locator('[data-node-id="node-2"] h3').click()
  await expect(roadmap.getByRole('button', { name: '收集入库', exact: true })).toHaveCount(0)
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
  await researchAgain(roadmap)
  expect(app.lastResearchPolicy()).toBe('clear')
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
  const report = roadmap.getByRole('tabpanel', { name: '研报', exact: true })
  const reportTab = roadmap.getByRole('tab', { name: '研报', exact: true })
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
  await researchAgain(roadmap)
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
  await researchAgain(roadmap)
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
  const report = roadmap.getByRole('tabpanel', { name: '研报', exact: true })
  await roadmap.getByRole('tab', { name: '研报', exact: true }).click()
  await expect(report.getByRole('heading', { name: 'Saved research report', exact: true })).toBeVisible()
  await roadmap.getByRole('combobox', { name: '领域', exact: true }).selectOption('roadmap-vlm')
  await expect(report.getByRole('heading', { name: 'VLM · 研报', exact: true })).toBeVisible()
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
  const report = roadmap.getByRole('tabpanel', { name: '研报', exact: true })
  const canvas = roadmap.getByTestId('roadmap-canvas')
  const node = canvas.locator('[data-node-id="node-1"]')
  const reportTab = roadmap.getByRole('tab', { name: '研报', exact: true })
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

test('development trees distinguish topic, branch and milestone nodes, route vertical edges, and keep structure editable', async ({ page }) => {
  const app = await setup(page, true)
  app.developmentTree()
  await page.reload()
  await page.getByRole('navigation').getByRole('button', { name: '识途', exact: true }).click()
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  const topic = roadmap.locator('[data-node-id="topic"]')
  const branch = roadmap.locator('[data-node-id="branch"]')
  const milestone = roadmap.locator('[data-node-id="node-0"]')
  await expect(topic).toHaveAttribute('data-roadmap-role', 'topic')
  await expect(branch).toHaveAttribute('data-roadmap-role', 'branch')
  await expect(milestone.getByText('里程碑', { exact: true })).toBeVisible()
  const structure = roadmap.locator('[data-edge-id="topic-branch"]')
  await expect(structure).toHaveAttribute('data-roadmap-structure', 'true')
  await expect(structure).toHaveAttribute('data-orientation', 'vertical')
  const coordinates = (await structure.locator('.domain-edge-line').getAttribute('d'))!.match(/-?\d+(?:\.\d+)?/g)!.map(Number)
  expect(coordinates[0]).toBe(340)
  expect(coordinates[2]).toBe(coordinates[0])
  expect(coordinates[6]).toBe(340)
  expect(coordinates[1]).toBeLessThan(coordinates[7])
  await expect(roadmap.locator('[data-edge-id="evolution"]')).toHaveAttribute('data-orientation', 'horizontal')
  await topic.getByRole('heading').click()
  await roadmap.getByRole('textbox', { name: '主题名称', exact: true }).fill('语言模型技术演进')
  await expect.poll(() => app.state().boards[0].nodes.find(n => n.id === 'topic')?.text).toBe('语言模型技术演进')
  await branch.getByRole('heading').click()
  await roadmap.getByRole('textbox', { name: '分支名称', exact: true }).fill('注意力与预训练')
  await branch.getByRole('button', { name: '连接节点', exact: true }).click()
  await expect(topic.getByRole('button', { name: '连接节点', exact: true })).toBeDisabled()
  await roadmap.locator('[data-node-id="node-1"]').getByRole('button', { name: '连接论文', exact: true }).click()
  await expect.poll(() => app.state().boards[0].edges.some(e => e.source === 'branch' && e.target === 'node-1' && e.roadmapStructure && !e.analysis)).toBe(true)
  await topic.getByRole('button', { name: '连接节点', exact: true }).click()
  await expect(milestone.getByRole('button', { name: '连接论文', exact: true })).toBeDisabled()
  await page.keyboard.press('Escape')
  await structure.getByRole('button', { name: '锁定连线', exact: true }).click()
  await expect(roadmap.getByRole('heading', { name: '结构连线', exact: true })).toBeVisible()
  await expect(roadmap.getByText('用于组织主题、分支与代表论文，不表示论文之间的证据关系。', { exact: true })).toBeVisible()
  await expect(roadmap.getByRole('heading', { name: /连线解读/ })).toHaveCount(0)
  await page.screenshot({ path: '/tmp/knowra-shitu-development-tree.png' })
  expect(app.domainWrites()).toBe(0)
  expect(app.errors()).toEqual([])
})

test('an existing roadmap can be arranged without running research again', async ({ page }) => {
  const app = await setup(page, true)
  app.developmentTree()
  await page.reload()
  await page.getByRole('navigation').getByRole('button', { name: '识途', exact: true }).click()
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  await roadmap.getByRole('button', { name: '整理布局', exact: true }).click()
  const menu = roadmap.getByRole('menu', { name: '整理布局', exact: true })
  await expect(menu.getByRole('menuitem', { name: /横向排列/ })).toContainText('研究分支横向展开，论文向下演进。')
  await expect(menu.getByRole('menuitem', { name: /竖直排列/ })).toContainText('研究分支纵向展开，论文向右演进。')
  expect(app.layoutPosts()).toBe(0)
  await menu.getByRole('menuitem', { name: /竖直排列/ }).click()
  await expect.poll(app.layoutPosts).toBe(1)
  expect(app.lastLayoutOrientation()).toBe('vertical')
  await expect.poll(() => app.state().boards[0].nodes.find(node => node.id === 'topic')?.x).toBe(80)
  await expect.poll(() => app.state().boards[0].nodes.find(node => node.id === 'branch')?.x).toBe(520)
  await roadmap.getByRole('button', { name: '整理布局', exact: true }).click()
  await roadmap.getByRole('menuitem', { name: /横向排列/ }).click()
  await expect.poll(app.layoutPosts).toBe(2)
  expect(app.lastLayoutOrientation()).toBe('horizontal')
  await expect.poll(() => app.state().boards[0].nodes.find(node => node.id === 'topic')?.y).toBe(80)
  await expect.poll(() => app.state().boards[0].nodes.find(node => node.id === 'branch')?.y).toBe(520)
  expect(app.researchPosts()).toBe(0)
  expect(app.errors()).toEqual([])
})

test('an outdated local backend is identified instead of exposing an Axios 422', async ({ page }) => {
  const app = await setup(page, true)
  app.developmentTree()
  await page.reload()
  await page.getByRole('navigation').getByRole('button', { name: '识途', exact: true }).click()
  await page.route('**/api/roadmaps/layout', route => route.fulfill({
    status: 422,
    json: { detail: [{
      type: 'extra_forbidden', loc: ['body', 'orientation'],
      msg: 'Extra inputs are not permitted', input: 'vertical',
    }] },
  }))
  const roadmap = page.getByRole('region', { name: '识途', exact: true })

  await roadmap.getByRole('button', { name: '整理布局', exact: true }).click()
  await roadmap.getByRole('menuitem', { name: /竖直排列/ }).click()

  const alert = roadmap.getByRole('alert')
  await expect(alert).toContainText('本地服务仍在运行旧版本，请重新启动应用后重试。')
  await expect(alert).not.toContainText('Request failed with status code 422')
})

test('the first preview fits a wide tree when its canvas becomes visible and never overrides later viewport changes', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 800 })
  const app = await setup(page)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  const canvas = roadmap.getByTestId('roadmap-canvas')
  const proposed: DomainBoard = {
    ...board(true),
    nodes: [
      { id: 'wide-topic', kind: 'text', roadmapRole: 'topic', text: '语言模型技术发展', x: 2000, y: 0, width: 280, height: 140, paperId: null, groupId: null, locked: false },
      ...papers.map((p, i) => ({ id: `wide-branch-${i}`, kind: 'text' as const, roadmapRole: 'branch' as const, text: p.roadmap!.branch, x: i * 2000, y: 300, width: 280, height: 130, paperId: null, groupId: null, locked: false })),
      ...board(true).nodes.map((n, i) => ({ ...n, x: i * 2000, y: 620 })),
    ],
    edges: papers.flatMap((_, i) => [
      { id: `wide-root-${i}`, source: 'wide-topic', target: `wide-branch-${i}`, label: '研究分支', locked: false, roadmapStructure: true },
      { id: `wide-leaf-${i}`, source: `wide-branch-${i}`, target: `node-${i}`, label: '代表论文', locked: false, roadmapStructure: true },
    ]),
  }
  const initialViewport = structuredClone(app.state().boards[0].viewport)
  await roadmap.getByRole('button', { name: '生成路线图', exact: true }).click()
  await expect(roadmap.getByText('核验论文来源', { exact: false })).toBeVisible()
  await roadmap.getByRole('tab', { name: '研报', exact: true }).click()
  app.preview(undefined, undefined, proposed)
  await expect(roadmap.getByRole('tabpanel', { name: '研报', exact: true }).getByText('预览报告 · 应用方案后保存', { exact: true })).toBeVisible()
  expect(app.state().boards[0].viewport).toEqual(initialViewport)
  await roadmap.getByRole('tab', { name: '路线图', exact: true }).click()
  await expect.poll(() => app.state().boards[0].viewport.zoom).toBeLessThan(0.15)
  const canvasBounds = (await canvas.boundingBox())!
  for (const node of proposed.nodes) {
    const bounds = (await canvas.locator(`[data-node-id="${node.id}"]`).boundingBox())!
    expect(bounds.x).toBeGreaterThanOrEqual(canvasBounds.x)
    expect(bounds.y).toBeGreaterThanOrEqual(canvasBounds.y)
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(canvasBounds.x + canvasBounds.width)
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(canvasBounds.y + canvasBounds.height)
  }
  expect(app.state().boards[0].nodes).toEqual([])
  await page.screenshot({ path: '/tmp/knowra-shitu-first-preview-fit.png' })
  const fittedZoom = app.state().boards[0].viewport.zoom
  await roadmap.getByRole('button', { name: '放大', exact: true }).click()
  await expect.poll(() => app.state().boards[0].viewport.zoom).toBe(fittedZoom * 1.25)
  const chosenViewport = structuredClone(app.state().boards[0].viewport)
  await roadmap.getByRole('tab', { name: '研报', exact: true }).click()
  await roadmap.getByRole('tab', { name: '路线图', exact: true }).click()
  await page.setViewportSize({ width: 1180, height: 850 })
  await roadmap.getByRole('button', { name: '应用方案', exact: true }).click()
  await expect(roadmap.getByRole('button', { name: '重新调研', exact: true })).toBeEnabled()
  expect(app.state().boards[0].viewport).toEqual(chosenViewport)
  await researchAgain(roadmap)
  await expect(roadmap.getByText('核验论文来源', { exact: false })).toBeVisible()
  app.preview(undefined, undefined, proposed)
  await expect(roadmap.getByText('路线图预览', { exact: false })).toBeVisible()
  expect(app.state().boards[0].viewport).toEqual(chosenViewport)
  expect(app.errors()).toEqual([])
})

test('narrative reports render bounded flowcharts and verified local figures, and export figures with retry', async ({ page }) => {
  const app = await setup(page, true)
  const asset = `/api/roadmaps/report-assets/${'a'.repeat(64)}.png`
  const externalRequests: string[] = []
  page.on('request', request => { if (request.url().includes('tracking.invalid')) externalRequests.push(request.url()) })
  const markdown = '# 技术发展叙事\n\n从注意力架构到预训练目标，关键变化在于学习方式。\n\n```mermaid\nflowchart TB\n topic["语言模型"] --> branch["注意力与预训练"]\n branch --> p1["Transformer"]\n p1 -->|演进| p2["BERT"]\n```\n\n## 原文架构图\n\n![Transformer 原文架构图](' + asset + ')\n\n来源：论文第 3 页。\n\n![外部图](https://tracking.invalid/figure.png)\n\n![无效资产](/api/roadmaps/report-assets/unverified.png)\n\n<img src="https://tracking.invalid/raw.png">\n\n```mermaid\nflowchart TB\n A --> B\n click A "https://tracking.invalid/click"\n```'
  app.savedReport(markdown, true)
  const roadmap = page.getByRole('region', { name: '识途', exact: true })
  await roadmap.getByRole('tab', { name: '研报', exact: true }).click()
  const report = roadmap.getByRole('tabpanel', { name: '研报', exact: true })
  const flowchart = report.getByRole('img', { name: '技术发展流程图', exact: true })
  await expect(flowchart.locator('svg')).toBeVisible({ timeout: 15000 })
  await expect(flowchart).toContainText('语言模型')
  await expect(flowchart).toContainText('BERT')
  await expect(flowchart.locator('a, image, foreignObject, script')).toHaveCount(0)
  const originalFigure = report.getByRole('img', { name: 'Transformer 原文架构图', exact: true })
  await originalFigure.scrollIntoViewIfNeeded()
  await expect(originalFigure).toHaveAttribute('src', asset)
  await expect.poll(() => originalFigure.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(1)
  await expect(report.locator('img')).toHaveCount(1)
  await expect(report.getByText('流程图无法渲染，已保留原始定义。', { exact: true })).toBeVisible()
  app.failExportOnce()
  await report.getByRole('button', { name: '下载报告及图片', exact: true }).click()
  await expect(report.getByRole('alert')).toContainText('导出报告及图片失败，请重试。')
  const downloadPromise = page.waitForEvent('download')
  await report.getByRole('button', { name: '下载报告及图片', exact: true }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('LLM-research.zip')
  expect((await readFile((await download.path())!)).toString()).toBe('PK\u0003\u0004mock-report-and-figure')
  expect(app.exportRequests()).toBe(2)
  expect(externalRequests).toEqual([])
  await report.getByRole('button', { name: '展开报告', exact: true }).click()
  const expanded = page.getByRole('dialog', { name: '调研报告', exact: true })
  await expect(expanded.getByRole('img', { name: '技术发展流程图', exact: true }).locator('svg')).toBeVisible()
  await expect(expanded.getByRole('img', { name: '技术发展流程图', exact: true })).toContainText('BERT')
  await expect(expanded.getByRole('button', { name: '下载报告及图片', exact: true })).toBeEnabled()
  await expanded.getByRole('button', { name: '关闭', exact: true }).click()
  await report.locator('.roadmap-report-body').evaluate(element => { element.scrollTop = 0 })
  await page.screenshot({ path: '/tmp/knowra-shitu-narrative-flowchart.png' })
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
