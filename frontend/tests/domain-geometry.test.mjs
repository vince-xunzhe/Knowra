import assert from 'node:assert/strict'
import test from 'node:test'
import { computeDomainEdgeGeometries } from '../src/lib/domainGeometry.ts'

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
