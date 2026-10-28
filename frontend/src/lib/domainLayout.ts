import type { CanvasNode, DomainBoard } from '../api/domains'

export type DomainLayoutSizes = Record<string, { width: number; height: number }>
export interface DomainLayoutResult {
  board: DomainBoard
  moved: number
  fixed: number
  fixedOverlaps: number
}

type Rect = { x: number; y: number; width: number; height: number }
type Component = { key: string; nodes: Map<string, Rect>; width: number; height: number }
const COLUMN_GAP = 170
const ROW_GAP = 72
const MAX_COLUMN_EXTRA = 80
const MAX_ROW_EXTRA = 48
const COMPONENT_GAP = 140
const OBSTACLE_GAP = 48
const ORIGIN = 80
const compare = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0
const overlaps = (a: Rect, b: Rect, gap = 0) =>
  a.x < b.x + b.width + gap && a.x + a.width + gap > b.x &&
  a.y < b.y + b.height + gap && a.y + a.height + gap > b.y

function nodeRect(node: CanvasNode, sizes: DomainLayoutSizes): Rect {
  const size = sizes[node.id]
  const dimension = (measured: number | undefined, fallback: number) =>
    measured !== undefined && Number.isFinite(measured) && measured > 0 ? measured : fallback
  return { x: node.x, y: node.y,
    width: dimension(size?.width, node.width), height: dimension(size?.height, node.height) }
}

/** Iterative Kosaraju: cycles retain every edge and share one visual layer. */
function stronglyConnected(ids: string[], outgoing: Map<string, string[]>, incoming: Map<string, string[]>) {
  const seen = new Set<string>()
  const finish: string[] = []
  for (const start of ids) {
    if (seen.has(start)) continue
    seen.add(start)
    const stack: { id: string; index: number }[] = [{ id: start, index: 0 }]
    while (stack.length) {
      const top = stack[stack.length - 1]
      const next = outgoing.get(top.id)![top.index++]
      if (next !== undefined) {
        if (!seen.has(next)) { seen.add(next); stack.push({ id: next, index: 0 }) }
      } else { finish.push(top.id); stack.pop() }
    }
  }
  seen.clear()
  const result: string[][] = []
  for (const start of finish.reverse()) {
    if (seen.has(start)) continue
    const members: string[] = [], stack = [start]
    seen.add(start)
    while (stack.length) {
      const id = stack.pop()!
      members.push(id)
      for (const next of incoming.get(id)!) {
        if (!seen.has(next)) { seen.add(next); stack.push(next) }
      }
    }
    result.push(members.sort(compare))
  }
  return result.sort((a, b) => compare(a[0], b[0]))
}

