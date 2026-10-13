import { test, expect, type Page, type Locator } from '@playwright/test'
import type { DomainState, DomainPaper, DomainSnapshot, DomainAgentRun } from '../src/api/domains'
const reviews: DomainPaper[] = [
  {
    id: 'paper-a',
    title: 'Attention Is All You Need — A Complete Research Paper Title',
    year: '2017',
    category: 'LLM',
    eligible: true,
    reason: null,
    incomplete: false,
    sections: {
      core_contribution: 'A shared contribution',
      abstract_summary: 'The original review summary',
      problem: 'Sequence modelling',
      motivation: 'Parallel computation',
    },
  },
  {
    id: 'paper-b',
    title: 'A later paper with partial review',
    year: '2023',
    category: 'LLM',
    eligible: true,
    reason: null,
    incomplete: true,
    sections: { core_contribution: '', abstract_summary: 'Partial summary', problem: '', motivation: '' },
  },
  {
    id: 'pending',
    title: 'Not reviewed yet',
    year: '2025',
    category: 'LLM',
    eligible: false,
    reason: 'not_reviewed',
    incomplete: true,
    sections: { core_contribution: '', abstract_summary: '', problem: '', motivation: '' },
  },
]
async function setup(page: Page, inherited = false) {
  let state: DomainState = {
    activeId: 'llm',
    boards: (inherited ? ['LLM', 'VLM', 'VLA', '三维重建-静态', '三维重建-动态', '世界模型', '其他'] : ['LLM', '三维重建']).map((name, i) => ({
      id: i ? (inherited ? `category-${i}` : '3d') : 'llm',
      category: inherited ? name : null,
      name,
      nodes: [],
      edges: [],
      viewport: { x: 80, y: 80, zoom: 1 },
    })),
  }
  let revision = 1,
    failSave = false
  let runs: DomainAgentRun[] = []
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    let json: unknown = {}
    if (path === '/api/domains') {
      if (route.request().method() === 'PUT') {
        if (failSave) return route.fulfill({ status: 503, json: { detail: 'Offline' } })
        const body = route.request().postDataJSON() as DomainSnapshot
        if (body.revision !== revision) return route.fulfill({ status: 409, json: { detail: 'Conflict' } })
        state = body.state
        revision++
        json = { revision }
      } else json = { state, revision }
    } else if (path === '/api/domains/agent/runs') {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON()
        if (body.revision !== revision) return route.fulfill({ status: 409, json: { detail: 'Conflict' } })
        const run: DomainAgentRun = { id: 'run-1', boardId: body.boardId, status: 'pending', jobId: 'job', error: null, proposal: null, progress: { label: '分析论文关系' } }
        runs = [run]; json = run
      } else json = runs
    } else if (path.endsWith('/run-1/discard')) {
      runs = runs.map(r => ({ ...r, status: r.status === 'pending' ? 'cancelled' : 'discarded', proposal: null })); json = runs[0]
    } else if (path.endsWith('/run-1/apply')) {
      const run = runs[0]
      const before = structuredClone(state.boards.find(b => b.id === run.boardId)!)
      state = { ...state, boards: state.boards.map(b => b.id === run.boardId ? { ...run.proposal!.board, viewport: b.viewport } : b) }
      revision++; runs = [{ ...run, status: 'applied', proposal: null }]
      json = { state, revision, before }
    }
    else if (path === '/api/domains/papers') json = reviews
    else if (path === '/api/graph') json = { nodes: [], edges: [] }
    else if (path === '/api/papers')
      json = reviews.map((p) => ({
        ...p,
        processed: true,
        paper_category: p.category,
        authors: [],
        filename: `${p.id}.pdf`,
      }))
    else if (path.startsWith('/api/papers/')) {
      const p = reviews.find((p) => path === `/api/papers/${p.id}`)
      json = {
        ...p,
        processed: true,
        authors: [],
        filename: 'test.pdf',
        knowledge_nodes: [],
        extraction: p?.sections,
        notes: '',
        chat: { messages: [] },
      }
    } else if (path === '/api/paper-categories') json = { categories: [{ name: 'LLM' }] }
    else if (path === '/api/paper-teams') json = { teams: [] }
    else if (path === '/api/status') json = { running: false, total: 0, done: 0 }
    else if (path === '/api/promotion/counts')
      json = {
        summary: { counts: { pending: 0, promoted: 0, rejected: 0 }, by: { llm: 0, user: 0, heuristic: 0 } },
      }
    else if (path === '/api/wiki/freshness') json = { papers: { total: 0 }, concepts: { total: 0 } }
    else if (path === '/api/wiki/lint/job' || path === '/api/wiki/status')
      json = { running: false, phase: 'idle' }
    else if (path === '/api/prompt/preferences') json = { locale: 'zh' }
    await route.fulfill({ json })
  })
  await page.goto('/')
  await page.getByRole('navigation').getByRole('button', { name: '索骥', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '领域', exact: true })).toHaveValue('llm')
  return {
    snapshot: () => state,
    preview: () => {
      const board = structuredClone(state.boards[0])
      board.nodes[0].x = 80; board.nodes[0].y = 140
      board.nodes[1].x = 480; board.nodes[1].y = 140
      board.edges = [{ id: 'ai-edge', source: board.nodes[0].id, target: board.nodes[1].id, label: '改进', locked: false,
        analysis: { relation: 'improves', certainty: 'explicit', reason: 'The later paper extends the attention operator.',
          discussion: { citingPaperId: 'paper-b', citedPaperId: 'paper-a', status: 'analyzed', contextsFound: 2, contextsUsed: 2, truncated: false,
            findings: [{ aspect: 'purpose', text: 'Uses the earlier operator as an experimental baseline.', evidence: [{ paperId: 'paper-b', source: 'pdf', locator: 'PDF 第 4 页', page: 4, quote: 'The earlier operator serves as our experimental baseline under equal compute budgets.' }] }] },
          evidence: [{ paperId: 'paper-b', source: 'pdf', locator: 'PDF 第 8 页', page: 8, quote: 'We build upon the attention operator proposed in the earlier paper.' }] } }]
      const analysis = board.edges[0].analysis!
      const contextEvidence = analysis.discussion!.findings[0].evidence[0]
      // The same passage is returned for both relation and discussion; a second
      // passage on the page and a different paper's same page must survive.
      analysis.evidence.push({ ...contextEvidence, quote: `  ${contextEvidence.quote}  ` })
      analysis.discussion!.findings[0].evidence.push({ ...contextEvidence, quote: 'All baselines use the same training budget.' })
      analysis.evidence.push({ ...contextEvidence, paperId: 'paper-a', quote: 'The original paper describes its attention operator.' })
      runs = [{ ...runs[0], status: 'preview', proposal: { board, added: 1, removed: 0, protected: 0, rejected: 0, warnings: [], unconnected: [] } }]
    },
    errors,
    renameFirstCategory: () => {
      state = { ...state, boards: state.boards.map((b, i) => i ? b : { ...b, name: 'Language models', category: 'Language models' }) }
      revision++
    },
    fail: (value: boolean) => {
      failSave = value
    },
    conflict: () => {
      revision++
    },
  }
}
async function importPaper(page: Page, title = reviews[0].title) {
  await page.locator('.domain-header').getByRole('button', { name: '导入论文', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '导入论文', exact: true })
  await dialog.locator('label').filter({ hasText: title }).getByRole('checkbox').check()
  await dialog.getByRole('button', { name: '导入到画布' }).click()
}
async function drag(page: Page, locator: Locator, dx: number, dy: number) {
  const box = (await locator.boundingBox())!
  await page.mouse.move(box.x + 25, box.y + 30)
  await page.mouse.down()
  await page.mouse.move(box.x + 25 + dx, box.y + 30 + dy, { steps: 8 })
  await page.mouse.up()
}

