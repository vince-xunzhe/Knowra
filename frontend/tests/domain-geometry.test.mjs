import assert from 'node:assert/strict'
import test from 'node:test'
import { computeDomainEdgeGeometries } from '../src/lib/domainGeometry.ts'
import { arrangeDomainLayout } from '../src/lib/domainLayout.ts'
import { createSpacingFixture } from './domain-layout-fixture.mjs'
import { measureRenderedGraph } from './domain-layout-metrics.mjs'

const node = (id, x, y, more = {}) => ({ id, kind: 'paper', x, y, width: 280, height: 150,
  paperId: id, groupId: null, text: '', locked: false, ...more })
const edge = (id, source, target, more = {}) => ({ id, source, target, label: 'Synthetic relation', locked: false, ...more })
const board = (nodes, edges) => ({ id: 'synthetic', name: 'Synthetic geometry', nodes, edges, viewport: { x: 0, y: 0, zoom: 1 } })

function crossing(a, b, rect) {
  if (a.y === b.y) return a.y > rect.y && a.y < rect.y + rect.height && Math.max(a.x, b.x) > rect.x && Math.min(a.x, b.x) < rect.x + rect.width
  assert.equal(a.x, b.x, 'every segment stays orthogonal')
  return a.x > rect.x && a.x < rect.x + rect.width && Math.max(a.y, b.y) > rect.y && Math.min(a.y, b.y) < rect.y + rect.height
}

function assertClear(current, routes, measured = {}) {
  assert.equal(routes.size, current.edges.length)
  for (const relation of current.edges) {
    const geometry = routes.get(relation.id)
    assert.ok(geometry.d.startsWith('M '))
    assert.equal(geometry.obstructed, false, relation.id)
    const points = geometry.points
    points.slice(1).forEach((point, index) => {
      for (const card of current.nodes.filter(n => n.kind !== 'group')) {
        if (index === 0 && card.id === relation.source) continue
        if (index === points.length - 2 && card.id === relation.target) continue
        assert.equal(crossing(points[index], point, { ...card, ...measured[card.id] }), false, `${relation.id} crosses ${card.id}`)
      }
    })
    assert.ok(points.slice(1).some((point, index) => {
      const a = points[index], p = { x: geometry.labelX, y: geometry.labelY }
      return (a.x === point.x && p.x === a.x && p.y >= Math.min(a.y, point.y) && p.y <= Math.max(a.y, point.y)) ||
        (a.y === point.y && p.y === a.y && p.x >= Math.min(a.x, point.x) && p.x <= Math.max(a.x, point.x))
    }), 'label and lock lie on their actual route')
  }
}

test('long jumps route around intervening paper cards and text notes without changing relationships', () => {
  const current = board([node('a', 0, 150), node('b', 450, 80), node('c', 900, 150),
    node('note', 450, 310, { kind: 'text', height: 200, text: 'Preserved note' })],
  [edge('jump', 'a', 'c', { locked: true, analysis: { relation: 'builds_on', evidence: [{ quote: 'Synthetic evidence' }] } })])
  const before = structuredClone(current)
  const routes = computeDomainEdgeGeometries(current)
  assertClear(current, routes)
  assert.deepEqual(current, before)
  assert.ok(routes.get('jump').points.length >= 5)
})

test('same-column, reversed, self and parallel relations retain distinct finite routes', () => {
  const current = board([node('a', 0, 0), node('b', 0, 360), node('c', 500, 180)], [
    edge('down', 'a', 'b'), edge('up', 'b', 'a'), edge('reverse', 'c', 'a'),
    edge('parallel-1', 'a', 'c'), edge('parallel-2', 'a', 'c'), edge('self', 'c', 'c'),
  ])
  const routes = computeDomainEdgeGeometries(current)
  assertClear(current, routes)
  assert.notEqual(routes.get('parallel-1').d, routes.get('parallel-2').d)
  assert.notEqual(routes.get('down').d, routes.get('up').d)
  assert.equal(routes.get('down').vertical, true)
  for (const geometry of routes.values()) assert.ok(geometry.points.every(point => Number.isFinite(point.x) && Number.isFinite(point.y)))
})

test('rendered long-title heights and widths determine obstacle bounds and ports', () => {
  const current = board([node('a', 0, 0), node('b', 450, 0), node('c', 900, 0)], [edge('jump', 'a', 'c')])
  const measured = { a: { width: 300, height: 400 }, b: { width: 320, height: 540 }, c: { width: 300, height: 330 } }
  const routes = computeDomainEdgeGeometries(current, measured)
  assertClear(current, routes, measured)
  assert.equal(routes.get('jump').sx, 300)
  assert.equal(routes.get('jump').sy, 200)
  assert.equal(routes.get('jump').ty, 165)
})

