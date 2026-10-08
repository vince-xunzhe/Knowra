import assert from 'node:assert/strict'
import test from 'node:test'
import { readWithRetry, singleFlight } from '../src/api/polling.ts'

test('a timed-out status read reconnects without failing the task', async () => {
  let calls = 0
  const attempts = []
  const waits = []
  const result = await readWithRetry(async () => {
    if (++calls < 3) throw { code: 'ECONNABORTED' }
    return { running: true, done: 1 }
  }, { onRetry: n => attempts.push(n), sleep: async ms => { waits.push(ms) } })
  assert.deepEqual(result, { running: true, done: 1 })
  assert.deepEqual(attempts, [1, 2])
  assert.deepEqual(waits, [1500, 3000])
})

test('persistent connection errors report unknown task status, not task failure', async () => {
  let calls = 0
  const waits = []
  await assert.rejects(readWithRetry(async () => {
    calls += 1
    throw { response: { status: 503 } }
  }, { sleep: async ms => { waits.push(ms) } }), /后台任务可能仍在运行/)
  assert.equal(calls, 6)
  assert.deepEqual(waits, [1500, 3000, 6000, 8000, 8000])
})

test('authorization and programming errors are not retried', async () => {
  for (const error of [{ response: { status: 401 } }, new Error('bad data')]) {
    let calls = 0
    await assert.rejects(readWithRetry(async () => {
      calls += 1
      throw error
    }), actual => actual === error)
    assert.equal(calls, 1)
  }
})

test('multiple pollers share a pending read, but the next tick fetches fresh data', async () => {
  let resolve
  let calls = 0
  const read = singleFlight(() => {
    calls += 1
    return new Promise(done => { resolve = done })
  })
  const first = read()
  assert.equal(read(), first)
  assert.equal(calls, 1)
  resolve({ running: true })
  await first
  const next = read()
  assert.equal(calls, 2)
  resolve({ running: false })
  assert.deepEqual(await next, { running: false })
})

test('failed reads release the single-flight slot', async () => {
  let calls = 0
  const read = singleFlight(async () => {
    if (++calls === 1) throw new Error('offline')
    return 'reconnected'
  })
  await assert.rejects(read(), /offline/)
  assert.equal(await read(), 'reconnected')
})
