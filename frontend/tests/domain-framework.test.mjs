import assert from 'node:assert/strict'
import test from 'node:test'
import axios from 'axios'

// Capture the real client contract without contacting a server or provider.
const requests = []
axios.defaults.adapter = async config => {
  requests.push(config)
  return { data: config.method === 'get' ? [] : { id: 'synthetic-run' }, status: 200, statusText: 'OK', headers: {}, config }
}
const { listDomainFrameworks, startDomainRun } = await import('../src/api/domains.ts')

test('no-framework requests preserve the domain contract and omit roadmap-only policy', async () => {
  await startDomainRun('domain-a', 7, { 'node-a': 180 }, 'refresh')
  const request = requests.at(-1)
  assert.equal(request.baseURL, '/api/domains')
  assert.equal(request.url, '/agent/runs')
  assert.deepEqual(JSON.parse(request.data), { boardId: 'domain-a', revision: 7, heights: { 'node-a': 180 } })
})

test('an explicit framework includes the selected version with the target canvas revision', async () => {
  const framework = { boardId: 'roadmap-a', version: 'a'.repeat(64) }
  await startDomainRun('domain-b', 8, {}, undefined, framework)
  assert.deepEqual(JSON.parse(requests.at(-1).data), { boardId: 'domain-b', revision: 8, heights: {}, framework })
  assert.deepEqual(framework, { boardId: 'roadmap-a', version: 'a'.repeat(64) })
})

test('framework discovery is a cancellable domain read scoped to the selected board', async () => {
  const controller = new AbortController()
  assert.deepEqual(await listDomainFrameworks('domain-c', controller.signal), [])
  const request = requests.at(-1)
  assert.equal(request.method, 'get')
  assert.equal(request.baseURL, '/api/domains')
  assert.equal(request.url, '/agent/frameworks')
  assert.deepEqual(request.params, { boardId: 'domain-c' })
  assert.equal(request.signal, controller.signal)
  controller.abort()
  const before = requests.length
  await assert.rejects(listDomainFrameworks('domain-c', controller.signal), error => axios.isCancel(error))
  assert.equal(requests.length, before)
})