test('dense cyclic graph is deterministic across edge and node input order; group backgrounds do not block edges', () => {
  const nodes = Array.from({ length: 24 }, (_, index) => node(`n${index}`, Math.floor(index / 4) * 500, (index % 4) * 320))
  nodes.push(node('group-background', -40, -40, { kind: 'group', width: 2600, height: 1320 }))
  const edges = []
  for (let index = 0; index < 24; index++) {
    edges.push(edge(`chain-${index}`, `n${index}`, `n${(index + 1) % 24}`))
    if (index < 18) edges.push(edge(`long-${index}`, `n${index}`, `n${index + 6}`))
  }
  const current = board(nodes, edges), routes = computeDomainEdgeGeometries(current)
  assertClear(current, routes)
  assert.deepEqual(computeDomainEdgeGeometries(current), routes)
  assert.deepEqual(computeDomainEdgeGeometries(board([...nodes].reverse(), [...edges].reverse())), routes)
})

test('existing overlapping locked endpoint cards keep the relation visible and explicitly flag obstruction', () => {
  const current = board([node('a', 0, 0, { locked: true }), node('b', 80, 20, { locked: true })], [edge('overlap', 'a', 'b', { locked: true })])
  const routes = computeDomainEdgeGeometries(current)
  assert.equal(routes.size, 1)
  assert.equal(routes.get('overlap').obstructed, true)
  assert.ok(routes.get('overlap').d)
  assert.equal(current.edges[0].locked, true)
})

test('obstacle maze falls back to a multi-turn route when a single outer lane cannot escape', () => {
  const current = board([node('source', 0, 0), node('target', 1100, 0),
    node('upper-wall', 300, -400, { kind: 'text', width: 250, height: 390 }),
    node('lower-wall', 300, 170, { kind: 'text', width: 250, height: 630 }),
    node('middle-wall', 800, -80, { kind: 'text', width: 200, height: 300 }),
  ], [edge('maze', 'source', 'target')])
  const routes = computeDomainEdgeGeometries(current)
  assertClear(current, routes)
  assert.ok(routes.get('maze').points.length >= 6)
})

test('endpoint stubs stop before a nearby fixed card instead of falsely blocking a route', () => {
  const current = board([node('source', 0, 0, { locked: true, groupId: 'fixed-group' }),
    node('neighbor', 320, 0, { locked: true, groupId: 'fixed-group' }), node('target', 1100, 400),
    node('fixed-group', -20, -40, { kind: 'group', width: 640, height: 230, locked: true }),
  ], Array.from({ length: 5 }, (_, index) => edge(`relation-${index}`, 'source', 'target')))
  const original = structuredClone(current), routes = computeDomainEdgeGeometries(current)
  assertClear(current, routes)
  assert.deepEqual(current, original)
})

test('stub clearance holds left, right, above and below for 24–48px gaps and self relations', () => {
  const transforms = [
    n => n,
    n => ({ ...n, x: -n.x - n.width }),
    n => ({ ...n, x: n.y, y: n.x, width: n.height, height: n.width }),
    n => ({ ...n, x: n.y, y: -n.x - n.width, width: n.height, height: n.width }),
  ]
  for (const gap of [24, 32, 40, 48]) for (const transform of transforms) {
    const current = board([node('source', 0, 0, { locked: true }), node('neighbor', 280 + gap, 0, { locked: true }),
      node('target', 1100, 0)].map(transform),
    [...Array.from({ length: 5 }, (_, index) => edge(`relation-${index}`, 'source', 'target')), edge('self', 'source', 'source')])
    const before = structuredClone(current)
    assertClear(current, computeDomainEdgeGeometries(current))
    assert.deepEqual(current, before)
  }
})

test('large drawings explicitly simplify routing without omitting or mutating any relationship', () => {
  const nodes = Array.from({ length: 300 }, (_, index) => node(`n${index}`, Math.floor(index / 10) * 500, (index % 10) * 300))
  const edges = Array.from({ length: 1200 }, (_, index) => edge(`e${index}`, `n${index % 300}`, `n${(index + 17) % 300}`))
  edges.push(edge('self', 'n0', 'n0'))
  const current = board(nodes, edges), before = structuredClone(current)
  const routes = computeDomainEdgeGeometries(current)
  assert.equal(routes.size, edges.length)
  assert.ok([...routes.values()].every(value => value.simplified && value.d && Number.isFinite(value.labelX)))
  assert.deepEqual(current, before)
  assert.deepEqual(computeDomainEdgeGeometries(current), routes)
  const self = routes.get('self')
  for (let index = 1; index < self.points.length - 2; index++) assert.equal(crossing(self.points[index], self.points[index + 1], nodes[0]), false)
})

