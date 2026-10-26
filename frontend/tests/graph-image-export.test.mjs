import assert from 'node:assert/strict'
import test from 'node:test'
import { graphImageSize, withPngDensity } from '../src/lib/graphImageExport.ts'

test('PNG detail is 300/96 pixels per CSS pixel; large exports stay within both budgets', () => {
  assert.deepEqual(graphImageSize(1000, 600), { width: 3125, height: 1875, scale: 3.125, limited: false })
  for (const [width, height] of [[6200, 3100], [500, 8000], [4000, 4000]]) {
    const size = graphImageSize(width, height)
    assert.ok(size.limited)
    assert.ok(size.width <= 8192 && size.height <= 8192)
    assert.ok(size.width * size.height <= 24_000_000)
    assert.ok(size.scale >= 1)
  }
})

test('empty, invalid, or excessively spread layouts fail before allocating a bitmap', () => {
  for (const [width, height] of [[0, 4], [NaN, 1], [Infinity, 1], [-1, 10], [900000, 100], [5000, 5000]]) {
    assert.throws(() => graphImageSize(width, height))
  }
})

function chunks(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const result = []
  for (let offset = 8; offset < bytes.length;) {
    const size = view.getUint32(offset)
    result.push({ type: String.fromCharCode(...bytes.subarray(offset + 4, offset + 8)),
      data: bytes.subarray(offset + 8, offset + 8 + size), raw: bytes.subarray(offset, offset + 12 + size) })
    offset += size + 12
  }
  return result
}

test('density is real 300 DPI pHYs metadata, replaces prior metadata, and preserves image data', () => {
  const original = new Uint8Array(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7V8AAAAASUVORK5CYII=', 'base64'))
  const output = withPngDensity(withPngDensity(original, 72), 300)
  const parts = chunks(output)
  const density = parts.filter(chunk => chunk.type === 'pHYs')
  assert.equal(density.length, 1)
  assert.deepEqual([...density[0].data], [0, 0, 46, 35, 0, 0, 46, 35, 1])
  assert.deepEqual([...density[0].raw.subarray(17)], [120, 165, 63, 118])
  assert.deepEqual(parts.filter(chunk => chunk.type !== 'pHYs').map(chunk => chunk.raw), chunks(original).map(chunk => chunk.raw))
  assert.throws(() => withPngDensity(original.subarray(0, 40)))
})

test('a stalled browser export has a deadline and successful work is returned', async context => {
  const { withGraphExportDeadline } = await import('../src/lib/graphImageExport.ts')
  context.mock.timers.enable({ apis: ['setTimeout'] })
  assert.equal(await withGraphExportDeadline(Promise.resolve('ready')), 'ready')
  const stalled = withGraphExportDeadline(new Promise(() => {}))
  const rejected = assert.rejects(stalled, /图像导出超时/)
  context.mock.timers.tick(15_000)
  await rejected
})
