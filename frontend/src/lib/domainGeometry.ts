import type { CanvasEdge, DomainBoard } from '../api/domains'

export interface DomainRoutePoint { x: number; y: number }
export interface DomainEdgeGeometry {
  sx: number
  sy: number
  tx: number
  ty: number
  d: string
  vertical: boolean
  labelX: number
  labelY: number
  /** Orthogonal centreline, retained for geometric verification. */
  points: DomainRoutePoint[]
  /** Existing overlapping cards can make an unobstructed endpoint impossible. */
  obstructed: boolean
  /** Very large drawings retain every edge with cheaper, unverified paths. */
  simplified: boolean
}
type Rect = { id: string; x: number; y: number; width: number; height: number }
type Side = 'left' | 'right' | 'top' | 'bottom'
type Endpoint = { edge: CanvasEdge; end: 'source' | 'target'; side: Side; other: Rect }
type Sizes = Record<string, { width: number; height: number }>
type SearchBudget = { remaining: number }
type Segment = { axis: number; from: number; to: number; horizontal: boolean }
const PAD = 12
const LANE_GAP = 20
// Ordinary research canvases get obstacle routing. Larger drawings must remain
// responsive during a drag instead of doing an unbounded N × E obstacle scan.
const MAX_ROUTED_NODES = 200
const MAX_ROUTED_EDGES = 800
const compare = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0
const equal = (a: DomainRoutePoint, b: DomainRoutePoint) => a.x === b.x && a.y === b.y

function crosses(a: DomainRoutePoint, b: DomainRoutePoint, rect: Rect, padding = PAD) {
  const left = rect.x - padding, right = rect.x + rect.width + padding
  const top = rect.y - padding, bottom = rect.y + rect.height + padding
  if (a.y === b.y) return a.y > top && a.y < bottom && Math.max(a.x, b.x) > left && Math.min(a.x, b.x) < right
  if (a.x === b.x) return a.x > left && a.x < right && Math.max(a.y, b.y) > top && Math.min(a.y, b.y) < bottom
  return true
}

function simplify(points: DomainRoutePoint[]) {
  const result: DomainRoutePoint[] = []
  for (const point of points) {
    if (result.length && equal(result[result.length - 1], point)) continue
    const a = result.at(-2), b = result.at(-1)
    if (a && b && ((a.x === b.x && b.x === point.x) || (a.y === b.y && b.y === point.y))) result.pop()
    result.push(point)
  }
  return result
}

function length(points: DomainRoutePoint[]) {
  return points.slice(1).reduce((sum, point, index) => sum + Math.abs(point.x - points[index].x) + Math.abs(point.y - points[index].y), 0)
}

function clear(points: DomainRoutePoint[], obstacles: Rect[]) {
  return points.slice(1).every((point, index) => obstacles.every(rect => !crosses(points[index], point, rect)))
}

function segments(points: DomainRoutePoint[], minimum = 32): Segment[] {
  return points.slice(1).flatMap((point, index) => {
    const previous = points[index], horizontal = point.y === previous.y
    const from = horizontal ? Math.min(point.x, previous.x) : Math.min(point.y, previous.y)
    const to = horizontal ? Math.max(point.x, previous.x) : Math.max(point.y, previous.y)
    // Callers retain short segments when checking crossings; only parallel
    // crowding excludes the unavoidable short fan-out at a card's ports.
    return to - from < minimum ? [] : [{ horizontal, from, to, axis: horizontal ? point.y : point.x }]
  })
}

/** Buckets index parallel lanes; the shared work counter also bounds scoring. */
class OccupiedLanes {
  horizontal = new Map<number, Segment[]>()
  vertical = new Map<number, Segment[]>()
  remaining = 1_000_000

  add(points: DomainRoutePoint[]) {
    for (const segment of segments(points, 0)) {
      const lanes = segment.horizontal ? this.horizontal : this.vertical
      const key = Math.floor(segment.axis / LANE_GAP)
      const bucket = lanes.get(key) ?? []
      bucket.push(segment); lanes.set(key, bucket)
    }
  }

  neighbors(horizontal: boolean, from: number, to: number, center: number) {
    if (!this.remaining) return []
    const lanes = horizontal ? this.horizontal : this.vertical
    const values = new Set<number>()
    for (const bucket of lanes.values()) {
      if (--this.remaining <= 0) return []
      for (const segment of bucket) {
        if (--this.remaining <= 0) return []
        if (segment.from < to && segment.to > from) {
          values.add(segment.axis - LANE_GAP); values.add(segment.axis + LANE_GAP)
        }
      }
    }
    // Enough alternatives to distribute a dense fan, without growing the
    // candidate set with the number of existing relationships.
    return [...values].sort((a, b) => Math.abs(a - center) - Math.abs(b - center) || a - b).slice(0, 32)
  }