test('import gate, duplicate references, independent notes and saved layout', async ({ page }) => {
  const context = await setup(page)
  await page.locator('.domain-header').getByRole('button', { name: '导入论文', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '导入论文', exact: true })
  await expect(
    dialog.locator('label').filter({ hasText: 'Not reviewed yet' }).getByRole('checkbox'),
  ).toBeDisabled()
  await expect(
    dialog.locator('label').filter({ hasText: reviews[1].title }).getByRole('checkbox'),
  ).toBeEnabled()
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
  await importPaper(page)
  await importPaper(page)
  const cards = page.locator('[data-kind="paper"]')
  await expect(cards).toHaveCount(2)
  await cards.first().getByRole('heading').click()
  await page.getByRole('textbox', { name: '卡片备注', exact: true }).fill('First reference only')
  await expect(cards.first()).toContainText('First reference only')
  await expect(cards.nth(1)).not.toContainText('First reference only')
  await drag(page, cards.first(), 170, 50)
  await expect.poll(() => context.snapshot().boards[0].nodes[0]?.x).toBeGreaterThan(100)
  const savedX = context.snapshot().boards[0].nodes[0].x
  await expect(page.getByRole('status').filter({ hasText: '已保存到本机' })).toBeVisible()
  await page.reload()
  await page.getByRole('navigation').getByRole('button', { name: '索骥', exact: true }).click()
  await expect(cards).toHaveCount(2)
  await expect(cards.first()).toHaveCSS('left', `${savedX}px`)
  await expect(cards.first()).toContainText('First reference only')
  expect(context.errors).toEqual([])
})

