import assert from 'node:assert/strict'
import test from 'node:test'
import { arrangeDomainLayout } from '../src/lib/domainLayout.ts'

const node = (id, overrides = {}) => ({ id, kind: 'paper', x: 100, y: 100, width: 280,
  height: 160, paperId: `paper-${id}`, groupId: null, text: `Note ${id}`, locked: false, ...overrides })
const edge = (source, target, overrides = {}) => ({ id: `${source}-${target}`, source, target,
  label: '人工关系 — 保留完整证据', locked: false, ...overrides })
const board = (nodes, edges = []) => ({ id: 'synthetic', name: '合成布局验收', category: 'Synthetic',
  researchKeywords: ['unchanged'], nodes, edges, viewport: { x: 53, y: -24, zoom: 0.75 } })
const freeze = value => {
  if (value && typeof value === 'object') { Object.freeze(value); Object.values(value).forEach(freeze) }
  return value
}
const semantic = value => ({ ...value, nodes: value.nodes.map(({ x: _x, y: _y, ...rest }) => rest) })
const positions = value => Object.fromEntries([...value.nodes].sort((a, b) => a.id.localeCompare(b.id)).map(n => [n.id, [n.x, n.y]]))
const overlap = (a, b, sizes = {}) => {
  const ar = { ...a, ...sizes[a.id] }, br = { ...b, ...sizes[b.id] }
  return ar.x < br.x + br.width && ar.x + ar.width > br.x && ar.y < br.y + br.height && ar.y + ar.height > br.y
}
function assertNoMovableOverlap(value, sizes = {}) {
  for (const a of value.nodes.filter(n => n.kind === 'paper' && !n.locked && !n.groupId)) {
    for (const b of value.nodes) if (a.id !== b.id) assert(!overlap(a, b, sizes), `${a.id} overlaps ${b.id}`)
  }
}

test('dense fanout: removes measured-card overlaps without changing content or mutating input', () => {
  const nodes = Array.from({ length: 24 }, (_, i) => node(`n${String(i).padStart(2, '0')}`))
  const edges = []
  for (let layer = 0; layer < 3; layer++) for (let a = 0; a < 6; a++) for (let b = 0; b < 6; b++) {
    edges.push(edge(nodes[layer * 6 + a].id, nodes[(layer + 1) * 6 + b].id))
  }
  edges[0].locked = true
  edges[0].analysis = { relation: 'improves', certainty: 'explicit', reason: 'Synthetic evidence remains byte-for-byte',
    evidence: [{ paperId: 'paper-n00', source: 'text', locator: 'Synthetic §2', page: null, quote: 'Public synthetic quotation.' }] }
  const input = freeze(board(nodes, edges)), original = JSON.stringify(input)
  const sizes = Object.fromEntries(nodes.map((n, i) => [n.id, { width: 280 + (i % 3) * 40, height: 180 + (i % 5) * 65 }]))
  const result = arrangeDomainLayout(input, sizes)
  assert.equal(result.moved, 24); assert.equal(result.fixed, 0); assert.equal(result.fixedOverlaps, 0)
  assertNoMovableOverlap(result.board, sizes)
  assert.deepEqual(semantic(result.board), semantic(input))
  assert.equal(JSON.stringify(input), original)
  assert.equal(result.board.edges, input.edges)
  assert.equal(result.board.viewport, input.viewport)
  assert.equal(arrangeDomainLayout(result.board, sizes).board, result.board)
})

test('cycles share a layer; descendants retain forward layers; duplicate and self edges survive', () => {
  const input = board(['a', 'b', 'c', 'd', 'e'].map(id => node(id)), [
    edge('a', 'b'), edge('b', 'c'), edge('c', 'a', { locked: true }), edge('c', 'd'), edge('d', 'e'),
    edge('a', 'a'), edge('a', 'b', { id: 'parallel', label: 'second manual relation' }),
  ])
  const result = arrangeDomainLayout(input)
  const byId = Object.fromEntries(result.board.nodes.map(n => [n.id, n]))
  assert.equal(byId.a.x, byId.b.x); assert.equal(byId.b.x, byId.c.x)
  assert(byId.d.x > byId.c.x); assert(byId.e.x > byId.d.x)
  assertNoMovableOverlap(result.board)
  assert.equal(result.board.edges, input.edges)
})

