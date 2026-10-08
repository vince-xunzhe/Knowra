import { test, expect, type Page } from '@playwright/test'
import type { Core } from 'cytoscape'
import type { GraphData, GraphEdge, GraphNode } from '../src/api/client'
import { groupTopics, neighborhood, shortestConnection, topicOverview, visibleRelations, withPaperCategories } from '../src/lib/graphExplorer'

const node = (id: string, category: string | null = '3D Reconstruction', type: GraphNode['node_type'] = 'paper'): GraphNode => ({ id, title: `Research ${id}`, category, node_type: type, content: '', origin: 'auto', hidden: false, concept_candidate: false, publishable_concept: true, promotion_status: 'promoted', promoted_by: null, promotion_reason: null, last_promotion_eval_at: null, tags: [], source_paper_ids: [], created_at: null })
const edge = (id: string, source: string, target: string, relation_type = 'uses'): GraphEdge => ({ id, source, target, relation_type, weight: 0.8 })
const graph: GraphData = {
  nodes: [node('A'), node('B', 'Localization'), node('Method', '3D Reconstruction', 'technique'), node('Dataset', 'Localization', 'dataset'), node('Isolated', null), node('Similar', 'Localization')],
  edges: [
    { ...edge('a-method', 'A', 'Method'), origin: 'explicit', confidence: 0, source_field: 'extraction.methods', evidence: 'A uses Method, according to the source.', metadata: { provenance: [{ origin: 'explicit', confidence: 0, evidence: 'A uses Method, according to the source.', source_field: 'extraction.methods' }, { origin: 'manual', evidence: 'Independently reviewed.' }] } },
    edge('a-dataset', 'A', 'Dataset', 'evaluated_on'), edge('b-dataset', 'B', 'Dataset', 'evaluated_on'), edge('a-similar', 'A', 'Similar', 'similar'),
  ],
}

test('graph projections preserve source data, categories and relationship directions', () => {
  const before = JSON.stringify(graph), filtered = visibleRelations(graph, false)
  const topics = groupTopics(filtered), overview = topicOverview(topics, filtered, 'Uncategorized')
  expect(overview.nodes.map(n => n.count).sort()).toEqual([1, 2, 3])
  expect(overview.edges).toHaveLength(1)
  expect(overview.edges[0].count).toBe(1)
  const path = shortestConnection(filtered, 'Method', 'B')
  expect(path.order).toEqual(['Method', 'A', 'Dataset', 'B'])
  expect(path.data.edges.map(e => [e.source, e.target])).toEqual([['A', 'Method'], ['A', 'Dataset'], ['B', 'Dataset']])
  expect(path.data.edges[0].metadata).toEqual(graph.edges[0].metadata)
  expect(JSON.stringify(graph)).toBe(before)
})

test('missing graph categories resolve from source-paper categories without rewriting source nodes', () => {
  const nodes = [
    { ...node('inferred', null), source_paper_ids: [2, 3, 1] },
    { ...node('single', null), paper_id: 1 },
    { ...node('unknown', null), source_paper_ids: [99] },
    { ...node('existing', 'Keep original'), source_paper_ids: [1] },
  ]
  const papers = [{ id: 1, paper_category: 'Vision' }, { id: 2, paper_category: 'Robotics' }, { id: 3, paper_category: 'Robotics' }]
  const grouped = withPaperCategories({ nodes, edges: [] }, papers)
  expect(grouped.nodes.map(n => n.category)).toEqual(['Robotics', 'Vision', null, 'Keep original'])
  expect(nodes[0].category).toBeNull()
})

