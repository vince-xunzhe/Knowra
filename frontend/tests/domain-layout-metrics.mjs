/** Independent visual measurements from rendered DOM/SVG, never router internals. */
import assert from 'node:assert/strict'

const EPS = 0.5
const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y)
const round = number => Math.round(number * 100) / 100
const rectOverlap = (a, b) => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y
const rectGap = (a, b) => Math.hypot(Math.max(0, a.x - b.x - b.width, b.x - a.x - a.width),
  Math.max(0, a.y - b.y - b.height, b.y - a.y - a.height))
const bbox = rects => {
  const x = Math.min(...rects.map(r => r.x)), y = Math.min(...rects.map(r => r.y))
  const right = Math.max(...rects.map(r => r.x + r.width)), bottom = Math.max(...rects.map(r => r.y + r.height))
  return { x, y, width: right - x, height: bottom - y, area: (right - x) * (bottom - y) }
}
const distribution = values => {
  const sorted = [...values].sort((a, b) => a - b)
  return sorted.length ? { count: sorted.length, min: round(sorted[0]), p10: round(sorted[Math.floor((sorted.length - 1) * 0.1)]),
    median: round(sorted[Math.floor((sorted.length - 1) / 2)]) } : { count: 0, min: null, p10: null, median: null }
}
const pointAlong = (a, b, fraction) => ({ x: a.x + (b.x - a.x) * fraction, y: a.y + (b.y - a.y) * fraction })
function segments(points, trim = 0) {
  const total = points.slice(1).reduce((sum, point, i) => sum + distance(points[i], point), 0)
  let cursor = 0
  return points.slice(1).flatMap((point, i) => {
    const start = cursor, size = distance(points[i], point); cursor += size
    const from = Math.max(start, trim), to = Math.min(cursor, total - trim)
    return size > EPS && to - from > EPS ? [{ a: pointAlong(points[i], point, (from - start) / size),
      b: pointAlong(points[i], point, (to - start) / size) }] : []
  })
}
function axis(segment) {
  if (Math.abs(segment.a.y - segment.b.y) < EPS) return { axis: 'h', fixed: (segment.a.y + segment.b.y) / 2,
    lo: Math.min(segment.a.x, segment.b.x), hi: Math.max(segment.a.x, segment.b.x) }
  if (Math.abs(segment.a.x - segment.b.x) < EPS) return { axis: 'v', fixed: (segment.a.x + segment.b.x) / 2,
    lo: Math.min(segment.a.y, segment.b.y), hi: Math.max(segment.a.y, segment.b.y) }
  return null
}
function properIntersection(a, b) {
  const dx1 = a.b.x - a.a.x, dy1 = a.b.y - a.a.y, dx2 = b.b.x - b.a.x, dy2 = b.b.y - b.a.y
  const denominator = dx1 * dy2 - dy1 * dx2
  if (Math.abs(denominator) < 0.0001) return null
  const dx = b.a.x - a.a.x, dy = b.a.y - a.a.y
  const t = (dx * dy2 - dy * dx2) / denominator, u = (dx * dy1 - dy * dx1) / denominator
  if (t <= 0.0001 || t >= 0.9999 || u <= 0.0001 || u >= 0.9999) return null
  return pointAlong(a.a, a.b, t)
}
function pairMetrics(routes, trim) {
  const expanded = routes.map(route => ({ ...route, segments: segments(route.points, trim) }))
  let crossingCount = 0, sharedCrossingCount = 0, collinearLength = 0, near12Length = 0, near20Length = 0
  const crossings = [], sharedCrossings = [], collinearPairs = [], near12Pairs = [], near20Pairs = []
  for (let i = 0; i < expanded.length; i++) for (const b of expanded.slice(i + 1)) {
    const a = expanded[i], points = new Set()
    let collinear = 0, near12 = 0, near20 = 0
    for (const first of a.segments) for (const second of b.segments) {
      const point = properIntersection(first, second)
      if (point) points.add(`${round(point.x)},${round(point.y)}`)
      const x = axis(first), y = axis(second)
      if (!x || !y || x.axis !== y.axis) continue
      const overlap = Math.min(x.hi, y.hi) - Math.max(x.lo, y.lo), gap = Math.abs(x.fixed - y.fixed)
      if (overlap <= EPS) continue
      if (gap <= EPS) collinear += overlap
      else if (overlap >= 24 && gap < 20) {
        near20 += overlap
        if (gap < 12) near12 += overlap
      }
    }
    const sharedEndpoint = [a.source, a.target].some(id => id === b.source || id === b.target)
    if (points.size) {
      if (sharedEndpoint) { sharedCrossingCount += points.size; sharedCrossings.push({ edges: [a.id, b.id], count: points.size }) }
      else { crossingCount += points.size; crossings.push({ edges: [a.id, b.id], count: points.size }) }
    }
    if (collinear) { collinearLength += collinear; collinearPairs.push({ edges: [a.id, b.id], length: round(collinear), sharedEndpoint }) }
    if (near12) { near12Length += near12; near12Pairs.push({ edges: [a.id, b.id], length: round(near12), sharedEndpoint }) }
    if (near20) { near20Length += near20; near20Pairs.push({ edges: [a.id, b.id], length: round(near20), sharedEndpoint }) }
  }
  return { crossings: crossingCount, crossingEdgePairs: crossings.length, sharedEndpointCrossings: sharedCrossingCount,
    totalCrossings: crossingCount + sharedCrossingCount,
    collinearOverlapLength: round(collinearLength), collinearEdgePairs: collinearPairs.length,
    nearParallel12Length: round(near12Length), nearParallel12EdgePairs: near12Pairs.length,
    nearParallel20Length: round(near20Length), nearParallel20EdgePairs: near20Pairs.length,
    details: { crossings, sharedCrossings, collinearPairs, near12Pairs, near20Pairs } }
}
function segmentRectangleGap(segment, rect) {
  const s = axis(segment)
  if (!s) return null
  const lower = s.axis === 'h' ? rect.x : rect.y, upper = lower + (s.axis === 'h' ? rect.width : rect.height)
  const lowFixed = s.axis === 'h' ? rect.y : rect.x, highFixed = lowFixed + (s.axis === 'h' ? rect.height : rect.width)
  return Math.hypot(Math.max(0, lower - s.hi, s.lo - upper), Math.max(0, lowFixed - s.fixed, s.fixed - highFixed))
}
function segmentCutsRectangle(segment, rect) {
  const s = axis(segment)
  if (!s) return false
  const lower = s.axis === 'h' ? rect.x : rect.y, upper = lower + (s.axis === 'h' ? rect.width : rect.height)
  const lowFixed = s.axis === 'h' ? rect.y : rect.x, highFixed = lowFixed + (s.axis === 'h' ? rect.height : rect.width)
  return s.fixed > lowFixed + EPS && s.fixed < highFixed - EPS && s.hi > lower + EPS && s.lo < upper - EPS
}

