import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import ts from 'typescript'
import type { Core } from 'cytoscape'
import { test, expect, type Page } from '@playwright/test'
import en from '../src/i18n/locales/en.json' with { type: 'json' }
import ja from '../src/i18n/locales/ja.json' with { type: 'json' }
import es from '../src/i18n/locales/es.json' with { type: 'json' }

async function mockBackend(page: Page, options: { offlineLanguage?: boolean; outdatedBackend?: boolean; graph?: boolean } = {}) {
  let locale = 'zh'
  const mutations: string[] = []
  const prompts: Record<string, string> = { zh: '中文抽取模板', en: 'English extraction template', ja: '日本語の抽出テンプレート', es: 'Plantilla de extracción en español' }
  await page.route('**/api/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.replace('/api', '')
    const language = url.searchParams.get('locale') ?? 'zh'
    if (request.method() !== 'GET') mutations.push(path)
    let body: unknown = {}
    if (path === '/prompt/preferences') {
      if (options.outdatedBackend) return route.fulfill({ status: 404, json: { detail: 'Not Found' } })
      if (request.method() === 'PUT') {
        if (options.offlineLanguage) return route.fulfill({ status: 503, json: { detail: 'offline' } })
        locale = request.postDataJSON().locale
      }
      body = { locale }
    } else if (path === '/config') body = {
      scan_directory: '/test/papers', similarity_threshold: 0.6,
      model_gateway: { providers: [], models: [], task_bindings: {}, task_specs: [] },
    }
    else if (path === '/prompt') {
      if (request.method() === 'POST') prompts[language] = request.postDataJSON().extraction_prompt
      body = { extraction_prompt: prompts[language], default_prompt: `Default ${language}` }
    } else if (path === '/prompt/reset') { prompts[language] = `Default ${language}`; body = { extraction_prompt: prompts[language] } }
    else if (path === '/promotion/prompt') body = { prompt: `Curation ${language}`, default_template: `Default ${language}` }
    else if (path === '/graph') body = options.graph ? {
      nodes: ['Attention', '世界模型 User concept'].map((title, i) => ({ id: String(i + 1), title, content: '', node_type: 'concept', origin: 'manual', hidden: false, concept_candidate: false, publishable_concept: true, promotion_status: 'promoted', promoted_by: 'user', tags: [], source_paper_ids: [] })),
      edges: [{ id: '1-2', source: '1', target: '2', relation_type: 'related', weight: 0.8 }],
    } : { nodes: [], edges: [] }
    else if (path === '/papers') body = [{ id: 1, title: '世界模型 User paper', filename: 'paper.pdf', filepath: 'paper.pdf', processed: false, processing_status: 'pending', authors: [], tags: [], paper_category: '用户自定义', year: 2025 }]
    else if (path === '/paper-categories') body = { categories: [{ name: '用户自定义', builtin: false, removable: true, count: 1 }] }
    else if (path === '/paper-teams') body = { teams: [] }
    else if (path === '/status') body = { running: false, done: 0, total: 0, errors: 0 }
    else if (path === '/promotion/counts') body = { summary: { counts: { pending: 0, promoted: 0, rejected: 0 }, by: { llm: 0, user: 0, heuristic: 0 } } }
    else if (path === '/wiki/freshness') body = { papers: { total: 0, ready: 0, missing: 0, stale: 0, orphan: 0 }, concepts: { total: 0, ready: 0, missing: 0, stale: 0, orphan: 0 } }
    else if (path === '/wiki/lint/job') body = { running: false, phase: 'idle' }
    else if (path === '/wiki/lint/status') body = { exists: false, stale: false }
    else if (path === '/wiki/status' || path === '/promotion/status') body = { running: false, phase: 'idle' }
    return route.fulfill({ json: body })
  })
  return { mutations, prompts }
}

async function openSettings(page: Page, label = '设置') {
  await page.getByRole('navigation').getByRole('button', { name: label, exact: true }).click()
  await expect(page.locator('#appearance-language')).toBeVisible()
}

test('all four languages switch immediately; theme and locale survive reload without workflow writes', async ({ page }) => {
  const { mutations } = await mockBackend(page)
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/')
  await openSettings(page)
  const initialScans = mutations.filter(path => path === '/scan').length
  for (const [language, code, heading] of [
    ['English', 'en', 'Language & appearance'], ['日本語', 'ja', '言語と外観'], ['Español', 'es', 'Idioma y apariencia'], ['中文', 'zh-CN', '语言与外观'],
  ]) {
    await page.locator('#appearance-language').selectOption({ label: language })
    await expect(page.locator('html')).toHaveAttribute('lang', code)
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible()
  }
  expect(mutations.filter(path => path === '/scan')).toHaveLength(initialScans)
  await page.locator('#appearance-language').selectOption({ label: 'English' })
  await page.locator('#appearance-theme').selectOption('light')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(244, 246, 251)')
  await page.screenshot({ path: 'test-results/settings-light-en.png', fullPage: true, animations: 'disabled' })
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await openSettings(page, 'Settings')
  await expect(page.locator('#appearance-theme')).toHaveValue('light')
  await page.locator('#appearance-theme').selectOption('dark')
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(11, 13, 18)')
  await page.screenshot({ path: 'test-results/settings-dark-en.png', fullPage: true, animations: 'disabled' })
  expect(mutations.every(path => path === '/prompt/preferences' || path === '/scan')).toBeTruthy()
  expect(errors).toEqual([])
})

