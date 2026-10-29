import test from 'node:test'
import assert from 'node:assert/strict'
import { measureRenderedGraph, sharedOverview, cropAtViewport } from './domain-layout-metrics.mjs'

const path = (id, points, source = `${id}-source`, target = `${id}-target`) => ({
  id, source, target, points,
  length: points.slice(1).reduce((sum, point, i) => sum + Math.hypot(point.x - points[i].x, point.y - points[i].y), 0),
  bounds: { x: Math.min(...points.map(point => point.x)), y: Math.min(...points.map(point => point.y)),
    width: Math.max(...points.map(point => point.x)) - Math.min(...points.map(point => point.x)),
    height: Math.max(...points.map(point => point.y)) - Math.min(...points.map(point => point.y)) },
})
const measure = routes => measureRenderedGraph({ routes, nodes: [], canvas: { width: 1400, height: 900 } }, { nodes: [] })

test('Actual orthogonal crossings are separated from pairs that share an endpoint', () => {
  const a = path('a', [{ x: 0, y: 0 }, { x: 100, y: 0 }])
  const b = path('b', [{ x: 50, y: -50 }, { x: 50, y: 50 }])
  assert.equal(measure([a, b]).corePaths.crossings, 1)
  assert.equal(measure([a, b]).corePaths.sharedEndpointCrossings, 0)
  assert.equal(measure([a, b]).corePaths.totalCrossings, 1)
  b.source = a.source
  assert.equal(measure([a, b]).corePaths.crossings, 0)
  assert.equal(measure([a, b]).corePaths.sharedEndpointCrossings, 1)
  assert.equal(measure([a, b]).corePaths.totalCrossings, 1)
})

test('Collinear length is exact, endpoint stubs reported separately, parallel proximity has explicit thresholds', () => {
  const a = path('a', [{ x: 0, y: 0 }, { x: 100, y: 0 }])
  const b = path('b', [{ x: 50, y: 0 }, { x: 150, y: 0 }])
  assert.equal(measure([a, b]).fullPaths.collinearOverlapLength, 50)
  assert.equal(measure([a, b]).corePaths.collinearOverlapLength, 2)
  const near = path('near', [{ x: 0, y: 10 }, { x: 100, y: 10 }])
  assert.equal(measure([a, near]).corePaths.nearParallel12Length, 52)
  assert.equal(measure([a, near]).corePaths.collinearOverlapLength, 0)
  const wider = path('wider', [{ x: 0, y: 16 }, { x: 100, y: 16 }])
  assert.equal(measure([a, wider]).corePaths.nearParallel12Length, 0)
  assert.equal(measure([a, wider]).corePaths.nearParallel20Length, 52)
})

test('One shared union viewport includes both complete graphs without independent fit or hidden clipping', () => {
  const a = { bounds: { x: -200, y: -100, width: 2500, height: 1600 }, canvas: { width: 1540, height: 950 } }
  const b = { bounds: { x: -350, y: -200, width: 2900, height: 2200 }, canvas: a.canvas }
  const viewport = sharedOverview(a, b)
  assert.equal(viewport.zoom, 0.34)
  assert.deepEqual(cropAtViewport(a, viewport), { left: 0, top: 0, right: 0, bottom: 0 })
  assert.deepEqual(cropAtViewport(b, viewport), { left: 0, top: 0, right: 0, bottom: 0 })
  assert(cropAtViewport(b, { zoom: 0.75, x: 0, y: 0 }).bottom > 0)
})