function makeComponent(ids: string[], outgoing: Map<string, string[]>, incoming: Map<string, string[]>,
  rects: Map<string, Rect>, edgeCounts: Map<string, Map<string, number>>, degree: Map<string, number>): Component {
  const sccs = stronglyConnected(ids, outgoing, incoming)
  const membership = new Map(sccs.flatMap((members, index) => members.map(id => [id, index] as const)))
  const successors = sccs.map(() => new Set<number>())
  const indegrees = sccs.map(() => 0), ranks = sccs.map(() => 0)
  for (const id of ids) for (const next of outgoing.get(id)!) {
    const from = membership.get(id)!, to = membership.get(next)!
    if (from !== to && !successors[from].has(to)) { successors[from].add(to); indegrees[to]++ }
  }
  const ready = indegrees.flatMap((degree, index) => degree === 0 ? [index] : [])
  for (let cursor = 0; cursor < ready.length; cursor++) {
    const from = ready[cursor]
    for (const to of [...successors[from]].sort((a, b) => a - b)) {
      ranks[to] = Math.max(ranks[to], ranks[from] + 1)
      if (--indegrees[to] === 0) ready.push(to)
    }
  }
  const rank = new Map(ids.map(id => [id, ranks[membership.get(id)!]]))
  const layers: string[][] = Array.from({ length: Math.max(...ranks) + 1 }, () => [])
  for (const id of ids) layers[rank.get(id)!].push(id)
  const positions = new Map<string, number>()
  const indexLayer = (layer: string[]) => layer.forEach((id, index) => positions.set(id, (index + 0.5) / layer.length))
  layers.forEach(indexLayer)
  // Bounded barycenter sweeps reduce crossings without depending on old positions.
  for (let sweep = 0; sweep < 4; sweep++) {
    for (const direction of [1, -1]) {
      for (let step = 0; step < layers.length; step++) {
        const level = direction === 1 ? step : layers.length - 1 - step
        const layer = layers[level]
        const scores = new Map(layer.map(id => {
          const neighbours = (direction === 1 ? incoming : outgoing).get(id)!
            .filter(other => direction === 1 ? rank.get(other)! < level : rank.get(other)! > level)
          return [id, neighbours.length
            ? neighbours.reduce((sum, other) => sum + positions.get(other)!, 0) / neighbours.length
            : positions.get(id)!]
        }))
        layer.sort((a, b) => scores.get(a)! - scores.get(b)! || compare(a, b))
        indexLayer(layer)
      }
    }
  }
  // Long edges need room at every crossed boundary. Count actual relations
  // (including parallel ones), while SCC/rank construction stays deduplicated.
  const boundaryDelta = layers.map(() => 0)
  for (const id of ids) for (const [target, count] of edgeCounts.get(id)!) {
    const first = Math.min(rank.get(id)!, rank.get(target)!), last = Math.max(rank.get(id)!, rank.get(target)!)
    if (first !== last) { boundaryDelta[first] += count; boundaryDelta[last] -= count }
  }
  let traffic = 0
  const columnGaps = layers.slice(0, -1).map((layer, level) => {
    traffic += boundaryDelta[level]
    const perRow = traffic / Math.max(layer.length, layers[level + 1].length)
    return COLUMN_GAP + Math.min(MAX_COLUMN_EXTRA, Math.max(0, Math.ceil((perRow - 1) * 14)))
  })
  // Dense ports get a few extra routing lanes, not an unbounded graph-wide
  // expansion. Sparse chains retain their original footprint; labels are data.
  const rowGaps = layers.map(layer => layer.slice(0, -1).map((id, index) =>
    ROW_GAP + Math.min(MAX_ROW_EXTRA, Math.max(0, Math.max(degree.get(id)!, degree.get(layer[index + 1])!) - 2) * 6)))
  const heights = layers.map((layer, level) => layer.reduce((sum, id) => sum + rects.get(id)!.height, 0)
    + rowGaps[level].reduce((sum, gap) => sum + gap, 0))
  const height = Math.max(...heights)
  const nodes = new Map<string, Rect>()
  let x = 0
  for (let level = 0; level < layers.length; level++) {
    const layer = layers[level], width = Math.max(...layer.map(id => rects.get(id)!.width))
    let y = (height - heights[level]) / 2
    for (const [index, id] of layer.entries()) {
      const rect = rects.get(id)!
      nodes.set(id, { x, y, width: rect.width, height: rect.height })
      y += rect.height + (rowGaps[level][index] ?? 0)
    }
    x += width + (columnGaps[level] ?? 0)
  }
  return { key: ids[0], nodes, width: x, height }
}

/**
 * Arrange only ungrouped, unlocked paper cards. Groups and their children,
 * independent notes and locked cards are stationary obstacles. Node/edge order,
 * semantic data, sizes and viewport remain unchanged; no remote work is needed.
 */