test('bulk import selects eligible filtered papers and preserves hidden selections', async ({ page }) => {
  const context = await setup(page)
  await page.locator('.domain-header').getByRole('button', { name: '导入论文', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '导入论文', exact: true })
  const all = dialog.getByRole('checkbox', { name: '全选', exact: true })
  const search = dialog.getByRole('textbox', { name: '搜索论文库' })
  await all.check()
  await expect(dialog.locator('footer')).toContainText('已选择 2 篇论文')
  await expect(dialog.locator('.domain-import-list input:disabled')).not.toBeChecked()
  await dialog.locator('label').filter({ hasText: reviews[0].title }).getByRole('checkbox').uncheck()
  await expect(all).toBeChecked({ indeterminate: true })
  await all.check()
  await search.fill('Attention')
  await expect(all).toBeChecked()
  await all.uncheck()
  await expect(dialog.locator('footer')).toContainText('已选择 1 篇论文')
  await search.fill('Not reviewed yet')
  await expect(all).toBeDisabled()
  await search.fill('no matching paper')
  await expect(all).toBeDisabled()
  await search.fill('')
  await expect(all).toBeChecked({ indeterminate: true })
  await all.check()
  await all.uncheck()
  await expect(dialog.getByRole('button', { name: '导入到画布' })).toBeDisabled()
  await all.check()
  await page.screenshot({ path: '/tmp/knowra-domain-import-select-all.png', fullPage: true })
  await dialog.getByRole('button', { name: '导入到画布' }).click()
  await expect(page.locator('[data-kind="paper"]')).toHaveCount(2)
  await expect.poll(() => context.snapshot().boards[0].nodes.map(n => n.paperId).sort()).toEqual(['paper-a', 'paper-b'])
  expect(context.errors).toEqual([])
})

test('clearing all canvas elements requires confirmation from button and keyboard', async ({ page }) => {
  const context = await setup(page)
  await importPaper(page)
  await importPaper(page, reviews[1].title)
  const cards = page.locator('[data-kind="paper"]')
  await cards.first().getByRole('button', { name: '连接论文', exact: true }).click()
  await cards.nth(1).getByRole('button', { name: '连接论文', exact: true }).click()
  await cards.first().getByRole('heading').click()
  await cards.nth(1).getByRole('heading').click({ modifiers: ['Shift'] })
  await expect.poll(() => context.snapshot().boards[0].edges.length).toBe(1)
  const before = structuredClone(context.snapshot().boards[0])
  page.once('dialog', async dialog => {
    expect(dialog.type()).toBe('confirm')
    expect(dialog.message()).toContain('全部元素（共 3 个，含连线）')
    await dialog.dismiss()
  })
  await page.getByRole('button', { name: '移除选中元素', exact: true }).click()
  await expect(cards).toHaveCount(2)
  await expect(page.locator('[data-kind="paper"][data-selected="true"]')).toHaveCount(2)
  expect(context.snapshot().boards[0]).toEqual(before)
  page.once('dialog', async dialog => {
    expect(dialog.message()).toContain('论文库中的论文不会被删除')
    await dialog.accept()
  })
  await page.keyboard.press('Delete')
  await expect(cards).toHaveCount(0)
  await expect(page.locator('[data-edge-id]')).toHaveCount(0)
  await expect.poll(() => context.snapshot().boards[0].nodes.length).toBe(0)
  await page.getByRole('button', { name: '撤销', exact: true }).click()
  await expect(cards).toHaveCount(2)
  await expect(page.locator('[data-edge-id]')).toHaveCount(1)
  await expect.poll(() => context.snapshot().boards[0].nodes).toEqual(before.nodes)
  await expect.poll(() => context.snapshot().boards[0].edges).toEqual(before.edges)
  expect(context.errors).toEqual([])
})

test('research workspace resizes without editing the canvas and persists across reload', async ({ page }) => {
  const context = await setup(page)
  await importPaper(page)
  await importPaper(page, reviews[1].title)
  await page.getByRole('button', { name: 'agent代连', exact: true }).click()
  await expect(page.getByText('分析论文关系 · 当前类别只读')).toBeVisible()
  const before = structuredClone(context.snapshot().boards[0])
  const divider = page.getByRole('separator', { name: '调整研究工作区宽度' })
  const panel = page.locator('.domain-inspector')
  const canvas = page.getByTestId('domain-canvas')
  const startWidth = (await panel.boundingBox())!.width
  const startCanvasWidth = (await canvas.boundingBox())!.width
  const box = (await divider.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + 100)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 - 220, box.y + 100, { steps: 6 })
  await page.mouse.up()
  await expect(panel).toHaveCSS('width', `${startWidth + 220}px`)
  expect((await canvas.boundingBox())!.width).toBeCloseTo(startCanvasWidth - 220, 0)
  expect(context.snapshot().boards[0]).toEqual(before)
  await expect.poll(() => page.evaluate(() => localStorage.getItem('knowra.domain.inspectorWidth'))).toBe(String(startWidth + 220))
  await page.reload()
  await page.getByRole('navigation').getByRole('button', { name: '索骥', exact: true }).click()
  await expect(panel).toHaveCSS('width', `${startWidth + 220}px`)
  await divider.focus()
  await page.keyboard.press('ArrowRight')
  await expect(panel).toHaveCSS('width', `${startWidth + 200}px`)
  await page.keyboard.press('Home')
  await expect(panel).toHaveCSS('width', '200px')
  await page.keyboard.press('End')
  expect((await canvas.boundingBox())!.width).toBeGreaterThanOrEqual(320)
  await page.setViewportSize({ width: 900, height: 1000 })
  await expect.poll(async () => (await panel.boundingBox())!.width).toBeLessThan(600)
  expect((await canvas.boundingBox())!.width).toBeGreaterThanOrEqual(320)
  await divider.dblclick()
  await expect(panel).toHaveCSS('width', '250px')
  await page.setViewportSize({ width: 1440, height: 1000 })
  await divider.focus()
  await page.keyboard.press('ArrowLeft')
  await page.screenshot({ path: '/tmp/knowra-domain-resizable-workspace.png', fullPage: true })
  expect(context.snapshot().boards[0]).toEqual(before)
  expect(context.errors).toEqual([])
})