test('neighborhood depth, limits, hidden nodes, cycles and missing paths are explicit', () => {
  const filtered = visibleRelations(graph, false)
  expect(neighborhood(filtered, 'A', 1).data.nodes.map(n => n.id).sort()).toEqual(['A', 'Dataset', 'Method'])
  expect(neighborhood(filtered, 'A', 2).data.nodes.map(n => n.id)).toContain('B')
  expect(neighborhood(filtered, 'A', 2, 2).data.nodes).toHaveLength(2)
  expect(neighborhood(filtered, 'A', 2, 2).total).toBe(4)
  expect(shortestConnection(filtered, 'A', 'Similar').found).toBe(false)
  expect(shortestConnection(visibleRelations(graph, true), 'A', 'Similar').found).toBe(true)
  expect(shortestConnection(filtered, 'A', 'Isolated').found).toBe(false)
  expect(shortestConnection(filtered, 'A', 'A').order).toEqual(['A'])
  expect(shortestConnection(filtered, 'missing', 'A').found).toBe(false)
  expect(shortestConnection(filtered, 'Method', 'B', 2).found).toBe(false)
  const scoped = visibleRelations({ nodes: graph.nodes.filter(n => n.id !== 'Dataset'), edges: [...graph.edges, edge('cycle', 'Method', 'A'), edge('dangling', 'ghost', 'B')] }, true)
  expect(scoped.edges.some(e => e.source === 'ghost' || e.target === 'Dataset')).toBe(false)
  expect(shortestConnection(scoped, 'A', 'B').found).toBe(false)
})

async function setup(page: Page, locale = 'zh', theme = 'dark') {
  const writes: string[] = []
  await page.addInitScript(({ locale, theme }) => { localStorage.setItem('knowra.locale', locale); localStorage.setItem('knowra.theme', theme) }, { locale, theme })
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    if (req.method() !== 'GET') writes.push(path)
    let body: unknown = {}
    if (path === '/prompt/preferences') body = { locale }
    else if (path === '/graph') body = graph
    else if (path.startsWith('/nodes/')) body = { ...graph.nodes.find(n => n.id === path.split('/').pop()), connected_nodes: [], edges: [], linked_papers: [], can_edit: false, can_hide: false }
    else if (['/papers','/wiki/papers','/wiki/concepts'].includes(path)) body = []
    else if (path === '/promotion/counts') body = { summary: { counts: { pending: 0, promoted: 6, rejected: 0 }, by: {} } }
    else if (path === '/wiki/freshness') body = { papers: { total_processed: 6 }, concepts: { total_nodes: 0 } }
    else if (path === '/wiki/graph') body = { nodes: [], edges: [] }
    await route.fulfill({ json: body })
  })
  await page.goto('/')
  await expect(page.getByTestId('knowledge-graph-canvas')).toBeVisible()
  return writes
}
const canvas = (page: Page) => page.getByTestId('knowledge-graph-canvas')
const explorer = (page: Page) => page.locator('.graph-explorer')

test.beforeEach(({ page }) => { page.on('pageerror', e => { throw e }) })

test('overview drills into real topics, preserves dragged positions, and returns to overview', async ({ page }) => {
  const writes = await setup(page)
  await expect(explorer(page).getByRole('button', { name: /3D Reconstruction.*2 个节点/ })).toBeVisible()
  await page.screenshot({ path: 'test-results/graph-overview-dark.png' })
  await explorer(page).getByRole('button', { name: /3D Reconstruction.*2 个节点/ }).click()
  await expect(explorer(page).getByRole('status').first()).toContainText('2 个节点')
  await canvas(page).evaluate(el => {
    const cy = (el as HTMLElement & { _cyreg: { cy: Core } })._cyreg.cy
    cy.getElementById('A').position({ x: 111, y: 222 })
  })
  await explorer(page).getByRole('checkbox').check()
  await expect.poll(() => canvas(page).evaluate(el => (el as HTMLElement & { _cyreg: { cy: Core } })._cyreg.cy.getElementById('A').position())).toEqual({ x: 111, y: 222 })
  await explorer(page).getByRole('button', { name: '返回上一步', exact: true }).click()
  await expect(explorer(page).getByRole('button', { name: '主题总览', exact: true })).toHaveAttribute('aria-pressed', 'true')
  expect(writes.filter(p => p !== '/scan')).toEqual([])
})