test('prompt saves and resets are scoped to language; source paper names remain untouched', async ({ page }) => {
  const { prompts } = await mockBackend(page)
  await page.goto('/')
  await openSettings(page)
  await page.locator('#appearance-language').selectOption({ label: 'English' })
  await page.getByRole('navigation').getByRole('button', { name: 'Library', exact: true }).click()
  await expect(page.getByText('世界模型 User paper', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Expand global prompt editor' }).click()
  const editor = page.locator('textarea').first()
  await expect(editor).toHaveValue('English extraction template')
  await page.getByRole('button', { name: 'Edit', exact: true }).click()
  await editor.fill('My English custom prompt')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect.poll(() => prompts.en).toBe('My English custom prompt')
  expect(prompts.zh).toBe('中文抽取模板')
  await openSettings(page, 'Settings')
  await page.locator('#appearance-language').selectOption({ label: '日本語' })
  await page.getByRole('navigation').getByRole('button', { name: '資料', exact: true }).click()
  await page.getByRole('button', { name: '共通プロンプト編集を展開' }).click()
  await expect(editor).toHaveValue('日本語の抽出テンプレート')
  await page.getByRole('button', { name: '編集', exact: true }).click()
  page.once('dialog', dialog => dialog.accept())
  await page.getByTitle('既定のプロンプトに戻す').click()
  await expect(editor).toHaveValue('Default ja')
  expect(prompts.en).toBe('My English custom prompt')
})

test('failed language persistence retains selection; theme is independent of backend', async ({ page }) => {
  const { mutations } = await mockBackend(page, { offlineLanguage: true })
  await page.goto('/')
  await openSettings(page)
  await page.locator('#appearance-language').selectOption({ label: 'Español' })
  await expect(page.getByText('语言保存失败，请检查后端连接后重试。')).toBeVisible()
  await expect(page.locator('#appearance-language')).toHaveValue('zh')
  await page.locator('#appearance-theme').selectOption('light')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  expect(mutations.filter(path => path !== '/scan')).toEqual(['/prompt/preferences'])
})

test('an older running backend gives actionable restart guidance without changing language', async ({ page }) => {
  await mockBackend(page, { outdatedBackend: true })
  await page.goto('/')
  await openSettings(page)
  await page.locator('#appearance-language').selectOption({ label: 'English' })
  await expect(page.getByText('当前后端尚未加载语言设置接口，请重启本地服务后重试。')).toBeVisible()
  await expect(page.locator('#appearance-language')).toHaveValue('zh')
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN')
})

test('catalogs cover the same messages and preserve interpolation parameters', () => {
  for (const catalog of [en, ja, es]) {
    expect(Object.keys(catalog).sort()).toEqual(Object.keys(en).sort())
    for (const [source, translated] of Object.entries(catalog)) {
      expect(translated.trim()).not.toBe('')
      expect(translated.match(/\{\d+\}/g)?.sort() ?? []).toEqual(source.match(/\{\d+\}/g)?.sort() ?? [])
    }
  }
})


test('all source UI messages have complete catalogs', () => {
  const catalog: Record<string, string> = en
  const missing = new Set<string>()
  function scan(dir: string) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name)
      if (entry.isDirectory() && entry.name !== 'i18n') scan(path)
      else if (/\.tsx?$/.test(entry.name)) {
        const ast = ts.createSourceFile(path, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
        function visit(node: ts.Node) {
          if (ts.isCallExpression(node) && ['tr', 't'].includes(node.expression.getText(ast))) {
            const source = node.arguments[0]
            if (source && ts.isStringLiteral(source) && /[\u3400-\u9fff]/.test(source.text) && !catalog[source.text]) missing.add(source.text)
          }
          ts.forEachChild(node, visit)
        }
        visit(ast)
      }
    }
  }
  scan(new URL('../src', import.meta.url).pathname)
  expect([...missing]).toEqual([])
})

test('Light updates the canvas without recreating the graph or translating source labels', async ({ page }) => {
  await mockBackend(page, { graph: true })
  await page.goto('/')
  const canvas = page.getByTestId('knowledge-graph-canvas')
  await expect(canvas.locator('canvas').first()).toBeVisible()
  await canvas.evaluate(el => {
    const cy = (el as HTMLElement & { _cyreg: { cy: Core } })._cyreg.cy
    cy.data('themeTestIdentity', 'same-graph')
    localStorage.setItem('knowra.theme', 'light')
    window.dispatchEvent(new StorageEvent('storage', { key: 'knowra.theme', newValue: 'light' }))
  })
  await expect.poll(() => canvas.evaluate(el => {
    const cy = (el as HTMLElement & { _cyreg: { cy: Core } })._cyreg.cy
    return { identity: cy.data('themeTestIdentity'), color: cy.nodes()[0].style('color'), count: cy.nodes().length }
  })).toEqual({ identity: 'same-graph', color: 'rgb(36,50,75)', count: 2 })
  await page.screenshot({ path: 'test-results/graph-light.png', animations: 'disabled' })
})

test('Spanish Light settings fit a narrow desktop window', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 1000 })
  await mockBackend(page)
  await page.goto('/')
  await openSettings(page)
  await page.locator('#appearance-language').selectOption({ label: 'Español' })
  await expect(page.locator('html')).toHaveAttribute('lang', 'es')
  await page.locator('#appearance-theme').selectOption('light')
  const panel = page.getByRole('region', { name: 'Idioma y apariencia' })
  await expect(panel).toBeVisible()
  expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBeTruthy()
  await page.screenshot({ path: 'test-results/settings-light-es-narrow.png', animations: 'disabled' })
})