test('review popover follows card and viewport, shows review source and restores on return', async ({
  page,
}) => {
  const context = await setup(page)
  await importPaper(page)
  const card = page.locator('[data-kind="paper"]').first()
  await card.getByRole('button', { name: /^查看论文回顾/ }).click()
  const pop = page.getByTestId('domain-review-popover')
  await expect(pop).toContainText('A shared contribution')
  const old = (await pop.boundingBox())!
  await drag(page, card, 70, 60)
  await expect(pop).toBeVisible()
  expect((await pop.boundingBox())!.y).toBeGreaterThan(old.y + 40)
  await pop.getByRole('tab', { name: '摘要', exact: true }).click()
  await expect(pop).toContainText('The original review summary')
  await page.getByRole('button', { name: '缩小', exact: true }).click()
  await expect(pop).toBeVisible()
  await pop.getByRole('button', { name: '打开完整回顾' }).click()
  await page.getByRole('button', { name: '返回索骥' }).click()
  await expect(card).toHaveAttribute('data-selected', 'true')
  await expect(pop).toBeVisible()
  await page.getByTestId('domain-canvas').click({ position: { x: 400, y: 450 } })
  await expect(pop).toHaveCount(0)
  expect(context.errors).toEqual([])
})

test('paper card shortcut opens the correct full review during generation and restores the canvas', async ({ page }) => {
  const context = await setup(page)
  await importPaper(page)
  await importPaper(page, reviews[1].title)
  const card = page.locator('[data-kind="paper"]').filter({ hasText: reviews[1].title })
  await page.getByRole('button', { name: '缩小', exact: true }).click()
  await page.getByRole('button', { name: 'agent代连', exact: true }).click()
  await expect(page.getByText('分析论文关系 · 当前类别只读')).toBeVisible()
  const before = structuredClone(context.snapshot().boards[0])
  const style = await card.getAttribute('style')
  const jump = card.getByRole('button', { name: `打开完整回顾：${reviews[1].title}`, exact: true })
  await expect(jump).toBeEnabled()
  const requestedPaper = page.waitForRequest(request => new URL(request.url()).pathname === '/api/papers/paper-b')
  await jump.click()
  await requestedPaper
  await expect(page.getByRole('heading', { name: reviews[1].title, exact: true })).toBeVisible()
  await page.getByRole('button', { name: '返回索骥' }).click()
  await expect(card).toHaveAttribute('style', style!)
  await expect(card).toHaveAttribute('data-selected', 'true')
  await expect(page.getByTestId('domain-review-popover')).toHaveCount(0)
  await expect(page.getByText('分析论文关系 · 当前类别只读')).toBeVisible()
  expect(context.snapshot().boards[0]).toEqual(before)
  await page.screenshot({ path: '/tmp/knowra-domain-review-shortcut.png', fullPage: true })
  expect(context.errors).toEqual([])
})