export function arrangeDomainLayout(board: DomainBoard, sizes: DomainLayoutSizes = {}): DomainLayoutResult {
  const movable = board.nodes.filter(node => node.kind === 'paper' && !node.locked && !node.groupId)
  const movableIds = new Set(movable.map(node => node.id))
  const fixed = board.nodes.filter(node => !movableIds.has(node.id))
  const rects = new Map(board.nodes.map(node => [node.id, nodeRect(node, sizes)]))
  let fixedOverlaps = 0
  for (let i = 0; i < fixed.length; i++) for (let j = i + 1; j < fixed.length; j++) {
    const a = fixed[i], b = fixed[j]
    if ((a.kind === 'group' && b.groupId === a.id) || (b.kind === 'group' && a.groupId === b.id)) continue
    if (overlaps(rects.get(a.id)!, rects.get(b.id)!)) fixedOverlaps++
  }
  const result = (next: DomainBoard, moved: number): DomainLayoutResult => ({ board: next, moved, fixed: fixed.length, fixedOverlaps })
  if (!movable.length) return result(board, 0)
  const ids = [...movableIds].sort(compare)
  const outgoingSets = new Map(ids.map(id => [id, new Set<string>()]))
  const incomingSets = new Map(ids.map(id => [id, new Set<string>()]))
  const edgeCounts = new Map(ids.map(id => [id, new Map<string, number>()]))
  const degree = new Map(ids.map(id => [id, 0]))
  for (const edge of board.edges) {
    // A relation to a fixed paper still consumes a port on its movable peer.
    if (movableIds.has(edge.source)) degree.set(edge.source, degree.get(edge.source)! + 1)
    if (movableIds.has(edge.target)) degree.set(edge.target, degree.get(edge.target)! + 1)
    if (movableIds.has(edge.source) && movableIds.has(edge.target)) {
      outgoingSets.get(edge.source)!.add(edge.target)
      incomingSets.get(edge.target)!.add(edge.source)
      const counts = edgeCounts.get(edge.source)!
      counts.set(edge.target, (counts.get(edge.target) ?? 0) + 1)
    }
  }
  const outgoing = new Map(ids.map(id => [id, [...outgoingSets.get(id)!].sort(compare)]))
  const incoming = new Map(ids.map(id => [id, [...incomingSets.get(id)!].sort(compare)]))
  const seen = new Set<string>(), components: Component[] = []
  for (const start of ids) {
    if (seen.has(start)) continue
    const connected: string[] = [], pending = [start]
    seen.add(start)
    for (let cursor = 0; cursor < pending.length; cursor++) {
      const id = pending[cursor]
      connected.push(id)
      for (const next of [...outgoing.get(id)!, ...incoming.get(id)!]) {
        if (!seen.has(next)) { seen.add(next); pending.push(next) }
      }
    }
    components.push(makeComponent(connected.sort(compare), outgoing, incoming, rects, edgeCounts, degree))
  }
  components.sort((a, b) => b.height * b.width - a.height * a.width || compare(a.key, b.key))
  const totalArea = components.reduce((sum, component) => sum + (component.width + COMPONENT_GAP) * (component.height + COMPONENT_GAP), 0)
  const shelfWidth = Math.max(...components.map(component => component.width), Math.min(2200, Math.max(1200, Math.sqrt(totalArea) * 1.4)))
  const obstacles = fixed.map(node => rects.get(node.id)!).sort((a, b) => a.y - b.y || a.x - b.x)
  const arranged = new Map<string, Rect>()
  let x = ORIGIN, rowY = ORIGIN, rowHeight = 0
  for (const component of components) {
    if (x > ORIGIN && x + component.width > ORIGIN + shelfWidth) {
      x = ORIGIN; rowY += rowHeight + COMPONENT_GAP; rowHeight = 0
    }
    const box = { x, y: rowY, width: component.width, height: component.height }
    // Move the whole component around obstacles, preserving its layer ordering.
    for (let attempt = 0; attempt <= obstacles.length; attempt++) {
      const hit = obstacles.find(obstacle => overlaps(box, obstacle, OBSTACLE_GAP))
      if (!hit) break
      box.y = hit.y + hit.height + OBSTACLE_GAP
    }
    for (const [id, rect] of component.nodes) {
      const placed = { ...rect, x: box.x + rect.x, y: box.y + rect.y }
      if (!Number.isFinite(placed.x) || !Number.isFinite(placed.y) || Math.abs(placed.x) > 1_000_000 || Math.abs(placed.y) > 1_000_000) {
        throw new Error('布局超出画布坐标范围，原画布保持不变。')
      }
      arranged.set(id, placed)
    }
    obstacles.push(box)
    rowHeight = Math.max(rowHeight, box.y - rowY + box.height)
    x += component.width + COMPONENT_GAP
  }
  let moved = 0
  const nodes = board.nodes.map(node => {
    const position = arranged.get(node.id)
    if (!position || (node.x === position.x && node.y === position.y)) return node
    moved++
    return { ...node, x: position.x, y: position.y }
  })
  return result(moved ? { ...board, nodes } : board, moved)
}
