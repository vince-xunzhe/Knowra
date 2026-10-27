// Real React and production routes; only the isolated fixture's model callable is controlled.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { chromium } from 'playwright-core'

const [web, api, executable, output] = process.argv.slice(2)
const result = { steps: [], screenshots: [], pageErrors: [], blockedRequests: [], failedResponses: [], starts: [] }
const browser = await chromium.launch({ executablePath: executable, headless: true,
  args: ['--disable-background-networking', '--no-first-run'] })
for (const sig of ['SIGTERM', 'SIGINT']) process.once(sig, async () => {
  await browser.close(); process.exit(128 + (sig === 'SIGTERM' ? 15 : 2))
})
let page
try {
  const context = await browser.newContext({ viewport: { width: 1680, height: 1050 }, locale: 'zh-CN' })
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    if (url.protocol === 'data:' || url.protocol === 'blob:' || [web, api].includes(url.origin)) return route.continue()
    result.blockedRequests.push(route.request().url())
    return route.abort()
  })
  page = await context.newPage()
  page.setDefaultTimeout(15_000)
  page.on('pageerror', error => result.pageErrors.push(error.message))
  page.on('response', response => { if (response.status() >= 400) result.failedResponses.push({ url: response.url(), status: response.status() }) })
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/domains/agent/runs') result.starts.push(request.postDataJSON())
  })
  const get = async endpoint => {
    const response = await context.request.get(api + endpoint)
    assert.equal(response.status(), 200, await response.text())
    return response.json()
  }
  const post = async (endpoint, data, status = 200) => {
    const response = await context.request.post(api + endpoint, { data })
    assert.equal(response.status(), status, await response.text())
    return response.json()
  }
  const fixture = () => get('/fixture/state')
  const live = () => get('/api/domains')
  const saved = () => page.locator('.domain-save.saved').waitFor()
  const refresh = async () => page.evaluate(() => window.dispatchEvent(new Event('focus')))
  const screenshot = async name => {
    await page.screenshot({ path: path.join(output, name), fullPage: true })
    result.screenshots.push(name)
  }
  const open = async () => {
    await page.getByRole('button', { name: 'agent代连', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: 'Agent 代连', exact: true })
    await dialog.getByRole('button', { name: '刷新框架', exact: true }).waitFor()
    await dialog.getByRole('button', { name: '刷新框架', exact: true }).evaluate(element => new Promise(resolve => {
      const poll = () => element.disabled ? requestAnimationFrame(poll) : resolve()
      poll()
    }))
    return dialog
  }
  const start = async (framework = '', repeat = false) => {
    const dialog = await open()
    await dialog.getByRole('combobox', { name: '识途参考框架', exact: true }).selectOption(framework)
    const before = result.starts.length
    const button = dialog.getByRole('button', { name: '生成代连预览', exact: true })
    if (repeat) await button.evaluate(element => { element.click(); element.click(); element.click() })
    else await button.click()
    await page.getByRole('button', { name: '取消生成', exact: true }).waitFor()
    assert.equal(result.starts.length, before + 1)
    const runs = await get('/api/domains/agent/runs')
    return runs.find(run => run.boardId === 'domain-llm')
  }
  const complete = async run => {
    await post(`/fixture/complete/${run.id}`)
    await refresh()
    await page.getByRole('button', { name: '应用方案', exact: true }).waitFor()
    return (await get('/api/domains/agent/runs')).find(item => item.id === run.id)
  }
  const discard = async () => {
    await page.getByRole('button', { name: '放弃方案', exact: true }).click()
    await page.getByRole('button', { name: 'agent代连', exact: true }).waitFor()
  }
  await page.goto(`${web}/tests/research-canvas-harness.html?mode=domain`, { waitUntil: 'networkidle' })
  await saved()
  await page.getByRole('button', { name: 'agent代连', exact: true }).waitFor()
  if (process.env.KNOWRA_FRAMEWORK_BASELINE === '1') {
    await screenshot('before-agent-canvas.png')
    result.steps.push('Fetched-main baseline UI rendered with the identical synthetic paper canvas')
  } else {
    const before = await fixture()
    const initial = (await live()).state.boards.find(board => board.id === 'domain-llm')
    let dialog = await open()
    const selector = dialog.getByRole('combobox', { name: '识途参考框架', exact: true })
    assert.equal(await selector.inputValue(), '')
    const choices = await selector.locator('option').allTextContents()
    assert.match(choices[1], /LLM.*同领域/)
    assert(!choices.some(label => label.includes('空领域')))
    await selector.selectOption('roadmap-llm')
    await screenshot('after-framework-selector.png')
    const afterList = await fixture()
    assert.deepEqual(afterList.roadmap, before.roadmap)
    assert.deepEqual(afterList.roadmapRuns, before.roadmapRuns)
    result.steps.push('Selector defaults to no framework, ranks same-field saved framework first, omits empty boards; listing leaves roadmap canvas revision and run statuses unchanged')
    await dialog.getByRole('button', { name: '取消', exact: true }).click()

    let run = await start('roadmap-llm', true)
    const runSnapshot = (await fixture()).runs.find(item => item.id === run.id).snapshot.framework
    assert.equal(runSnapshot.version, run.framework.version)
    assert.equal(runSnapshot.context.researchQuestion, '如何减少注意力计算成本？')
    assert.equal(runSnapshot.context.scope, '注意力效率与稀疏路由')
    assert(runSnapshot.context.report.excerpts.some(section => section.text.includes('阶段一：稀疏化；阶段二：检索。')))
    assert(runSnapshot.context.nodes.some(node => node.role === 'branch'))
    assert(runSnapshot.context.relations.some(relation => relation.structural))
    assert(runSnapshot.context.papers.some(paper => paper.sourceUrl === 'https://example.invalid/framework-source'))
    assert(!JSON.stringify(runSnapshot).includes('未应用预览'))
    const state = await live()
    await post('/api/domains/agent/runs', { boardId:'domain-llm', revision:state.revision }, 409)
    result.steps.push('Three same-frame generate clicks create one run; server rejects a second pending run; snapshot contains saved question/branch/stage/source and excludes unapplied roadmap preview')
    await post('/fixture/mutate/change')
    await post('/fixture/mutate/delete')
    await page.getByRole('combobox', { name:'领域', exact:true }).selectOption('domain-vision')
    await saved()
    await post(`/fixture/complete/${run.id}`)
    await refresh()
    assert.equal(await page.getByRole('button', { name:'应用方案', exact:true }).count(), 0)
    assert.equal(await page.locator('.domain-framework-snapshot').count(), 0)
    await page.getByRole('combobox', { name:'领域', exact:true }).selectOption('domain-llm')
    await page.getByRole('button', { name:'应用方案', exact:true }).waitFor()
    run = (await get('/api/domains/agent/runs')).find(item => item.id === run.id)
    const frozen = await fixture()
    assert.deepEqual(frozen.runs.find(item => item.id === run.id).snapshot.framework, runSnapshot)
    const calls = frozen.calls.filter(call => call.payload.focus)
    assert(calls.length > 0)
    assert(calls.every(call => call.payload.framework?.version === run.framework.version))
    assert(calls.every(call => !JSON.stringify(call.payload.framework).includes('已修改框架')))
    assert(calls.some(call => JSON.stringify(call.payload.framework).includes('example.invalid/framework-source')))
    assert(calls.every(call => call.system.includes('材料') && call.system.includes('证据')))
    const added = run.proposal.board.edges.find(edge => edge.analysis)
    assert(added)
    assert.equal(added.source, 'a'); assert.equal(added.target, 'c')
    assert(added.analysis.evidence.every(item => item.paperId === 'paper-c' && ['review','text'].includes(item.source)))
    assert(run.proposal.rejected > 0)
    assert(!run.proposal.board.edges.some(edge => edge.label.includes('同分支')))
    for (const id of ['manual','locked']) assert.deepEqual(run.proposal.board.edges.find(edge => edge.id === id), initial.edges.find(edge => edge.id === id))
    assert.deepEqual(run.proposal.board.nodes.find(node => node.id === 'a'), initial.nodes.find(node => node.id === 'a'))
    assert.equal(run.proposal.board.nodes.find(node => node.id === 'note').text, '人工整理\n论文仍是画布的主体')
    assert(run.proposal.frameworkNotes.some(note => note.includes('无法归入')))
    await screenshot('after-framework-preview.png')
    await page.locator(`[data-edge-id="${added.id}"] .domain-edge-hit`).dispatchEvent('pointerdown')
    await page.getByText('当前论文材料描述用稀疏路由替换稠密得分。', { exact:true }).waitFor()
    await page.getByRole('button', { name:'证据 1', exact:true }).click()
    await page.locator('.domain-edge-evidence blockquote').scrollIntoViewIfNeeded()
    assert.match(await page.locator('.domain-edge-evidence blockquote').innerText(), /We improve the baseline attention operator/)
    await screenshot('after-paper-evidence.png')
    result.steps.push('Real generation receives the captured framework and version after source edits/deletion, never live replacements; real grounding rejects framework-only evidence and retains valid paper quote; manual edge, locked edge/node and note survive; mismatch note shown')
    result.steps.push('Switching fields while generation finishes never displays/applies the old preview on the new canvas; returning shows the correct run')
    await page.getByRole('button', { name:'应用方案', exact:true }).evaluate(element => { element.click(); element.click() })
    await page.getByRole('button', { name:'agent代连', exact:true }).waitFor()
    await saved()
    let applied = (await live()).state.boards.find(board => board.id === 'domain-llm')
    assert.equal(applied.edges.length, 3)
    assert((await fixture()).runs.find(item => item.id === run.id).status === 'applied')
    result.steps.push('Explicit application persists the three preview edges exactly once; no roadmap writes occur during generation/application')

    await page.locator(`[data-edge-id="${added.id}"] .domain-edge-hit`).dispatchEvent('pointerdown')
    await page.getByRole('textbox', { name:'连线说明', exact:true }).fill('人工修订：只作对照，不宣称继承')
    await saved()
    applied = (await live()).state.boards.find(board => board.id === 'domain-llm')
    assert.equal(applied.edges.find(edge => edge.id === added.id).analysis, null)
    run = await start()
    assert.equal(run.framework, null)
    run = await complete(run)
    assert.equal(run.proposal.board.edges.find(edge => edge.id === added.id).label, '人工修订：只作对照，不宣称继承')
    const plainCalls = (await fixture()).calls.slice(calls.length).filter(call => call.payload.focus)
    assert(plainCalls.length > 0 && plainCalls.every(call => !call.payload.framework))
    await discard()
    result.steps.push('No-framework path invokes the original paper-only payload and preserves a generated edge after the user edits it into a manual relation')

    run = await start()
    const callCount = (await fixture()).calls.length
    await page.getByRole('button', { name:'取消生成', exact:true }).evaluate(element => { element.click(); element.click() })
    await page.getByRole('button', { name:'agent代连', exact:true }).waitFor()
    assert.equal((await post(`/fixture/complete/${run.id}`)).cancelled, true)
    assert.equal((await fixture()).calls.length, callCount)
    result.steps.push('Repeated cancellation releases the board; late generation sees cancellation and never calls the controlled model or restores a preview')

    run = await start()
    await post(`/fixture/interrupt/${run.id}`)
    await refresh()
    await page.getByRole('alert').filter({ hasText:'合成任务已中断，请重新生成。' }).waitFor()
    await page.getByRole('button', { name:'agent代连', exact:true }).waitFor()
    assert.equal((await fixture()).calls.length, callCount)
    result.steps.push('An interrupted job is reflected by the real polling route, releases readonly state and offers regeneration without a model call')

    await post('/fixture/mutate/restore')
    dialog = await open()
    await dialog.getByRole('combobox', { name:'识途参考框架', exact:true }).selectOption('roadmap-llm')
    await post('/fixture/mutate/change')
    await dialog.getByRole('button', { name:'生成代连预览', exact:true }).click()
    await dialog.getByRole('alert').waitFor()
    assert.match(await dialog.getByRole('alert').innerText(), /变化|变更|更新/)
    await dialog.getByRole('button', { name:'刷新框架', exact:true }).click()
    assert.equal(await dialog.getByRole('combobox', { name:'识途参考框架', exact:true }).inputValue(), '')
    await dialog.getByRole('button', { name:'取消', exact:true }).click()
    result.steps.push('Framework changed between selection and submission returns a recoverable conflict; refresh clears selection and requires an explicit new choice')

    dialog = await open()
    await dialog.getByRole('combobox', { name:'识途参考框架', exact:true }).selectOption('roadmap-llm')
    await post('/fixture/mutate/empty')
    await dialog.getByRole('button', { name:'生成代连预览', exact:true }).click()
    await dialog.getByRole('alert').waitFor()
    assert.match(await dialog.getByRole('alert').innerText(), /空|内容|可用|变化/)
    await dialog.getByRole('button', { name:'取消', exact:true }).click()
    await post('/fixture/mutate/restore')
    dialog = await open()
    await dialog.getByRole('combobox', { name:'识途参考框架', exact:true }).selectOption('roadmap-llm')
    await post('/fixture/mutate/delete')
    await dialog.getByRole('button', { name:'生成代连预览', exact:true }).click()
    await dialog.getByRole('alert').waitFor()
    assert.match(await dialog.getByRole('alert').innerText(), /不存在|删除|无法/)
    await screenshot('after-framework-recovery.png')
    await dialog.getByRole('button', { name:'取消', exact:true }).click()
    result.steps.push('A selected framework emptied or deleted before submission cannot silently fall back; both return actionable dialog errors')

    let injected = false
    await page.route('**/api/domains/agent/frameworks?*', route => {
      if (!injected) { injected = true; return route.fulfill({ status:503, json:{detail:'合成读取失败：可刷新或不使用框架'} }) }
      return route.fallback()
    })
    dialog = await open()
    await dialog.getByRole('alert').waitFor()
    await dialog.getByRole('button', { name:'生成代连预览', exact:true }).click()
    await page.getByRole('button', { name:'取消生成', exact:true }).waitFor()
    run = (await get('/api/domains/agent/runs')).find(item => item.boardId === 'domain-llm')
    assert.equal(run.framework, null)
    await page.getByRole('button', { name:'取消生成', exact:true }).click()
    await page.getByRole('button', { name:'agent代连', exact:true }).waitFor()
    await page.unroute('**/api/domains/agent/frameworks?*')
    result.steps.push('Framework-list read failure leaves an explicit no-framework recovery path usable without blocking paper analysis')

    await page.route('**/api/domains/agent/frameworks?*', route => route.fulfill({ status:200, json:[] }))
    dialog = await open()
    await dialog.getByText('暂无可用框架，可仅依据论文材料生成。', { exact:true }).waitFor()
    assert.equal(await dialog.getByRole('combobox', { name:'识途参考框架', exact:true }).locator('option').count(), 1)
    assert.equal(await dialog.getByRole('button', { name:'生成代连预览', exact:true }).isEnabled(), true)
    await dialog.getByRole('button', { name:'取消', exact:true }).click()
    await page.unroute('**/api/domains/agent/frameworks?*')
    result.steps.push('An empty framework list explicitly explains paper-only fallback and keeps it available (controlled list response)')

    let releaseList, enterList
    const listGate = new Promise(resolve => { releaseList = resolve })
    const listEntered = new Promise(resolve => { enterList = resolve })
    await page.route('**/api/domains/agent/frameworks?*', async route => {
      enterList()
      await listGate
      await route.fulfill({ status:200, json:[] }).catch(() => undefined)
    })
    const startsBeforeSwitch = result.starts.length
    await page.getByRole('button', { name:'agent代连', exact:true }).click()
    await listEntered
    await page.getByRole('dialog', { name:'Agent 代连', exact:true }).getByRole('button', { name:'取消', exact:true }).click()
    await page.getByRole('combobox', { name:'领域', exact:true }).selectOption('domain-vision')
    releaseList()
    await saved()
    assert.equal(await page.getByRole('dialog', { name:'Agent 代连', exact:true }).count(), 0)
    assert.equal(result.starts.length, startsBeforeSwitch)
    await page.unroute('**/api/domains/agent/frameworks?*')
    await page.getByRole('combobox', { name:'领域', exact:true }).selectOption('domain-llm')
    await saved()
    result.steps.push('Closing the selector and switching fields during a delayed framework read discards its response without reopening UI or starting a run')

    run = await start()
    run = await complete(run)
    await post('/fixture/mutate/stale-domain')
    await page.getByRole('button', { name:'应用方案', exact:true }).click()
    await page.getByRole('alert').filter({ hasText:/原画布已变化/ }).waitFor()
    assert.equal((await fixture()).runs.find(item => item.id === run.id).status, 'preview')
    assert((await live()).state.boards.find(board => board.id === 'domain-llm').nodes[0].text.includes('外部变化'))
    await discard()
    result.steps.push('A stale preview apply returns 409, preserves the external canvas update and can be discarded safely')
    await page.reload({ waitUntil:'networkidle' })
    await saved()
    await page.getByRole('combobox', { name:'领域', exact:true }).selectOption('domain-empty')
    await saved()
    assert.equal(await page.getByRole('button', { name:'agent代连', exact:true }).isDisabled(), true)
    result.steps.push('An empty paper canvas cannot start agent generation')
    result.modelCalls = (await fixture()).calls.length
    result.frameworkVersion = runSnapshot.version
    result.expectedHttpFailures = result.failedResponses.map(item => item.status)
    assert(result.failedResponses.every(item => [404,409,422,503].includes(item.status)))
  }
  assert.deepEqual(result.pageErrors, [])
  assert.deepEqual(result.blockedRequests, [])
  result.success = true
} catch (error) {
  result.success = false
  result.error = error.stack || String(error)
  if (page) await page.screenshot({ path:path.join(output,'failure.png'), fullPage:true }).catch(() => undefined)
  process.exitCode = 1
} finally {
  await browser.close()
  await fs.writeFile(path.join(output, 'browser-result.json'), JSON.stringify(result, null, 2) + '\n')
  if (!result.success) console.error(result.error)
}