  score(points: DomainRoutePoint[], minimum = 32) {
    let result = 0
    for (const segment of segments(points, 0)) {
      const parallel = segment.horizontal ? this.horizontal : this.vertical
      const perpendicular = segment.horizontal ? this.vertical : this.horizontal
      const key = Math.floor(segment.axis / LANE_GAP)
      for (let bucket = key - 1; bucket <= key + 1; bucket++) {
        for (const previous of parallel.get(bucket) ?? []) {
          if (--this.remaining <= 0) { this.remaining = 0; return null }
          const distance = Math.abs(segment.axis - previous.axis)
          const overlap = Math.min(segment.to, previous.to) - Math.max(segment.from, previous.from)
          if (segment.to - segment.from < minimum || previous.to - previous.from < 32) continue
          if (distance >= LANE_GAP || overlap <= 0) continue
          result += overlap * (distance < 0.5 ? 1.8 : 0.9 * (1 - distance / LANE_GAP))
        }
      }
      for (const [bucket, values] of perpendicular) {
        if (--this.remaining <= 0) { this.remaining = 0; return null }
        if ((bucket + 1) * LANE_GAP <= segment.from || bucket * LANE_GAP >= segment.to) continue
        for (const previous of values) {
          if (--this.remaining <= 0) { this.remaining = 0; return null }
          // Ignore touching bends and endpoints; penalize proper crossings.
          if (previous.axis > segment.from + 4 && previous.axis < segment.to - 4 &&
            segment.axis > previous.from + 4 && segment.axis < previous.to - 4) result += 40
        }
      }
    }
    return result
  }
}

/** A small binary heap keeps the rare obstacle maze fallback bounded. */
class Queue {
  values: { id: number; score: number }[] = []
  push(value: { id: number; score: number }) {
    let index = this.values.length
    this.values.push(value)
    while (index > 0) {
      const parent = (index - 1) >> 1
      if (this.values[parent].score <= value.score) break
      this.values[index] = this.values[parent]
      index = parent
    }
    this.values[index] = value
  }
  pop() {
    const first = this.values[0], last = this.values.pop()
    if (!this.values.length || !last) return first
    let index = 0
    while (index * 2 + 1 < this.values.length) {
      let child = index * 2 + 1
      if (child + 1 < this.values.length && this.values[child + 1].score < this.values[child].score) child++
      if (this.values[child].score >= last.score) break
      this.values[index] = this.values[child]
      index = child
    }
    this.values[index] = last
    return first
  }
}

function mazeRoute(start: DomainRoutePoint, end: DomainRoutePoint, obstacles: Rect[], budget: SearchBudget,
  congestion?: { occupied: OccupiedLanes; maxLength: number; maxCost: number }) {
  const xs = [...new Set([start.x, end.x, ...obstacles.flatMap(rect => [rect.x - PAD, rect.x + rect.width + PAD])])].sort((a, b) => a - b)
  const ys = [...new Set([start.y, end.y, ...obstacles.flatMap(rect => [rect.y - PAD, rect.y + rect.height + PAD])])].sort((a, b) => a - b)
  const width = xs.length
  const first = (ys.indexOf(start.y) * width + xs.indexOf(start.x)) * 2
  const target = ys.indexOf(end.y) * width + xs.indexOf(end.x)
  const point = (index: number) => ({ x: xs[index % width], y: ys[Math.floor(index / width)] })
  const scores = new Map([[first, 0]]), previous = new Map<number, number>()
  const distances = new Map([[first, 0]])
  const queue = new Queue()
  const done = new Set<number>()
  queue.push({ id: first, score: 0 })
  // The common case uses a short candidate route. Do not freeze the editor on
  // a pathological overlapping drawing; retain its edge with an explicit flag.
  const allowance = congestion ? Math.min(600, budget.remaining) : budget.remaining
  while (queue.values.length && budget.remaining > 0 && done.size < allowance) {
    const { id } = queue.pop()!
    if (done.has(id)) continue
    const location = Math.floor(id / 2)
    if (location === target) {
      const result = [point(location)]
      let cursor = id
      while (previous.has(cursor)) { cursor = previous.get(cursor)!; result.push(point(Math.floor(cursor / 2))) }
      return simplify(result.reverse())
    }
    done.add(id)
    budget.remaining--
    const x = location % width, y = Math.floor(location / width), a = point(location)
    const neighbors = [x > 0 ? location - 1 : -1, x + 1 < width ? location + 1 : -1,
      y > 0 ? location - width : -1, y + 1 < ys.length ? location + width : -1]
    for (const [direction, adjacent] of neighbors.entries()) {
      if (adjacent < 0) continue
      const next = adjacent * 2 + (direction >= 2 ? 1 : 0)
      if (done.has(next)) continue
      const b = point(adjacent), distance = Math.abs(a.x - b.x) + Math.abs(a.y - b.y)
      const travelled = distances.get(id)! + distance
      const remaining = Math.abs(b.x - end.x) + Math.abs(b.y - end.y)
      if (congestion && travelled + remaining > congestion.maxLength) continue
      if (obstacles.some(rect => crosses(a, b, rect))) continue
      const penalty = congestion?.occupied.score([a, b], 0) ?? 0
      if (congestion && !congestion.occupied.remaining) return null
      const bend = id !== first && id % 2 !== next % 2 ? 24 : 0
      const score = scores.get(id)! + distance + penalty + bend
      if (congestion && score + remaining >= congestion.maxCost) continue
      if (score >= (scores.get(next) ?? Infinity)) continue
      scores.set(next, score); distances.set(next, travelled); previous.set(next, id)
      queue.push({ id: next, score: score + remaining })
    }
  }
  return null
}