test('many parallel edges also select the explicit bounded route mode even on a small board', () => {
  const current = board([node('a', 0, 0), node('b', 600, 0)],
    Array.from({ length: 801 }, (_, index) => edge(`parallel-${index}`, 'a', 'b')))
  const routes = computeDomainEdgeGeometries(current)
  assert.equal(routes.size, 801)
  assert.ok([...routes.values()].every(value => value.simplified))
})

// Fixed positions isolate routing from layout spacing changes. No user titles,
// relationships, measurements or data are used in this congested graph.
function crowdedDrawing(size, multiplier = 4) {
  return board(Array.from({ length: size }, (_, index) => node(`n${index}`, Math.floor(index / 4) * 500,
    (index % 4) * 260, { height: 160 })),
  Array.from({ length: size * multiplier }, (_, index) => edge(`e${index}`, `n${index % size}`,
    `n${(index % size + Math.floor(index / size) * 13 + 1) % size}`)))
}

/** Independent geometric measurements; does not call the router's cost code. */
function crowdingMetrics(routes) {
  const paths = [...routes.values()]
  const segments = paths.flatMap((geometry, edgeIndex) => geometry.points.slice(1).map((b, index) => {
    const a = geometry.points[index], horizontal = a.y === b.y
    return { edgeIndex, horizontal, axis: horizontal ? a.y : a.x,
      from: horizontal ? Math.min(a.x, b.x) : Math.min(a.y, b.y),
      to: horizontal ? Math.max(a.x, b.x) : Math.max(a.y, b.y) }
  }))
  let shared = 0, near = 0, crossings = 0
  for (let index = 0; index < segments.length; index++) for (let other = index + 1; other < segments.length; other++) {
    const a = segments[index], b = segments[other]
    if (a.edgeIndex === b.edgeIndex) continue
    if (a.horizontal === b.horizontal) {
      const overlap = Math.min(a.to, b.to) - Math.max(a.from, b.from), distance = Math.abs(a.axis - b.axis)
      if (overlap <= 24) continue
      if (distance < 0.5) shared += overlap
      else if (distance < 12) near += overlap
    } else if (a.axis > b.from && a.axis < b.to && b.axis > a.from && b.axis < a.to) crossings++
  }
  const points = paths.flatMap(path => path.points)
  return {
    sharedLength: Math.round(shared), nearParallelLength: Math.round(near), crossings,
    totalLength: Math.round(segments.reduce((sum, segment) => sum + segment.to - segment.from, 0)),
    maxLength: Math.max(...paths.map(path => path.points.slice(1).reduce((sum, point, index) =>
      sum + Math.abs(point.x - path.points[index].x) + Math.abs(point.y - path.points[index].y), 0))),
    bends: paths.reduce((sum, path) => sum + path.points.length - 2, 0),
    bounds: { left: Math.min(...points.map(point => point.x)), top: Math.min(...points.map(point => point.y)),
      right: Math.max(...points.map(point => point.x)), bottom: Math.max(...points.map(point => point.y)) },
  }
}

test('congestion-aware channels reduce shared and near-parallel runs without excessive detours', t => {
  const current = crowdedDrawing(23), original = structuredClone(current)
  const routes = computeDomainEdgeGeometries(current), measured = crowdingMetrics(routes)
  // Measured from a392c6f using this exact 23-node / 92-edge fixed drawing.
  // Keep explicit baseline figures rather than embedding the old implementation.
  const baseline = { sharedLength: 27366, nearParallelLength: 109095, crossings: 973,
    totalLength: 85306, maxLength: 2931.7333333333336, bends: 214,
    bounds: { left: 140, top: 28.8, right: 2640, bottom: 911.2 } }
  assertClear(current, routes)
  assert.deepEqual(current, original)
  assert.deepEqual(computeDomainEdgeGeometries(board([...current.nodes].reverse(), [...current.edges].reverse())), routes)
  assert.ok(measured.sharedLength < baseline.sharedLength * 0.5, 'long shared trunks must separate')
  assert.ok(measured.nearParallelLength < baseline.nearParallelLength * 0.7, 'parallel routes must have visible clearance')
  assert.ok(measured.crossings <= baseline.crossings, 'do not merely trade all overlaps for crossings')
  assert.ok(measured.totalLength <= baseline.totalLength * 1.12, 'do not buy clarity with huge detours')
  assert.ok(measured.maxLength <= baseline.maxLength + 360)
  assert.ok(measured.bends <= baseline.bends * 1.2)
  assert.ok(measured.bounds.top >= baseline.bounds.top - 180 && measured.bounds.bottom <= baseline.bounds.bottom + 180)
  assert.ok(measured.bounds.left >= baseline.bounds.left - 180 && measured.bounds.right <= baseline.bounds.right + 180)
  t.diagnostic(JSON.stringify({ fixture: 'fixed 23 papers / 92 relations', baselineCommit: 'a392c6f', baseline, measured }))
})