export async function collectRenderedGraph(page, edges) {
  const graph = await page.evaluate(edges => {
    const canvas = document.querySelector('[data-testid="domain-canvas"]')
    const nodes = [...canvas.querySelectorAll('[data-node-id]')].map(element => ({ id: element.dataset.nodeId, kind: element.dataset.kind,
      x: parseFloat(element.style.left), y: parseFloat(element.style.top), width: element.offsetWidth, height: element.offsetHeight }))
    const edgeElements = [...canvas.querySelectorAll('[data-edge-id]')]
    const routes = edges.flatMap(edge => {
      const element = edgeElements.find(element => element.dataset.edgeId === edge.id)
      const path = element?.querySelector('.domain-edge-line')
      if (!path) return []
      let visible = true
      for (let ancestor = path; ancestor; ancestor = ancestor.parentElement) {
        const style = getComputedStyle(ancestor)
        if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || Number(style.opacity) <= 0) {
          visible = false; break
        }
      }
      const pathStyle = getComputedStyle(path)
      visible &&= Number(pathStyle.strokeOpacity) > 0 && pathStyle.stroke !== 'none' && parseFloat(pathStyle.strokeWidth) > 0
      const d = path.getAttribute('d'), length = path.getTotalLength(), bounds = path.getBBox()
      const tokens = d.match(/[a-zA-Z]|[-+]?(?:\d*\.)?\d+(?:e[-+]?\d+)?/g) || []
      const points = []; let exactPolyline = tokens.length > 0
      for (let i = 0; i < tokens.length; i += 3) {
        if (!['M', 'L'].includes(tokens[i]) || !Number.isFinite(Number(tokens[i + 1])) || !Number.isFinite(Number(tokens[i + 2]))) { exactPolyline = false; break }
        points.push({ x: Number(tokens[i + 1]), y: Number(tokens[i + 2]) })
      }
      if (!exactPolyline) {
        points.length = 0
        const count = Math.max(2, Math.ceil(length / 2))
        for (let i = 0; i <= count; i++) { const p = path.getPointAtLength(length * i / count); points.push({ x: p.x, y: p.y }) }
      }
      return [{ id: edge.id, source: edge.source, target: edge.target, d, points, exactPolyline, length,
        bounds: { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height },
        visible }]
    })
    return { nodes, routes, actualEdgeIds: edgeElements.map(e => e.dataset.edgeId), canvas: { width: canvas.clientWidth, height: canvas.clientHeight } }
  }, edges)
  // Missing, duplicate, hidden or invalid paths fail instead of disappearing from metrics.
  assert.deepEqual([...graph.actualEdgeIds].sort(), edges.map(edge => edge.id).sort(), 'Every fixture relation must render exactly once')
  assert.equal(graph.routes.length, edges.length, 'Every edge must have a measured SVG path')
  assert.deepEqual(graph.routes.filter(route => !route.visible || !Number.isFinite(route.length) || route.length <= 0)
    .map(route => route.id), [], 'Every edge must remain visible and measurable, including ancestor opacity')
  assert(graph.routes.every(route => route.exactPolyline), 'Metrics require exact rendered M/L paths; unsupported curves are reported, not silently undercounted')
  return graph
}

