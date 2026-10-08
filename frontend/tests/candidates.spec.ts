import { test, expect, type Page } from '@playwright/test'
import type { Core } from 'cytoscape'

async function setup(page: Page) {
  let fail = false
  let delay = 0
  const mutations: string[] = []
  const nodes = [
    { id: 'paper-1', title: 'Example paper', node_type: 'paper', promotion_status: 'promoted' },
    { id: 'candidate-tech', title: '候选技术 Alpha', node_type: 'technique', promotion_status: 'pending' },
    { id: 'candidate-data', title: 'Candidate dataset Beta', node_type: 'dataset', promotion_status: 'pending' },
  ].map(node => ({ ...node, content: 'Review context', origin: 'auto', hidden: false, concept_candidate: node.node_type !== 'paper', publishable_concept: false, promoted_by: null, promotion_reason: null, last_promotion_eval_at: null, tags: ['example'], source_paper_ids: [], created_at: null }))
  await page.route('**/api/**', async route => {
    const req = route.request()
    const url = new URL(req.url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    if (req.method() !== 'GET') mutations.push(path)
    let body: unknown = {}
    if (path === '/prompt/preferences') body = { locale: 'zh' }
    else if (path === '/papers' || path === '/wiki/papers' || path === '/wiki/concepts') body = []
    else if (path === '/promotion/counts') body = { summary: { counts: { pending: nodes.filter(n => n.promotion_status === 'pending').length, promoted: 0, rejected: 0 }, by: {} } }
    else if (path === '/graph') {
      const included = url.searchParams.get('include_candidates') === 'true'
      if (included && delay) await new Promise(resolve => setTimeout(resolve, delay))
      if (included && fail) return route.fulfill({ status: 503, json: { detail: 'unavailable' } })
      body = { nodes: nodes.filter(n => included || n.promotion_status === 'promoted'), edges: [] }
    }
    else if (path.startsWith('/nodes/')) body = { ...nodes.find(n => n.id === path.split('/').pop()), connected_nodes: [], edges: [], linked_papers: [], can_edit: false, can_hide: true }
    else if (path.startsWith('/promotion/') && req.method() === 'PATCH') {
      const node = nodes.find(n => n.id === path.split('/').pop())!
      node.promotion_status = req.postDataJSON().status
      body = { node }
    }
    else if (path === '/wiki/freshness') body = { papers: { total_processed: 1 }, concepts: { total_nodes: 0 } }
    else if (path === '/wiki/graph') body = { nodes: [], edges: [] }
    else if (path === '/promotion/prompt') body = { prompt: '', default_template: '' }
    await route.fulfill({ json: body })
  })
  await page.goto('/')
  await page.locator('aside section > button[aria-expanded]').nth(1).click()
  await expect(page.getByRole('button', { name: '候选(2)', exact: true })).toBeVisible()
  return { mutations, fail: () => { fail = true }, recover: () => { fail = false }, delay: () => { delay = 350 } }
}

test.beforeEach(async ({ page }) => {
  page.on('pageerror', error => { throw error })
})

const panel = (page: Page) => page.getByRole('region', { name: /待审核候选/ })

test('candidate mode immediately lists, searches, locates and reviews candidates', async ({ page }) => {
  const state = await setup(page)
  await page.getByRole('button', { name: '候选(2)', exact: true }).click()
  await expect(panel(page).getByRole('listitem')).toHaveCount(2)
  await panel(page).getByRole('textbox').fill('Beta')
  await expect(panel(page).getByRole('listitem')).toHaveCount(1)
  await panel(page).getByRole('button', { name: /Candidate dataset Beta/ }).click()
  await expect(page.getByRole('heading', { name: 'Candidate dataset Beta', exact: true })).toBeVisible()
  const canvas = page.getByTestId('knowledge-graph-canvas')
  await expect.poll(() => canvas.evaluate(el => (el as HTMLElement & { _cyreg: { cy: Core } })._cyreg.cy.getElementById('candidate-data').hasClass('highlighted'))).toBeTruthy()
  await page.getByRole('button', { name: '精选', exact: true }).click()
  await expect(panel(page).getByRole('button', { name: /Candidate dataset Beta/ })).toHaveCount(0)
  await panel(page).getByRole('textbox').fill('')
  await expect(panel(page).getByRole('listitem')).toHaveCount(1)
  await panel(page).getByRole('button', { name: /候选技术 Alpha/ }).click()
  await page.getByRole('button', { name: '淘汰', exact: true }).click()
  await expect(panel(page).getByText('当前没有待审核候选。')).toBeVisible()
  expect(state.mutations.filter(path => path !== '/scan')).toEqual(['/promotion/candidate-data', '/promotion/candidate-tech'])
})

test('candidates are visible from compiled view and selection clears conflicting type filters', async ({ page }) => {
  await setup(page)
  await page.getByRole('button', { name: /^论文\s*1$/ }).click()
  await page.getByRole('button', { name: '编译图谱', exact: true }).click()
  await page.getByRole('button', { name: '候选(2)', exact: true }).click()
  await expect(panel(page).getByRole('listitem')).toHaveCount(2)
  await panel(page).getByRole('button', { name: /Candidate dataset Beta/ }).click()
  const canvas = page.getByTestId('knowledge-graph-canvas')
  await expect(canvas).toBeVisible()
  await expect.poll(() => canvas.evaluate(el => {
    const cy = (el as HTMLElement & { _cyreg: { cy: Core } })._cyreg.cy
    const n = cy.getElementById('candidate-data')
    const p = n.renderedPosition()
    return !n.empty() && n.hasClass('highlighted') && p.x >= 0 && p.x <= cy.width() && p.y >= 0 && p.y <= cy.height()
  })).toBeTruthy()
  await page.screenshot({ path: 'test-results/candidates-review.png', animations: 'disabled' })
})

test('candidate loading, retry and exit are explicit, including late responses', async ({ page }) => {
  const state = await setup(page)
  state.fail()
  await page.getByRole('button', { name: '候选(2)', exact: true }).click()
  await expect(panel(page).getByRole('alert')).toContainText('候选加载失败，请重试。')
  state.recover()
  await panel(page).getByRole('button', { name: '重试', exact: true }).click()
  await expect(panel(page).getByRole('listitem')).toHaveCount(2)
  await panel(page).getByRole('button', { name: '退出候选查看', exact: true }).click()
  await expect(panel(page)).toHaveCount(0)
  state.delay()
  const lateResponse = page.waitForResponse(response => response.url().includes('/api/graph?include_candidates=true'))
  await page.getByRole('button', { name: '候选(2)', exact: true }).click()
  await expect(panel(page).getByRole('status')).toHaveText('加载候选中…')
  await panel(page).getByRole('button', { name: '退出候选查看', exact: true }).click()
  await expect(panel(page)).toHaveCount(0)
  await lateResponse
  await expect.poll(() => page.getByTestId('knowledge-graph-canvas').evaluate(el => (el as HTMLElement & { _cyreg: { cy: Core } })._cyreg.cy.nodes().length)).toBe(1)
})