test('staggered 74-edge browser fixture changes lanes rather than sharing a seven-pixel corridor', t => {
  const fixture = createSpacingFixture().state.boards[0]
  // Actual dimensions from the isolated synthetic browser fixture, including
  // long titles/notes. This reproduces the narrow inter-column corridor that
  // a single-lane-only candidate search incorrectly preferred for many edges.
  const sizes = Object.fromEntries(fixture.nodes.map(n => [n.id, { width: n.width,
    height: n.kind === 'group' ? n.height : n.kind === 'text' ? 150 :
      n.id === 'n6' ? 156 : n.id === 'n14' ? 160 : ['n1', 'n5', 'n9', 'n18', 'n19', 'n20', 'n21', 'n22'].includes(n.id) ? 101 : 121 }]))
  const current = arrangeDomainLayout(fixture, sizes).board, original = structuredClone(current)
  assert.equal(current.edges.length, 74)
  const routes = computeDomainEdgeGeometries(current, sizes)
  assertClear(current, routes, sizes)
  assert.deepEqual(current, original)
  const graph = { nodes: current.nodes.map(n => ({ ...n, ...sizes[n.id] })),
    actualEdgeIds: current.edges.map(e => e.id), canvas: { width: 1545, height: 968 },
    routes: current.edges.map(e => {
      const g = routes.get(e.id), x = Math.min(...g.points.map(p => p.x)), y = Math.min(...g.points.map(p => p.y))
      return { ...e, ...g, length: g.points.slice(1).reduce((sum, p, i) => sum + Math.abs(p.x - g.points[i].x) + Math.abs(p.y - g.points[i].y), 0),
        bounds: { x, y, width: Math.max(...g.points.map(p => p.x)) - x, height: Math.max(...g.points.map(p => p.y)) - y } }
    }) }
  const measured = measureRenderedGraph(graph, current)
  // Independently measured a392c6f arranged browser baseline; all 74 edges are
  // included, with the shared metric's fixed 24px endpoint trim.
  assert.ok(measured.corePaths.collinearOverlapLength < 13103.66 * 0.8)
  assert.ok(measured.corePaths.nearParallel12Length < 51493 * 0.6)
  assert.ok(measured.corePaths.crossings <= 303)
  assert.ok(measured.corePaths.sharedEndpointCrossings <= 155)
  assert.ok(measured.corePaths.crossings + measured.corePaths.sharedEndpointCrossings <= 458)
  assert.ok(measured.fullPaths.crossings <= 344)
  assert.ok(measured.fullPaths.sharedEndpointCrossings <= 198)
  assert.ok(measured.fullPaths.crossings + measured.fullPaths.sharedEndpointCrossings <= 542)
  assert.equal(measured.edgeNodeHits, 0)
  assert.ok(measured.paths.totalLength <= 64300.66 * 1.15)
  assert.ok(measured.paths.longestLength <= 2399.71 * 1.2)
  assert.ok(measured.paths.maxBends <= 8)
  t.diagnostic(JSON.stringify({ fixture: '74-edge synthetic browser geometry',
    core: { ...measured.corePaths, details: undefined }, full: { ...measured.fullPaths, details: undefined },
    paths: measured.paths, bounds: measured.bounds, hits: measured.hitPairs }))
})

test('dense and schema-sized routing stay bounded and retain every edge', t => {
  const results = []
  for (const size of [23, 100, 200, 300, 10000]) {
    const current = crowdedDrawing(size, size === 10000 ? 2 : 4)
    const started = performance.now(), routes = computeDomainEdgeGeometries(current), elapsedMs = performance.now() - started
    assert.equal(routes.size, current.edges.length)
    assert.ok([...routes.values()].every(geometry => geometry.d && Number.isFinite(geometry.labelX) && Number.isFinite(geometry.labelY)))
    if (size > 200) assert.ok([...routes.values()].every(geometry => geometry.simplified))
    // A loose regression guard catches accidental N×E or E² work in the large
    // fallback; diagnostics retain actual timings without promising a frame rate.
    assert.ok(elapsedMs < 3000, `${size}-node routing took ${elapsedMs}ms`)
    results.push({ nodes: size, edges: current.edges.length, routes: routes.size, elapsedMs: Math.round(elapsedMs * 10) / 10 })
  }
  t.diagnostic(JSON.stringify({ node: process.versions.node, timings: results }))
})