test('separate components and isolated nodes pack without overlap; old positions and input order do not affect layout', () => {
  const input = board(['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((id, i) => node(id, { x: i * 1983, y: -i * 111 })),
    [edge('a', 'b'), edge('b', 'c'), edge('d', 'e')])
  const result = arrangeDomainLayout(input)
  assertNoMovableOverlap(result.board)
  const changedInput = { ...input, nodes: [...input.nodes].reverse().map(n => ({ ...n, x: -99999, y: 99999 })),
    edges: [...input.edges].reverse() }
  assert.deepEqual(positions(arrangeDomainLayout(changedInput).board), positions(result.board))
  assert.equal(arrangeDomainLayout(result.board).moved, 0)
})

test('locked cards, all groups/children and notes are fixed obstacles, including children outside their container', () => {
  const input = freeze(board([
    node('locked', { locked: true, x: 80, y: 80 }),
    node('group', { kind: 'group', paperId: null, x: 600, y: 40, width: 640, height: 420 }),
    node('group-child', { groupId: 'group', x: 640, y: 120 }),
    node('locked-child', { groupId: 'group', locked: true, x: 910, y: 120 }),
    node('outside-child', { groupId: 'group', x: 1250, y: 500 }),
    node('locked-group', { kind: 'group', paperId: null, locked: true, x: 1600, y: 40, width: 640, height: 420 }),
    node('inherited-lock', { groupId: 'locked-group', x: 1650, y: 100 }),
    node('note', { kind: 'text', paperId: null, x: 80, y: 600, text: '用户备注不得移动' }),
    node('a'), node('b'), node('c'),
  ], [edge('locked', 'a', { locked: true }), edge('a', 'b'), edge('b', 'c'), edge('c', 'group-child')]))
  const result = arrangeDomainLayout(input)
  assert.equal(result.fixed, 8); assert.equal(result.moved, 3)
  for (const fixed of input.nodes.slice(0, 8)) assert.equal(result.board.nodes.find(n => n.id === fixed.id), fixed)
  assertNoMovableOverlap(result.board)
  assert.deepEqual(semantic(result.board), semantic(input))
  assert.equal(arrangeDomainLayout(result.board).moved, 0)
})

test('fixed overlaps are reported while intentional group containment is excluded, with an exact no-op', () => {
  const input = board([
    node('group', { kind: 'group', width: 700, height: 600 }),
    node('a', { groupId: 'group', x: 150, y: 160 }),
    node('b', { groupId: 'group', x: 150, y: 160 }),
  ])
  const result = arrangeDomainLayout(input)
  assert.equal(result.board, input); assert.equal(result.moved, 0); assert.equal(result.fixed, 3)
  assert.equal(result.fixedOverlaps, 1)
  assert.deepEqual(arrangeDomainLayout(board([])), { board: board([]), moved: 0, fixed: 0, fixedOverlaps: 0 })
})

test('long measured titles use actual dimensions; invalid measurements fall back safely', () => {
  const input = board([node('a', { text: '长标题'.repeat(100) }), node('b'), node('c')], [edge('a', 'c'), edge('b', 'c')])
  const sizes = { a: { width: 520, height: 1000 }, b: { width: 280, height: 390 }, c: { width: 280, height: 450 } }
  const result = arrangeDomainLayout(input, sizes)
  assertNoMovableOverlap(result.board, sizes)
  assert.deepEqual(semantic(result.board), semantic(input))
  assert.deepEqual(arrangeDomainLayout(input, { a: { width: NaN, height: -1 } }), arrangeDomainLayout(input))
})

test('crossing reduction orders opposite targets consistently with their sources', () => {
  const input = board(['a', 'b', 'c', 'x', 'y', 'z'].map(id => node(id)), [
    edge('a', 'z'), edge('b', 'y'), edge('c', 'x'), edge('a', 'y'), edge('b', 'x'),
  ])
  const result = arrangeDomainLayout(input)
  const byId = Object.fromEntries(result.board.nodes.map(n => [n.id, n]))
  assert((byId.a.y - byId.c.y) * (byId.z.y - byId.x.y) >= 0)
  assertNoMovableOverlap(result.board)
})

test('bounded synthetic graph permutations retain idempotence, locks and every relation', () => {
  let seed = 17
  const random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 2 ** 32 }
  for (let fixture = 0; fixture < 30; fixture++) {
    const nodes = Array.from({ length: 6 + fixture }, (_, i) => node(`n-${i}`, {
      x: Math.round(random() * 1000), y: Math.round(random() * 800), locked: i % 11 === 0,
    }))
    const edges = Array.from({ length: nodes.length * 3 }, (_, i) => edge(
      nodes[Math.floor(random() * nodes.length)].id, nodes[Math.floor(random() * nodes.length)].id, { id: `e-${i}`, locked: i % 3 === 0 }))
    const input = board(nodes, edges)
    const result = arrangeDomainLayout(input)
    assertNoMovableOverlap(result.board)
    assert.deepEqual(semantic(result.board), semantic(input))
    assert.equal(arrangeDomainLayout(result.board).moved, 0)
    assert.deepEqual(positions(arrangeDomainLayout({ ...input, nodes: [...nodes].reverse(), edges: [...edges].reverse() }).board), positions(result.board))
  }
})

test('coordinate overflow fails atomically instead of emitting an unsavable canvas', () => {
  const input = freeze(board([node('a'), node('fixed', { locked: true, x: 80, y: 80, height: 1_000_000 })]))
  const before = JSON.stringify(input)
  assert.throws(() => arrangeDomainLayout(input), /坐标范围/)
  assert.equal(JSON.stringify(input), before)
})