test('review navigation reveals the current paper without interrupting manual list browsing', async ({ page }) => {
  const context = await setup(page)
  const target = reviews[1]
  const paperList = Array.from({ length: 30 }, (_, i) => ({
    id: `earlier-${i}`, title: `Earlier language model ${i}`, filename: `earlier-${i}.pdf`,
    processed: true, paper_category: 'LLM', paper_team: 'A team', authors: [], year: 2020,
  }))
  await page.route('**/api/papers', route => route.fulfill({ json: [
    ...paperList,
    { ...target, processed: true, paper_category: '世界模型', paper_team: 'Z team', authors: [], filename: 'target.pdf' },
  ] }))
  await importPaper(page, target.title)
  await page.getByRole('button', { name: `打开完整回顾：${target.title}`, exact: true }).click()
  await expect(page.getByRole('heading', { name: target.title, exact: true })).toBeVisible()
  const list = page.getByTestId('review-paper-list')
  const current = list.locator('[aria-current="page"]')
  const expectCurrentInView = async () => {
    await expect(current).toContainText(target.title)
    await expect.poll(async () => current.evaluate(el => {
      const row = el.getBoundingClientRect()
      const viewport = el.closest('[data-testid="review-paper-list"]')!.getBoundingClientRect()
      return row.top >= viewport.top && row.bottom <= viewport.bottom
    })).toBe(true)
  }
  await expectCurrentInView()
  await expect.poll(() => list.evaluate(el => el.scrollTop)).toBeGreaterThan(0)
  // An explicit collapse remains respected; reopening the navigation reveals it again.
  await list.getByRole('button', { name: '世界模型 1', exact: true }).click()
  await expect(current).toHaveCount(0)
  await page.getByRole('button', { name: '收起论文导航', exact: true }).click()
  await page.getByRole('button', { name: '展开论文导航', exact: true }).click()
  await expectCurrentInView()
  await page.getByRole('button', { name: '按团队分组', exact: true }).click()
  await expectCurrentInView()
  await expect(list.getByRole('button', { name: 'Z team 1', exact: true })).toHaveAttribute('aria-expanded', 'true')
  await list.evaluate(el => { el.scrollTop = 0 })
  await page.getByRole('textbox', { name: '搜索标题、作者、文件名' }).fill('Earlier language')
  await expect(current).toHaveCount(0)
  await expect(list.getByText('Earlier language model 0', { exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: '搜索标题、作者、文件名' }).clear()
  await expectCurrentInView()
  await page.screenshot({ path: '/tmp/knowra-review-current-paper.png', fullPage: true })
  expect(context.errors).toEqual([])
})

test('connections, groups, locking, removal and undo preserve references', async ({ page }) => {
  const context = await setup(page)
  await importPaper(page)
  await importPaper(page, reviews[1].title)
  const cards = page.locator('[data-kind="paper"]')
  await cards.first().getByRole('button', { name: '连接论文', exact: true }).click()
  await cards.nth(1).getByRole('button', { name: '连接论文', exact: true }).click()
  await expect(page.locator('[data-edge-id]')).toHaveCount(1)
  const edgeLock = page.locator('[data-edge-id]').getByRole('button')
  await expect(edgeLock).toHaveAccessibleName('锁定连线')
  await expect(edgeLock).toHaveAttribute('aria-pressed', 'false')
  const unlockedColor = await edgeLock.evaluate(el => getComputedStyle(el).color)
  const controlSize = (await edgeLock.boundingBox())!.width
  await edgeLock.click()
  await expect(edgeLock).toHaveAccessibleName('解锁连线')
  await expect(edgeLock).toHaveAttribute('aria-pressed', 'true')
  expect(await edgeLock.evaluate(el => getComputedStyle(el).color)).not.toBe(unlockedColor)
  await expect.poll(() => context.snapshot().boards[0].edges[0]?.locked).toBe(true)
  await expect(page.locator('.domain-inspector').getByRole('button', { name: /锁定连线|解锁连线/ })).toHaveCount(0)
  await page.getByRole('button', { name: '移除选中元素', exact: true }).click()
  await expect(edgeLock).toHaveCount(1)
  await page.getByRole('button', { name: '缩小', exact: true }).click()
  expect((await edgeLock.boundingBox())!.width).toBeCloseTo(controlSize, 1)
  await edgeLock.focus()
  await page.keyboard.press('Space')
  await expect(edgeLock).toHaveAttribute('aria-pressed', 'false')
  await page.getByRole('button', { name: '撤销', exact: true }).click()
  await expect(edgeLock).toHaveAttribute('aria-pressed', 'true')
  await page.screenshot({ path: '/tmp/knowra-domain-edge-lock.png', fullPage: true })
  await edgeLock.click()
  await expect.poll(() => context.snapshot().boards[0].edges[0]?.locked).toBe(false)
  await cards.first().getByRole('heading').click()
  await cards
    .nth(1)
    .getByRole('heading')
    .click({ modifiers: ['Shift'] })
  await page.getByRole('button', { name: '创建分组', exact: true }).click()
  const group = page.locator('[data-kind="group"]')
  await page.getByRole('textbox', { name: '分组名称', exact: true }).fill('Architecture')
  await expect(group).toContainText('Architecture')
  await drag(page, group, 80, 50)
  await page.getByRole('button', { name: '切换锁定', exact: true }).click()
  const before = await cards.first().boundingBox()
  await drag(page, cards.first(), 100, 0)
  expect((await cards.first().boundingBox())?.x).toBe(before?.x)
  await group.locator('.domain-group-heading').click()
  await page.getByRole('button', { name: '解锁', exact: true }).click()
  await page.getByRole('button', { name: '删除分组框，保留内容', exact: true }).click()
  await expect(group).toHaveCount(0)
  await expect(cards).toHaveCount(2)
  await page.getByRole('button', { name: '撤销', exact: true }).click()
  await expect(group).toHaveCount(1)
  await page.getByRole('navigation').getByRole('button', { name: '资料', exact: true }).click()
  await page.getByRole('navigation').getByRole('button', { name: '索骥', exact: true }).click()
  await page.getByRole('button', { name: '重做', exact: true }).click()
  await expect(group).toHaveCount(0)
  expect(context.errors).toEqual([])
})

test('save failure retains edits and explicit retry persists them', async ({ page }) => {
  const context = await setup(page)
  context.fail(true)
  await importPaper(page)
  await expect(page.getByRole('status')).toContainText('保存失败')
  await expect(page.locator('[data-kind="paper"]')).toHaveCount(1)
  expect(context.snapshot().boards[0].nodes).toHaveLength(0)
  context.fail(false)
  await page.getByRole('status').getByRole('button', { name: '重试', exact: true }).click()
  await expect.poll(() => context.snapshot().boards[0].nodes.length).toBe(1)
  await expect(page.getByRole('status')).toContainText('已保存到本机')
})

test('domain management, empty sections, independent text and screenshots', async ({ page }) => {
  await setup(page)
  await page.getByRole('button', { name: '新建领域', exact: true }).click()
  await page.getByRole('textbox', { name: '领域名称', exact: true }).fill('Vision research')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await importPaper(page, reviews[1].title)
  await page
    .locator('[data-kind="paper"]')
    .getByRole('button', { name: /^查看论文回顾/ })
    .click()
  await expect(page.locator('.domain-review-content')).toBeEmpty()
  await page.getByTestId('domain-review-popover').getByRole('tab', { name: '摘要', exact: true }).click()
  await expect(page.locator('.domain-review-content')).toHaveText('Partial summary')
  await page.getByTestId('domain-review-popover').getByRole('button', { name: '关闭', exact: true }).click()
  await page.getByRole('button', { name: '添加独立文本', exact: true }).click()
  await page.getByRole('textbox', { name: '独立文本', exact: true }).fill('An independent research note')
  await expect(page.locator('[data-kind="text"]')).toContainText('An independent research note')
  await page.getByRole('button', { name: '适应画布', exact: true }).click()
  await page.screenshot({ path: 'test-results/domain-dark.png', fullPage: true, animations: 'disabled' })
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'))
  await expect(page.getByRole('button', { name: '切换锁定', exact: true })).toHaveCSS('color', 'rgb(36, 50, 75)')
  await page.screenshot({ path: 'test-results/domain-light.png', fullPage: true, animations: 'disabled' })
  page.on('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '删除领域', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '领域', exact: true }).getByRole('option', { name: /Vision research/ })).toHaveCount(0)
})

test('editable connection, marquee, group resize, search and independent domains', async ({ page }) => {
  const context = await setup(page)
  await page.locator('.domain-header').getByRole('button', { name: '导入论文', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '导入论文', exact: true })
  await dialog.locator('label').filter({ hasText: reviews[0].title }).getByRole('checkbox').check()
  await dialog.locator('label').filter({ hasText: reviews[1].title }).getByRole('checkbox').check()
  await dialog.getByRole('button', { name: '导入到画布' }).click()
  const cards = page.locator('[data-kind="paper"]')
  await expect(cards.first()).toContainText('2017')
  await expect(cards.nth(1)).toContainText('2023')
  await cards.first().getByRole('button', { name: '连接论文', exact: true }).click()
  await cards.nth(1).getByRole('button', { name: '连接论文', exact: true }).click()
  const selectionPoint = await page.locator('.domain-edge-hit').evaluate((element) => {
    const path = element as SVGPathElement
    const point = path.getPointAtLength(path.getTotalLength() / 10).matrixTransform(path.getScreenCTM()!)
    return { x: point.x, y: point.y }
  })
  await page.mouse.click(selectionPoint.x, selectionPoint.y)
  await page.getByRole('textbox', { name: '连线说明', exact: true }).fill('Improves the architecture')
  await expect(page.locator('.domain-edges text')).toHaveText('Improves the architecture')
  const canvas = (await page.getByTestId('domain-canvas').boundingBox())!
  await page.mouse.move(canvas.x + 70, canvas.y + 40)
  await page.mouse.down()
  await page.mouse.move(canvas.x + 740, canvas.y + 350, { steps: 5 })
  await page.mouse.up()
  await expect(page.locator('[data-kind="paper"][data-selected="true"]')).toHaveCount(2)
  await page.getByRole('button', { name: '创建分组', exact: true }).click()
  const group = page.locator('[data-kind="group"]')
  const oldWidth = (await group.boundingBox())!.width
  const resize = (await page.getByRole('button', { name: '调整分组大小', exact: true }).boundingBox())!
  await page.mouse.move(resize.x + 5, resize.y + 5)
  await page.mouse.down()
  await page.mouse.move(resize.x + 95, resize.y + 65, { steps: 5 })
  await page.mouse.up()
  expect((await group.boundingBox())!.width).toBeGreaterThan(oldWidth + 80)
  await page.getByRole('textbox', { name: '搜索画布中的论文' }).fill('Attention')
  await page.locator('.domain-search-results').getByRole('button').click()
  await expect(cards.first()).toHaveAttribute('data-selected', 'true')
  await page.getByRole('textbox', { name: '卡片备注', exact: true }).fill('Independent domain note')
  await page.getByRole('combobox', { name: '领域', exact: true }).selectOption('3d')
  await importPaper(page)
  await expect(cards).toHaveCount(1)
  await expect(cards.first()).not.toContainText('Independent domain note')
  await page.getByRole('combobox', { name: '领域', exact: true }).selectOption('llm')
  await expect(cards).toHaveCount(2)
  await expect(cards.first()).toContainText('Independent domain note')
  await page.getByRole('button', { name: '适应画布', exact: true }).click()
  await cards
    .first()
    .getByRole('button', { name: /^查看论文回顾/ })
    .click()
  await page.screenshot({ path: 'test-results/domain-flow.png', fullPage: true, animations: 'disabled' })
  expect(context.errors).toEqual([])
})

test('conflict preserves unsaved content until explicit reload and modal escape works', async ({ page }) => {
  const context = await setup(page)
  await importPaper(page)
  await expect(page.getByRole('status')).toContainText('已保存到本机')
  context.conflict()
  await page.getByRole('textbox', { name: '卡片备注', exact: true }).fill('Conflicting local edit')
  await expect(page.getByRole('status')).toContainText('其他窗口')
  await expect(page.locator('[data-kind="paper"]')).toContainText('Conflicting local edit')
  expect(context.snapshot().boards[0].nodes[0].text).toBe('')
  page.on('dialog', (dialog) => dialog.accept())
  await page.getByRole('status').getByRole('button', { name: '重新加载' }).click()
  await expect(page.locator('[data-kind="paper"]')).not.toContainText('Conflicting local edit')
  await page.locator('.domain-header').getByRole('button', { name: '导入论文', exact: true }).click()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: '导入论文', exact: true })).toHaveCount(0)
})