function route(start: DomainRoutePoint, end: DomainRoutePoint, obstacles: Rect[], budget: SearchBudget,
  occupied: OccupiedLanes, fallbackLane: number, channelBudget: SearchBudget) {
  const candidates: { points: DomainRoutePoint[]; kind: 'x' | 'y' | 'elbow'; length: number; cost: number }[] = []
  const seen = new Set<string>()
  const add = (points: DomainRoutePoint[], kind: 'x' | 'y' | 'elbow' = 'elbow') => {
    const simplified = simplify(points), key = simplified.map(point => `${point.x},${point.y}`).join(';')
    if (seen.has(key)) return
    seen.add(key)
    const distance = length(simplified)
    candidates.push({ points: simplified, kind, length: distance, cost: distance + simplified.length * 24 })
  }
  add([start, { x: end.x, y: start.y }, end])
  add([start, { x: start.x, y: end.y }, end])
  add([start, { x: (start.x + end.x) / 2, y: start.y }, { x: (start.x + end.x) / 2, y: end.y }, end], 'x')
  add([start, { x: start.x, y: (start.y + end.y) / 2 }, { x: end.x, y: (start.y + end.y) / 2 }, end], 'y')
  // Once the whole-drawing score budget is spent, retain the former bounded
  // lane spread instead of collapsing every remaining edge onto one boundary.
  if (!occupied.remaining) {
    const direct = candidates.find(candidate => clear(candidate.points, obstacles))
    if (direct) return direct.points
  }
  const margin = occupied.remaining ? 24 : 24 + fallbackLane * 7
  const xs = [(start.x + end.x) / 2, ...obstacles.flatMap(rect => [rect.x - margin, rect.x + rect.width + margin]),
    ...occupied.neighbors(false, Math.min(start.y, end.y), Math.max(start.y, end.y), (start.x + end.x) / 2)]
  const ys = [(start.y + end.y) / 2, ...obstacles.flatMap(rect => [rect.y - margin, rect.y + rect.height + margin]),
    ...occupied.neighbors(true, Math.min(start.x, end.x), Math.max(start.x, end.x), (start.y + end.y) / 2)]
  for (const x of [...new Set(xs)].sort((a, b) => a - b)) add([start, { x, y: start.y }, { x, y: end.y }, end], 'x')
  for (const y of [...new Set(ys)].sort((a, b) => a - b)) add([start, { x: start.x, y }, { x: end.x, y }, end], 'y')
  candidates.sort((a, b) => a.cost - b.cost)
  const first = candidates.find(candidate => clear(candidate.points, obstacles))
  if (!first) return budget.remaining > 0 ? mazeRoute(start, end, obstacles, budget) : null
  if (!occupied.remaining) return first.points
  let best = first.points, bestCost = first.cost + (occupied.score(first.points) ?? 0)
  const direct = Math.abs(start.x - end.x) + Math.abs(start.y - end.y)
  // Congestion may justify a nearby lane, never an ever-growing outer ring.
  const maxLength = first.length + Math.max(160, Math.min(360, direct * 0.35))
  const checked = { x: 0, y: 0, elbow: 0 }
  for (const candidate of candidates) {
    if (candidate === first || candidate.length > maxLength || checked[candidate.kind] >= 12) continue
    if (!clear(candidate.points, obstacles)) continue
    checked[candidate.kind]++
    const congestion = occupied.score(candidate.points)
    if (congestion === null) break
    if (candidate.cost + congestion < bestCost) { bestCost = candidate.cost + congestion; best = candidate.points }
  }
  // A single horizontal lane can be only a few pixels wide across staggered
  // columns. A bounded search may change lanes between columns instead of
  // stacking long edges there or growing an outer ring around the drawing.
  if (occupied.remaining && channelBudget.remaining && bestCost - length(best) - best.length * 24 > 120) {
    const stepped = mazeRoute(start, end, obstacles, channelBudget, { occupied, maxLength, maxCost: bestCost })
    if (stepped) {
      const penalty = occupied.score(stepped)
      if (penalty !== null && length(stepped) + stepped.length * 24 + penalty < bestCost) best = stepped
    }
  }
  return best
}

