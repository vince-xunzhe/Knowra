import { test, expect, type Page, type Locator } from '@playwright/test'
import type { DomainState, DomainPaper, DomainSnapshot } from '../src/api/domains'
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
async function setup(page: Page) {
  let state: DomainState = {
    activeId: 'llm',
    boards: ['LLM', '三维重建'].map((name, i) => ({
      id: i ? '3d' : 'llm',
      name,
      nodes: [],
      edges: [],
      viewport: { x: 80, y: 80, zoom: 1 },
    })),
  }
  let revision = 1,
    failSave = false
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
    } else if (path === '/api/domains/papers') json = reviews
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
  await expect(page.getByRole('tab', { name: 'LLM' })).toBeVisible()
  return {
    snapshot: () => state,
    errors,
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

test('connections, groups, locking, removal and undo preserve references', async ({ page }) => {
  const context = await setup(page)
  await importPaper(page)
  await importPaper(page, reviews[1].title)
  const cards = page.locator('[data-kind="paper"]')
  await cards.first().getByRole('button', { name: '连接论文', exact: true }).click()
  await cards.nth(1).getByRole('button', { name: '连接论文', exact: true }).click()
  await expect(page.locator('[data-edge-id]')).toHaveCount(1)
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
  await expect(page.getByRole('tab', { name: 'Vision research' })).toHaveCount(0)
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
  const midpoint = await page.locator('.domain-edge-hit').evaluate((element) => {
    const path = element as SVGPathElement
    const point = path.getPointAtLength(path.getTotalLength() / 2).matrixTransform(path.getScreenCTM()!)
    return { x: point.x, y: point.y }
  })
  await page.mouse.click(midpoint.x, midpoint.y)
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
  await page.getByRole('tab', { name: '三维重建' }).click()
  await importPaper(page)
  await expect(cards).toHaveCount(1)
  await expect(cards.first()).not.toContainText('Independent domain note')
  await page.getByRole('tab', { name: 'LLM' }).click()
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