test('local exploration respects depth and shows isolated-node feedback', async ({ page }) => {
  await setup(page)
  await explorer(page).getByRole('button', { name: '局部探索', exact: true }).click()
  await expect(explorer(page)).toContainText('选择一个节点，查看它的局部关系。')
  await explorer(page).getByRole('combobox', { name: '中心节点', exact: true }).selectOption('A')
  await expect.poll(() => canvas(page).evaluate(el => (el as HTMLElement & { _cyreg: { cy: Core } })._cyreg.cy.nodes().length)).toBe(3)
  await explorer(page).getByRole('combobox', { name: '邻居层数' }).selectOption('2')
  await expect.poll(() => canvas(page).evaluate(el => (el as HTMLElement & { _cyreg: { cy: Core } })._cyreg.cy.nodes().length)).toBe(4)
  await explorer(page).getByRole('combobox', { name: '中心节点', exact: true }).selectOption('Isolated')
  await expect(explorer(page)).toContainText('这个节点在当前筛选范围内没有可见邻居')
})

test('paths show original edge direction and provenance, and react to similarity filters', async ({ page }) => {
  const writes = await setup(page)
  await explorer(page).getByRole('button', { name: '关系与证据', exact: true }).click()
  await explorer(page).getByRole('combobox', { name: '起点', exact: true }).selectOption('Method')
  await explorer(page).getByRole('combobox', { name: '终点', exact: true }).selectOption('B')
  await expect(explorer(page).getByRole('status').first()).toContainText('4 个节点 · 3 条关系')
  await explorer(page).locator('.ge-path-steps').getByRole('button', { name: /Research A.*使用.*Research Method/ }).click()
  const proof = explorer(page).getByRole('complementary', { name: '关系证据' })
  await expect(proof).toContainText('A uses Method, according to the source.')
  await expect(proof).toContainText('Independently reviewed.')
  await expect(proof).toContainText('置信度：0.000')
  await expect.poll(() => canvas(page).evaluate(el => {
    const cy = (el as HTMLElement & { _cyreg: { cy: Core } })._cyreg.cy
    return cy.nodes().every(n => { const b = n.renderedBoundingBox({ includeLabels: true }); return b.x1 >= 0 && b.y1 >= 0 && b.x2 <= cy.width() && b.y2 <= cy.height() })
  })).toBe(true)
  await page.screenshot({ path: 'test-results/graph-path-evidence.png' })
  await explorer(page).getByRole('combobox', { name: '终点', exact: true }).selectOption('Similar')
  await expect(proof).toHaveCount(0)
  await expect(explorer(page)).toContainText('6 跳内未找到连接')
  await explorer(page).getByRole('checkbox').check()
  await expect(explorer(page).getByRole('status').first()).toContainText('3 个节点 · 2 条关系')
  await explorer(page).getByRole('combobox', { name: '终点', exact: true }).selectOption('Method')
  await expect(explorer(page).getByRole('status').first()).toContainText('1 个节点 · 0 条关系')
  expect(writes.filter(p => p !== '/scan')).toEqual([])
})

for (const locale of ['zh', 'en', 'ja', 'es']) test(`three perspectives fit a narrow window in ${locale} Light`, async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 1000 })
  await setup(page, locale, 'light')
  const modes = explorer(page).locator('.ge-modes button')
  for (let i = 0; i < 3; i++) {
    await modes.nth(i).click()
    await expect.poll(() => explorer(page).evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
    const boxes = await modes.evaluateAll(elements => elements.map(e => { const r = e.getBoundingClientRect(); return { x: r.x, right: r.right, y: r.y, bottom: r.bottom } }))
    for (let a = 0; a < boxes.length; a++) for (let b = a + 1; b < boxes.length; b++) expect(boxes[a].right <= boxes[b].x || boxes[b].right <= boxes[a].x || boxes[a].bottom <= boxes[b].y || boxes[b].bottom <= boxes[a].y).toBeTruthy()
  }
  await page.screenshot({ path: `test-results/graph-narrow-${locale}.png` })
})