function sides(source: Rect, target: Rect): [Side, Side] {
  if (source.id === target.id) return ['right', 'bottom']
  if (target.x >= source.x + source.width + 24) return ['right', 'left']
  if (source.x >= target.x + target.width + 24) return ['left', 'right']
  if (target.y >= source.y + source.height + 24) return ['bottom', 'top']
  if (source.y >= target.y + target.height + 24) return ['top', 'bottom']
  return target.x + target.width / 2 >= source.x + source.width / 2 ? ['right', 'left'] : ['left', 'right']
}

function port(rect: Rect, side: Side, fraction: number) {
  if (side === 'left' || side === 'right') return { x: rect.x + (side === 'right' ? rect.width : 0), y: rect.y + rect.height * fraction }
  return { x: rect.x + rect.width * fraction, y: rect.y + (side === 'bottom' ? rect.height : 0) }
}

function stub(point: DomainRoutePoint, side: Side, distance: number, obstacles: Rect[], ownId: string) {
  for (const rect of obstacles) {
    if (rect.id === ownId) continue
    if (side === 'left' || side === 'right') {
      if (point.y <= rect.y - PAD || point.y >= rect.y + rect.height + PAD) continue
      const gap = side === 'right' ? rect.x - PAD - point.x : point.x - rect.x - rect.width - PAD
      if (gap >= 0) distance = Math.min(distance, Math.max(PAD, gap))
    } else {
      if (point.x <= rect.x - PAD || point.x >= rect.x + rect.width + PAD) continue
      const gap = side === 'bottom' ? rect.y - PAD - point.y : point.y - rect.y - rect.height - PAD
      if (gap >= 0) distance = Math.min(distance, Math.max(PAD, gap))
    }
  }
  return { x: point.x + (side === 'right' ? distance : side === 'left' ? -distance : 0),
    y: point.y + (side === 'bottom' ? distance : side === 'top' ? -distance : 0) }
}

function labelPoint(points: DomainRoutePoint[], labelWidth: number, obstacles: Rect[], occupied: Rect[]) {
  const total = length(points)
  let travelled = 0
  const options: { point: DomainRoutePoint; score: number }[] = []
  points.slice(1).forEach((b, index) => {
    const a = points[index], segment = Math.abs(a.x - b.x) + Math.abs(a.y - b.y)
    for (const fraction of [0.5, 0.3, 0.7]) {
      const point = { x: a.x + (b.x - a.x) * fraction, y: a.y + (b.y - a.y) * fraction }
      const box = { id: '', x: point.x - labelWidth / 2, y: point.y - 34, width: labelWidth, height: 48 }
      const overlaps = (rect: Rect) => box.x < rect.x + rect.width && box.x + box.width > rect.x && box.y < rect.y + rect.height && box.y + box.height > rect.y
      const collides = obstacles.some(overlaps) || occupied.some(overlaps)
      options.push({ point, score: (collides ? 100000 : 0) + Math.abs(travelled + segment * fraction - total / 2) + (a.x === b.x ? 60 : 0) })
    }
    travelled += segment
  })
  options.sort((a, b) => a.score - b.score)
  return options[0]?.point ?? points[0]
}