test('navigation order and inherited system categories preserve edits on taxonomy refresh', async ({ page }) => {
  const context = await setup(page, true)
  const labels = (await page.getByRole('navigation').getByRole('button').allTextContents()).map((label) => label.trim())
  expect(labels.indexOf('索骥')).toBe(labels.indexOf('推荐') - 1)
  expect(labels.indexOf('索骥')).toBeGreaterThan(labels.indexOf('回顾'))
  const selector = page.getByRole('combobox', { name: '领域', exact: true })
  await expect(selector.getByRole('option')).toHaveCount(7)
  await expect(page.getByTestId('domain-imported-count')).toHaveText('已导入：0 张卡片')
  await expect(page.getByRole('tablist', { name: '领域' })).toHaveCount(0)
  for (const category of ['LLM', 'VLM', 'VLA', '三维重建-静态', '三维重建-动态', '世界模型', '其他']) {
    await expect(selector.getByRole('option', { name: category, exact: true })).toHaveCount(1)
  }
  await expect(page.getByRole('button', { name: '重命名领域', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '删除领域', exact: true })).toBeDisabled()
  await importPaper(page)
  await expect(page.getByRole('status')).toHaveText('已保存到本机')
  const nodes = structuredClone(context.snapshot().boards[0].nodes)
  context.renameFirstCategory()
  await page.getByRole('navigation').getByRole('button', { name: '资料', exact: true }).click()
  await page.getByRole('navigation').getByRole('button', { name: '索骥', exact: true }).click()
  await expect(selector).toHaveValue('llm')
  await expect(selector.locator('option:checked')).toHaveText('Language models')
  await expect(page.getByTestId('domain-imported-count')).toHaveText('已导入：1 张卡片')
  await selector.selectOption('category-1')
  await expect(page.getByTestId('domain-imported-count')).toHaveText('已导入：0 张卡片')
  await selector.selectOption('llm')
  await expect(page.getByTestId('domain-imported-count')).toHaveText('已导入：1 张卡片')
  await expect(page.getByTestId('domain-canvas').getByText(reviews[0].title, { exact: true })).toBeVisible()
  expect(context.snapshot().boards[0].nodes).toEqual(nodes)
  expect(context.errors).toEqual([])
})