export function measureRenderedGraph(graph, board) {
  const cards = graph.nodes.filter(node => node.kind !== 'group')
  const movable = new Set(board.nodes.filter(node => node.kind === 'paper' && !node.groupId && !node.locked).map(node => node.id))
  let overlaps = 0
  const nodeGaps = [], movableGaps = [], rowGaps = [], columnGaps = [], hitPairs = [], clearances = []
  for (let i = 0; i < cards.length; i++) for (const b of cards.slice(i + 1)) {
    const a = cards[i], gap = rectGap(a, b)
    nodeGaps.push(gap)
    if (rectOverlap(a, b)) overlaps++
    if (movable.has(a.id) && movable.has(b.id)) {
      movableGaps.push(gap)
      if (a.x < b.x + b.width && b.x < a.x + a.width) rowGaps.push(Math.max(0, a.y - b.y - b.height, b.y - a.y - a.height))
      if (a.y < b.y + b.height && b.y < a.y + a.height) columnGaps.push(Math.max(0, a.x - b.x - b.width, b.x - a.x - a.width))
    }
  }
  for (const route of graph.routes) {
    const lines = segments(route.points)
    for (const node of cards.filter(node => node.id !== route.source && node.id !== route.target)) {
      if (lines.some(line => segmentCutsRectangle(line, node))) hitPairs.push([route.id, node.id])
      clearances.push(Math.min(...lines.map(line => segmentRectangleGap(line, node)).filter(value => value !== null)))
    }
  }
  const allBounds = bbox([...graph.nodes, ...graph.routes.map(route => route.bounds)])
  const lengths = graph.routes.map(route => route.length), bends = graph.routes.map(route => Math.max(0, route.points.length - 2))
  return { nodeCount: graph.nodes.length, edgeCount: graph.routes.length, missingEdges: [],
    nodeOverlaps: overlaps, edgeNodeHits: hitPairs.length, hitPairs,
    paths: { totalLength: round(lengths.reduce((a, b) => a + b, 0)), longestLength: round(Math.max(...lengths)),
      totalBends: bends.reduce((a, b) => a + b, 0), maxBends: Math.max(...bends) },
    fullPaths: pairMetrics(graph.routes, 0), corePaths: pairMetrics(graph.routes, 24),
    endpointStubPolicy: 'Core paths trim exactly 24 world pixels from each end; full-path metrics retain all endpoint stubs. Crossings between shared-endpoint edge pairs are reported separately.',
    proximityPolicy: 'Collinear tolerance 0.5 world px; near-parallel excludes collinear and requires >=24 world px longitudinal overlap; measured at both <12 and <20 world px separation.',
    spacing: { allCards: distribution(nodeGaps), movableCards: distribution(movableGaps),
      horizontalChannels: distribution(columnGaps), verticalChannels: distribution(rowGaps), edgeToNonendpointCard: distribution(clearances) },
    bounds: Object.fromEntries(Object.entries(allBounds).map(([key, value]) => [key, round(value)])),
    fitZoom: round(Math.min((graph.canvas.width - 120) / allBounds.width, (graph.canvas.height - 160) / allBounds.height)),
    canvas: graph.canvas }
}

export function sharedOverview(first, second) {
  assert.deepEqual(first.canvas, second.canvas)
  const union = bbox([first.bounds, second.bounds])
  const rawZoom = Math.min(0.39, (first.canvas.width - 120) / union.width, (first.canvas.height - 200) / union.height)
  const zoom = Math.floor(rawZoom * 1000) / 1000
  assert(zoom >= 0.15, 'Graph union is too large for the production minimum zoom; do not hide cropping')
  return { zoom, x: round((first.canvas.width - union.width * zoom) / 2 - union.x * zoom),
    y: round(150 + (first.canvas.height - 200 - union.height * zoom) / 2 - union.y * zoom), unionBounds: union }
}

export function cropAtViewport(metrics, viewport) {
  const b = metrics.bounds, left = b.x * viewport.zoom + viewport.x, top = b.y * viewport.zoom + viewport.y
  const right = (b.x + b.width) * viewport.zoom + viewport.x, bottom = (b.y + b.height) * viewport.zoom + viewport.y
  return { left: round(Math.max(0, -left)), top: round(Math.max(0, -top)),
    right: round(Math.max(0, right - metrics.canvas.width)), bottom: round(Math.max(0, bottom - metrics.canvas.height)) }
}