/** Drawing only: never changes edge identity, relation, evidence, or lock state. */
export function computeDomainEdgeGeometries(board: Pick<DomainBoard, 'nodes' | 'edges'>, measured: Sizes = {}): Map<string, DomainEdgeGeometry> {
  const rects = new Map(board.nodes.map(node => [node.id, { id: node.id, x: node.x, y: node.y,
    width: measured[node.id]?.width || node.width, height: measured[node.id]?.height || node.height }]))
  const obstacles = board.nodes.filter(node => node.kind !== 'group').map(node => rects.get(node.id)!)
    .sort((a, b) => compare(a.id, b.id))
  const edges = [...board.edges].sort((a, b) => compare(a.id, b.id))
  const largeDrawing = obstacles.length > MAX_ROUTED_NODES || edges.length > MAX_ROUTED_EDGES
  const searchBudget: SearchBudget = { remaining: 8000 }
  const channelBudget: SearchBudget = { remaining: 8000 }
  const endpoints = new Map<string, Endpoint[]>()
  const addEndpoint = (nodeId: string, endpoint: Endpoint) => {
    const key = `${nodeId}:${endpoint.side}`
    const values = endpoints.get(key) ?? []
    values.push(endpoint); endpoints.set(key, values)
  }
  for (const edge of edges) {
    const source = rects.get(edge.source), target = rects.get(edge.target)
    if (!source || !target) continue
    const [from, to] = sides(source, target)
    addEndpoint(source.id, { edge, end: 'source', side: from, other: target })
    addEndpoint(target.id, { edge, end: 'target', side: to, other: source })
  }
  const ports = new Map<string, { point: DomainRoutePoint; side: Side }>()
  for (const entries of endpoints.values()) {
    entries.sort((a, b) => {
      const vertical = a.side === 'left' || a.side === 'right'
      const delta = vertical ? a.other.y - b.other.y : a.other.x - b.other.x
      return delta || compare(a.edge.id, b.edge.id) || compare(a.end, b.end)
    })
    entries.forEach((entry, index) => {
      const node = rects.get(entry.edge[entry.end])!
      const fraction = entries.length === 1 ? 0.5 : 0.18 + 0.64 * index / (entries.length - 1)
      ports.set(`${entry.edge.id}:${entry.end}`, { point: port(node, entry.side, fraction), side: entry.side })
    })
  }
  const result = new Map<string, DomainEdgeGeometry>(), labels: Rect[] = []
  const occupied = new OccupiedLanes()
  for (const [index, edge] of edges.entries()) {
    const from = ports.get(`${edge.id}:source`), to = ports.get(`${edge.id}:target`)
    if (!from || !to) continue
    const distance = 24 + (index % 5) * 5
    const start = stub(from.point, from.side, distance, largeDrawing ? [] : obstacles, edge.source)
    const end = stub(to.point, to.side, distance, largeDrawing ? [] : obstacles, edge.target)
    const fastRoute = edge.source === edge.target
      ? [start, { x: start.x, y: end.y }, end]
      : [start, { x: start.x, y: (start.y + end.y) / 2 }, { x: end.x, y: (start.y + end.y) / 2 }, end]
    const middle = largeDrawing ? fastRoute : route(start, end, obstacles, searchBudget, occupied, index % 7, channelBudget)
    const simplified = largeDrawing || (!middle && searchBudget.remaining === 0)
    const points = simplify([from.point, ...(middle ?? fastRoute), to.point])
    if (!largeDrawing && middle) occupied.add(points)
    const obstructed = !simplified && (!middle || points.slice(1).some((point, segment) => obstacles.some(rect => {
      if (segment === 0 && rect.id === edge.source) return false
      if (segment === points.length - 2 && rect.id === edge.target) return false
      return crosses(points[segment], point, rect, 0)
    })))
    const labelWidth = Math.max(64, Math.min(260, edge.label.length * 9 + 24))
    const label = labelPoint(points, labelWidth, simplified ? [] : obstacles, simplified ? [] : labels)
    if (!simplified) labels.push({ id: edge.id, x: label.x - labelWidth / 2, y: label.y - 34, width: labelWidth, height: 48 })
    result.set(edge.id, {
      sx: from.point.x, sy: from.point.y, tx: to.point.x, ty: to.point.y,
      d: points.map((point, pointIndex) => `${pointIndex ? 'L' : 'M'} ${point.x} ${point.y}`).join(' '),
      vertical: from.side === 'top' || from.side === 'bottom', labelX: label.x, labelY: label.y,
      points, obstructed, simplified,
    })
  }
  return result
}