test('agent generation is readonly, permits browsing and other categories, cancellation restores edits', async ({ page }) => {
  const context = await setup(page)
  await importPaper(page)
  await importPaper(page, reviews[1].title)
  const cards = page.locator('[data-kind="paper"]')
  await cards.first().getByRole('button', { name: '连接论文', exact: true }).click()
  await cards.nth(1).getByRole('button', { name: '连接论文', exact: true }).click()
  await page.getByRole('button', { name: 'agent代连', exact: true }).click()
  await expect(page.getByText('分析论文关系 · 当前类别只读')).toBeVisible()
  await expect(page.locator('[data-edge-id]').getByRole('button')).toBeDisabled()
  await expect(page.locator('.domain-header').getByRole('button', { name: '导入论文' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '添加独立文本' })).toBeDisabled()
  const card = page.locator('[data-kind="paper"]').first()
  const oldStyle = await card.getAttribute('style')
  await drag(page, card, 120, 80)
  await expect(card).toHaveAttribute('style', oldStyle!)
  await expect(page.getByRole('textbox', { name: '卡片备注', exact: true })).toBeDisabled()
  await card.locator('.domain-review-plus').click()
  await expect(page.locator('.domain-popover')).toBeVisible()
  await page.getByRole('combobox', { name: '领域', exact: true }).selectOption('3d')
  await expect(page.getByRole('button', { name: '添加独立文本' })).toBeEnabled()
  await page.getByRole('button', { name: '添加独立文本' }).click()
  await page.getByRole('combobox', { name: '领域', exact: true }).selectOption('llm')
  await page.getByRole('button', { name: '取消生成' }).click()
  await expect(page.getByRole('button', { name: '添加独立文本' })).toBeEnabled()
  await expect.poll(() => context.snapshot().boards[1].nodes.length).toBe(1)
  expect(context.errors).toEqual([])
})

test('agent preview survives refresh, shows evidence, applies once and undoes as one operation', async ({ page }) => {
  const context = await setup(page)
  await importPaper(page)
  await importPaper(page, reviews[1].title)
  await page.getByRole('button', { name: 'agent代连', exact: true }).click()
  await expect(page.getByRole('button', { name: '取消生成' })).toBeVisible()
  const before = structuredClone(context.snapshot().boards[0])
  context.preview()
  await page.reload()
  await page.getByRole('navigation').getByRole('button', { name: '索骥', exact: true }).click()
  await expect(page.getByRole('button', { name: '应用方案' })).toBeVisible()
  expect(context.snapshot().boards[0].edges).toHaveLength(0)
  await expect(page.getByRole('button', { name: '撤销', exact: true })).toBeDisabled()
  await expect(page.locator('[data-edge-id]').getByRole('button', { name: '锁定连线', exact: true })).toBeDisabled()
  await page.locator('.domain-edges text').filter({ hasText: '改进' }).click()
  await expect(page.getByText('The later paper extends the attention operator.')).toBeVisible()
  await expect(page.locator('.domain-evidence-sources summary').filter({ hasText: 'PDF 第 8 页' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '引用语境深读' })).toBeVisible()
  await expect(page.getByText('Uses the earlier operator as an experimental baseline.')).toBeVisible()
  await expect(page.getByText('所分析片段中未找到明确表述。')).toHaveCount(2)
  const sources = page.locator('.domain-evidence-sources')
  await expect(sources.locator('details')).toHaveCount(3)
  await expect(page.locator('.domain-citation-discussion blockquote')).toHaveCount(0)
  await page.locator('.domain-citation-discussion').getByRole('button', { name: '证据 2', exact: true }).click()
  const sharedSource = sources.locator('details').nth(1)
  await expect(sharedSource).toHaveAttribute('open', '')
  await expect(sharedSource.locator('blockquote')).toHaveCount(2)
  await expect(sharedSource).toContainText('The earlier operator serves as our experimental baseline under equal compute budgets.')
  await expect(sharedSource).toContainText('All baselines use the same training budget.')
  await expect(sharedSource).toContainText('支持：连线依据 · 引用用途')
  await expect(sharedSource).toContainText(reviews[1].title)
  await sources.locator('details').nth(2).locator('summary').click()
  await expect(sources.locator('details').nth(2)).toContainText(reviews[0].title)
  await page.screenshot({ path: '/tmp/knowra-domain-agent-preview.png', fullPage: true })
  await page.getByRole('button', { name: '应用方案' }).click()
  await expect.poll(() => context.snapshot().boards[0].edges.length).toBe(1)
  await page.getByRole('button', { name: '撤销', exact: true }).click()
  await expect.poll(() => context.snapshot().boards[0].nodes).toEqual(before.nodes)
  await expect.poll(() => context.snapshot().boards[0].edges).toEqual(before.edges)
  expect(context.errors).toEqual([])
})

test('discarding an agent preview leaves original cards unchanged', async ({ page }) => {
  const context = await setup(page)
  await importPaper(page)
  await importPaper(page, reviews[1].title)
  await page.getByRole('button', { name: 'agent代连', exact: true }).click()
  await expect(page.getByRole('button', { name: '取消生成' })).toBeVisible()
  const before = structuredClone(context.snapshot().boards[0])
  context.preview()
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await page.getByRole('button', { name: '放弃方案' }).click()
  await expect(page.getByRole('button', { name: 'agent代连', exact: true })).toBeEnabled()
  expect(context.snapshot().boards[0]).toEqual(before)
})

test('Domain model can be selected independently and changing main model preserves it', async ({ page }) => {
  await setup(page)
  const tasks = ['paper_extract', 'paper_chat', 'domain_connect']
  let config = {
    scan_directory: '/test', similarity_threshold: 0.6,
    model_gateway: {
      providers: [{ id: 'openai', label: 'OpenAI', provider_type: 'openai', api_key: '', base_url: '', enabled: true }],
      models: ['first', 'second'].map(id => ({ id: `openai/${id}`, label: id, provider_id: 'openai', upstream_model: id, model_kind: 'chat', supports_vision: true, supported_tasks: tasks })),
      task_specs: tasks.map(id => ({ id, label: id === 'domain_connect' ? '索骥代连' : id, category: '索骥', task_type: 'llm', description: '', recommended_model_id: 'openai/first' })),
      task_bindings: Object.fromEntries(tasks.map(id => [id, { model_id: 'openai/first', reasoning_effort: 'medium' }])),
      available_provider_types: [],
    },
  }
  await page.route('**/api/config', async route => {
    if (route.request().method() !== 'GET') config = { ...config, ...route.request().postDataJSON() }
    await route.fulfill({ json: config })
  })
  await page.reload()
  await page.getByRole('navigation').getByRole('button', { name: '设置', exact: true }).click()
  await page.getByRole('button', { name: /^任务模型/ }).click()
  const section = (name: string) => page.locator('.rounded-xl').filter({ has: page.locator('p').filter({ hasText: new RegExp(`^${name}$`) }) }).last()
  const domain = section('索骥代连')
  const main = section('主模型')
  await expect(domain).toBeVisible()
  await domain.locator('select').nth(3).selectOption('openai/second')
  await main.locator('select').nth(3).selectOption('openai/second')
  await main.locator('select').nth(3).selectOption('openai/first')
  await expect(domain.locator('select').nth(3)).toHaveValue('openai/second')
  await page.getByRole('button', { name: '保存设置' }).click()
  await expect.poll(() => config.model_gateway.task_bindings.domain_connect.model_id).toBe('openai/second')
  expect(config.model_gateway.task_bindings.paper_extract.model_id).toBe('openai/first')
})
