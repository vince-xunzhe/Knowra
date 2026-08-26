import { useEffect, useRef, useCallback } from 'react'
import cytoscape from 'cytoscape'
import type { GraphData, GraphNode } from '../api/client'

const NODE_COLORS: Record<string, string> = {
  paper: '#7A88C9',
  technique: '#5BAEAA',
  dataset: '#B8A36A',
  concept: '#A48BC7',
  entity: '#B783A4',
  topic: '#719BB8',
  fact: '#B4A77C',
}

const NODE_LABELS: Record<string, string> = {
  paper: '论文',
  technique: '技术',
  dataset: '数据集',
  concept: '概念',
  entity: '实体',
  topic: '主题',
  fact: '事实',
}

interface Props {
  data: GraphData
  onNodeClick: (node: GraphNode) => void
  selectedNodeId: string | null
}

const IDLE_AUTOPLAY_DELAY_MS = 2600
const IDLE_AUTOPLAY_STEP_MS = 1800
const FALLBACK_CANVAS = { width: 960, height: 640 }

interface GraphCanvas {
  width: number
  height: number
}

interface GraphVisuals {
  activeLabelWidth: number
  componentSpacing: number
  degreeBoost: number
  edgeLength: number
  fitPadding: number
  fontSize: number
  gravity: number
  hoverExtra: number
  labelChars: number
  labelWidth: number
  maxZoom: number
  minZoom: number
  nodeOverlap: number
  nodeSize: number
  paperBoost: number
  repulsion: number
  selectedExtra: number
  similarEdgeLength: number
}

interface DensityNode {
  node: cytoscape.NodeSingular
  x: number
  y: number
  degree: number
  density: number
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max)
}

function measureCanvas(container: HTMLElement | null): GraphCanvas {
  if (!container) return FALLBACK_CANVAS
  const rect = container.getBoundingClientRect()
  const width = Math.max(rect.width || container.clientWidth || FALLBACK_CANVAS.width, 320)
  const height = Math.max(rect.height || container.clientHeight || FALLBACK_CANVAS.height, 320)
  return { width, height }
}

function graphVisuals(nodeCount: number, canvas: GraphCanvas): GraphVisuals {
  const count = Math.max(nodeCount, 1)
  const width = Math.max(canvas.width, 320)
  const height = Math.max(canvas.height, 320)
  const shortSide = Math.min(width, height)
  const areaPerNode = (width * height) / count
  const spaciousness = clamp(Math.sqrt(areaPerNode) / 92, 0.68, 1.52)
  const crowding = clamp((count - 36) / 96, 0, 1)
  const narrowness = clamp((680 - shortSide) / 360, 0, 1)
  const nodeScale = clamp(spaciousness * (1 - crowding * 0.16), 0.72, 1.28)
  const edgeScale = clamp(spaciousness * (1 - crowding * 0.08), 0.72, 1.58)
  const edgeLength = Math.round(clamp(178 * edgeScale + shortSide * 0.045, 128, 330))

  return {
    activeLabelWidth: Math.round(clamp(122 * nodeScale + shortSide * 0.025, 96, 168)),
    componentSpacing: Math.round(clamp(edgeLength * 1.08, 148, 380)),
    degreeBoost: Math.round(clamp(13 * nodeScale, 7, 20)),
    edgeLength,
    fitPadding: Math.round(clamp(shortSide * (0.09 - crowding * 0.025), 34, 120)),
    fontSize: Number(clamp(10.8 * nodeScale, 9.2, 12.5).toFixed(1)),
    gravity: Number(clamp(0.11 / edgeScale + narrowness * 0.026, 0.055, 0.16).toFixed(3)),
    hoverExtra: Math.round(clamp(18 * nodeScale, 11, 22)),
    labelChars: Math.round(clamp(20 * nodeScale - crowding * 3, 12, 28)),
    labelWidth: Math.round(clamp(98 * nodeScale + shortSide * 0.018, 72, 132)),
    maxZoom: Number(clamp(2.9 - crowding * 0.25, 2.35, 3).toFixed(2)),
    minZoom: Number(clamp(0.16 + crowding * 0.08 - spaciousness * 0.025, 0.14, 0.28).toFixed(2)),
    nodeOverlap: Math.round(clamp(13 - spaciousness * 2 + crowding * 4, 7, 16)),
    nodeSize: Math.round(clamp(44 * nodeScale, 32, 58)),
    paperBoost: Math.round(clamp(8 * nodeScale, 5, 12)),
    repulsion: Math.round(clamp(34000 * edgeScale * edgeScale * (1 + count / 120), 24000, 92000)),
    selectedExtra: Math.round(clamp(14 * nodeScale, 9, 18)),
    similarEdgeLength: Math.round(clamp(edgeLength + 52 * edgeScale, 170, 410)),
  }
}

function compactLabel(title: string, maxChars: number) {
  const normalized = title.replace(/\s+/g, ' ').trim()
  if (normalized.length <= maxChars) return normalized
  return `${normalized.slice(0, maxChars - 1)}…`
}

function shuffleIds(ids: string[]) {
  const next = [...ids]
  for (let index = next.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1))
    const current = next[index]
    next[index] = next[swapIndex]
    next[swapIndex] = current
  }
  return next
}

function graphLayout(visuals: GraphVisuals, { fit, animate, numIter }: {
  fit: boolean
  animate: boolean
  numIter: number
}): cytoscape.LayoutOptions {
  return {
    name: 'cose',
    animate,
    animationDuration: animate ? (fit ? 900 : 520) : 0,
    fit: false,
    nodeRepulsion: () => visuals.repulsion,
    idealEdgeLength: edge => edge.data('relation_type') === 'similar'
      ? visuals.similarEdgeLength
      : visuals.edgeLength,
    edgeElasticity: edge => edge.data('relation_type') === 'similar' ? 44 : 118,
    nodeOverlap: visuals.nodeOverlap,
    componentSpacing: visuals.componentSpacing,
    gravity: visuals.gravity,
    nestingFactor: 0.9,
    initialTemp: 120,
    coolingFactor: 0.96,
    minTemp: 1.0,
    numIter,
    padding: fit ? visuals.fitPadding : Math.round(visuals.fitPadding * 0.52),
    nodeDimensionsIncludeLabels: true,
    randomize: false,
  }
}

function quantile(sortedValues: number[], q: number) {
  if (sortedValues.length === 0) return 0
  const index = clamp((sortedValues.length - 1) * q, 0, sortedValues.length - 1)
  const lo = Math.floor(index)
  const hi = Math.ceil(index)
  if (lo === hi) return sortedValues[lo]
  return sortedValues[lo] + (sortedValues[hi] - sortedValues[lo]) * (index - lo)
}

function applyGraphEmphasis(
  cy: cytoscape.Core,
  selectedNodeId: string | null,
  hoveredNodeId: string | null,
) {
  if (cy.destroyed()) return
  cy.nodes().removeClass('highlighted hovered neighbor faded')
  cy.edges().removeClass('neighbor faded')

  const activeId = hoveredNodeId || selectedNodeId
  if (!activeId) return

  const target = cy.getElementById(activeId)
  if (!target || target.empty()) return

  cy.elements().addClass('faded')
  const neighborhood = target.closedNeighborhood()
  neighborhood.removeClass('faded').addClass('neighbor')
  target.removeClass('neighbor')
  target.addClass(hoveredNodeId ? 'hovered' : 'highlighted')
}

function nodeVisualData(data: GraphData, visuals: GraphVisuals) {
  const degree = new Map<string, number>()
  for (const edge of data.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1)
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1)
  }
  const maxDegree = Math.max(1, ...degree.values())

  return data.nodes.map(n => {
    const d = degree.get(n.id) ?? 0
    const degreeRatio = Math.sqrt(d / maxDegree)
    const typeBoost = n.node_type === 'paper'
      ? visuals.paperBoost
      : n.node_type === 'concept'
        ? Math.round(visuals.paperBoost * 0.35)
        : 0
    const size = Math.round(visuals.nodeSize + typeBoost + visuals.degreeBoost * degreeRatio)
    const labelChars = n.node_type === 'paper'
      ? Math.round(visuals.labelChars * 1.45)
      : visuals.labelChars

    return {
      id: n.id,
      label: compactLabel(n.title, labelChars),
      fullTitle: n.title,
      node_type: n.node_type,
      promotion_status: n.promotion_status || 'promoted',
      promoted_by: n.promoted_by || '',
      color: NODE_COLORS[n.node_type] || '#94a3b8',
      degree: d,
      fontSize: visuals.fontSize,
      hoverSize: size + visuals.hoverExtra,
      labelWidth: visuals.labelWidth,
      selectedSize: size + visuals.selectedExtra,
      size,
      activeLabelWidth: visuals.activeLabelWidth,
    }
  })
}

function graphElements(data: GraphData, visuals: GraphVisuals) {
  return [
    ...nodeVisualData(data, visuals).map(data => ({ data })),
    ...data.edges.map(e => ({
      data: {
        id: e.id,
        source: e.source,
        target: e.target,
        relation_type: e.relation_type,
        label: e.relation_type !== 'similar' ? e.relation_type : '',
        weight: e.weight,
      },
    })),
  ]
}

function applyResponsiveVisuals(cy: cytoscape.Core, data: GraphData) {
  const visuals = graphVisuals(data.nodes.length, measureCanvas(cy.container()))
  cy.minZoom(visuals.minZoom)
  cy.maxZoom(visuals.maxZoom)
  const visualNodes = nodeVisualData(data, visuals)
  cy.batch(() => {
    for (const visualNode of visualNodes) {
      const node = cy.getElementById(visualNode.id)
      if (!node.empty()) node.data(visualNode)
    }
  })
  return visuals
}

function largestUsefulComponent(cy: cytoscape.Core) {
  const allNodes = cy.nodes().toArray()
  if (allNodes.length === 0) return allNodes

  let largest: cytoscape.NodeSingular[] = []
  for (const component of cy.elements().components()) {
    const nodes = component.nodes().toArray()
    if (nodes.length > largest.length) largest = nodes
  }

  return largest.length >= Math.max(12, allNodes.length * 0.48)
    ? largest
    : allNodes
}

function densityRankedNodes(nodes: cytoscape.NodeSingular[]): DensityNode[] {
  const positioned = nodes.map(node => {
    const p = node.position()
    return {
      node,
      x: p.x,
      y: p.y,
      degree: node.connectedEdges().length,
      density: 1,
    }
  })
  if (positioned.length <= 2) return positioned

  const neighborCount = Math.round(clamp(Math.sqrt(positioned.length) * 1.45, 5, 18))
  return positioned
    .map((item, index) => {
      const distances = positioned
        .map((other, otherIndex) => (
          otherIndex === index ? Infinity : Math.hypot(item.x - other.x, item.y - other.y)
        ))
        .filter(Number.isFinite)
        .sort((a, b) => a - b)
      const localRadius = distances[Math.min(neighborCount - 1, distances.length - 1)] || 1
      const density = (neighborCount / Math.max(localRadius, 1)) * (1 + Math.log1p(item.degree) * 0.18)
      return { ...item, density }
    })
    .sort((a, b) => b.density - a.density)
}

function densityCore(cy: cytoscape.Core) {
  const allNodeCount = cy.nodes().length
  const candidates = largestUsefulComponent(cy)
  const ranked = densityRankedNodes(candidates)
  if (ranked.length === 0) return { core: ranked, center: { x: 0, y: 0 } }

  const usingMainComponent = candidates.length < allNodeCount
  const keepRatio = usingMainComponent
    ? (ranked.length >= 120 ? 0.84 : ranked.length >= 60 ? 0.88 : 1)
    : (ranked.length >= 120 ? 0.68 : ranked.length >= 60 ? 0.76 : 0.88)
  const minCore = Math.min(ranked.length, Math.max(10, Math.round(Math.sqrt(allNodeCount) * 2.2)))
  let core = ranked.slice(0, Math.max(minCore, Math.round(ranked.length * keepRatio)))

  const weighted = core.reduce(
    (acc, item) => ({
      x: acc.x + item.x * item.density,
      y: acc.y + item.y * item.density,
      weight: acc.weight + item.density,
    }),
    { x: 0, y: 0, weight: 0 },
  )
  const center = weighted.weight > 0
    ? { x: weighted.x / weighted.weight, y: weighted.y / weighted.weight }
    : { x: core[0].x, y: core[0].y }

  if (core.length > minCore) {
    const distances = core
      .map(item => Math.hypot(item.x - center.x, item.y - center.y))
      .sort((a, b) => a - b)
    const distanceLimit = quantile(distances, 0.9) * 1.12
    core = core.filter(item => Math.hypot(item.x - center.x, item.y - center.y) <= distanceLimit)
    if (core.length < minCore) core = ranked.slice(0, minCore)
  }

  const finalWeighted = core.reduce(
    (acc, item) => ({
      x: acc.x + item.x * item.density,
      y: acc.y + item.y * item.density,
      weight: acc.weight + item.density,
    }),
    { x: 0, y: 0, weight: 0 },
  )
  const finalCenter = finalWeighted.weight > 0
    ? { x: finalWeighted.x / finalWeighted.weight, y: finalWeighted.y / finalWeighted.weight }
    : center

  return { core, center: finalCenter }
}

function applyDensityViewport(cy: cytoscape.Core, visuals: GraphVisuals, animate: boolean) {
  if (cy.destroyed()) return
  const nodes = cy.nodes()
  if (nodes.length === 0) return
  if (nodes.length <= 8) {
    if (animate) cy.animate({ fit: { eles: nodes, padding: visuals.fitPadding } }, { duration: 260 })
    else cy.fit(nodes, visuals.fitPadding)
    return
  }

  const canvas = measureCanvas(cy.container())
  const { core, center } = densityCore(cy)
  if (core.length === 0) return

  const xs = core.map(item => item.x).sort((a, b) => a - b)
  const ys = core.map(item => item.y).sort((a, b) => a - b)
  const coreWidth = Math.max(quantile(xs, 0.96) - quantile(xs, 0.04), visuals.nodeSize * 4)
  const coreHeight = Math.max(quantile(ys, 0.96) - quantile(ys, 0.04), visuals.nodeSize * 4)
  const padding = clamp(visuals.fitPadding * 1.34, 58, 168)
  const availableWidth = Math.max(canvas.width - padding * 2, canvas.width * 0.48)
  const availableHeight = Math.max(canvas.height - padding * 2, canvas.height * 0.48)
  const densityMaxZoom = clamp(1.02 - nodes.length / 920, 0.74, 1)
  const breathingScale = nodes.length >= 120 ? 0.74 : nodes.length >= 60 ? 0.78 : 0.84
  const zoom = clamp(
    Math.min(availableWidth / coreWidth, availableHeight / coreHeight, densityMaxZoom) * breathingScale,
    cy.minZoom(),
    cy.maxZoom(),
  )
  const pan = {
    x: canvas.width / 2 - center.x * zoom,
    y: canvas.height / 2 - center.y * zoom,
  }

  if (animate) {
    cy.animate({ zoom, pan }, { duration: 320 })
  } else {
    cy.zoom(zoom)
    cy.pan(pan)
  }
}

export default function KnowledgeGraph({ data, onNodeClick, selectedNodeId }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)
  const activeLayoutRef = useRef<cytoscape.Layouts | null>(null)
  const graphDataRef = useRef<GraphData>(data)
  const dataNodesRef = useRef<GraphNode[]>(data.nodes)
  const onNodeClickRef = useRef(onNodeClick)
  const focusedNodeIdRef = useRef<string | null>(null)
  const selectedNodeIdRef = useRef<string | null>(selectedNodeId)
  const hoveredNodeIdRef = useRef<string | null>(null)
  const didInitialDataSyncRef = useRef(false)
  const relayoutTimerRef = useRef<number | null>(null)
  const idleAutoplayDelayRef = useRef<number | null>(null)
  const idleAutoplayIntervalRef = useRef<number | null>(null)
  const idleAutoplayNodeIdRef = useRef<string | null>(null)
  const idleAutoplayPaperIdsRef = useRef<string[]>([])
  const idleAutoplayIndexRef = useRef(0)
  const pauseIdleAutoplayRef = useRef<((clearAutoHover?: boolean) => void) | null>(null)
  const scheduleIdleAutoplayRef = useRef<(() => void) | null>(null)

  const handleNodeClick = useCallback((nodeId: string) => {
    const node = dataNodesRef.current.find(n => n.id === nodeId)
    if (node) onNodeClickRef.current(node)
  }, [])

  useEffect(() => {
    graphDataRef.current = data
    dataNodesRef.current = data.nodes
  }, [data])

  useEffect(() => {
    onNodeClickRef.current = onNodeClick
  }, [onNodeClick])

  const stopActiveLayout = useCallback(() => {
    const layout = activeLayoutRef.current
    if (!layout) return
    try {
      layout.stop()
      layout.removeAllListeners()
    } catch {
      // Best-effort cleanup; Cytoscape can already be tearing down.
    }
    activeLayoutRef.current = null
  }, [])

  const stopRuntimeMotion = useCallback((cy: cytoscape.Core) => {
    stopActiveLayout()
    try {
      cy.stop(true, true)
    } catch {
      // Core may already be mid-destroy on older Cytoscape internals.
    }
    try {
      cy.elements().stop(true, true)
    } catch {
      // Best-effort cleanup for node/edge animations spawned by layouts.
    }
  }, [stopActiveLayout])

  const startLayout = useCallback((
    cy: cytoscape.Core,
    dataForLayout: GraphData,
    options: { fit: boolean; animate: boolean; numIter: number },
  ) => {
    if (cy.destroyed()) return
    const visuals = applyResponsiveVisuals(cy, dataForLayout)
    stopRuntimeMotion(cy)
    // Defer the new layout by one animation frame. Cytoscape's old
    // layout schedules its refresh() via requestAnimationFrame; even
    // after layout.stop() the last queued frame still fires once,
    // and if we've already removed/replaced elements it walks a
    // null collection and throws "Cannot read properties of null
    // (reading 'notify')". Waiting one RAF lets that orphan frame
    // run to completion (it's a no-op against the now-empty graph)
    // before we kick the new layout.
    requestAnimationFrame(() => {
      if (cy.destroyed()) return
      const layout = cy.layout(graphLayout(visuals, options))
      activeLayoutRef.current = layout
      layout.one('layoutstop', () => {
        if (activeLayoutRef.current === layout) {
          activeLayoutRef.current = null
        }
        if (options.fit && !cy.destroyed()) {
          applyDensityViewport(cy, visuals, options.animate)
        }
      })
      layout.run()
    })
  }, [stopRuntimeMotion])

  useEffect(() => {
    if (!containerRef.current) return
    const initialVisuals = graphVisuals(data.nodes.length, measureCanvas(containerRef.current))

    const cy = cytoscape({
      container: containerRef.current,
      minZoom: initialVisuals.minZoom,
      maxZoom: initialVisuals.maxZoom,
      motionBlur: true,
      pixelRatio: 'auto',
      textureOnViewport: false,
      elements: graphElements(data, initialVisuals),
      style: [
        {
          selector: 'node',
          style: {
            'background-color': 'data(color)',
            label: 'data(label)',
            color: '#f1f5f9',
            'font-size': 'data(fontSize)',
            'font-weight': 500,
            'text-valign': 'bottom',
            'text-halign': 'center',
            'text-margin-y': 12,
            opacity: 0.96,
            width: 'data(size)',
            height: 'data(size)',
            'text-wrap': 'wrap',
            'text-max-width': 'data(labelWidth)',
            'text-opacity': 0.88,
            'border-width': 2,
            'border-color': '#0b0d12',
            'text-outline-width': 0,
            'text-background-color': '#020617',
            'text-background-opacity': 0.82,
            'text-background-shape': 'roundrectangle',
            'text-background-padding': '5px',
            'overlay-opacity': 0,
            'transition-property': 'opacity background-opacity border-color border-width width height text-opacity text-background-opacity font-size',
            'transition-duration': 170,
            'transition-timing-function': 'ease-out',
          },
        },
        {
          selector: 'node[node_type = "paper"]',
          style: {
            'font-weight': 600,
          },
        },
        // Pending: dashed amber border + slight transparency, so the user sees
        // "this is in review" at a glance.
        {
          selector: 'node[promotion_status = "pending"]',
          style: {
            'border-style': 'dashed',
            'border-width': 2,
            'border-color': '#B8A36A',
            'background-opacity': 0.55,
          },
        },
        // Rejected: ghosted out — only visible when the rescue panel
        // explicitly asks for them.
        {
          selector: 'node[promotion_status = "rejected"]',
          style: {
            'background-opacity': 0.18,
            'border-color': '#475569',
            'border-style': 'dotted',
            color: '#475569',
          },
        },
        {
          selector: 'node:selected, node.highlighted',
          style: {
            label: 'data(fullTitle)',
            'border-width': 3,
            'border-color': '#ffffff',
            'border-style': 'solid',
            'background-opacity': 1,
            width: 'data(selectedSize)',
            height: 'data(selectedSize)',
            'font-size': '13px',
            'text-max-width': 'data(activeLabelWidth)',
          },
        },
        {
          selector: 'node.hovered',
          style: {
            label: 'data(fullTitle)',
            'border-width': 3,
            'border-color': '#c4b5fd',
            'border-style': 'solid',
            'background-opacity': 1,
            width: 'data(hoverSize)',
            height: 'data(hoverSize)',
            'font-size': '13px',
            'text-max-width': 'data(activeLabelWidth)',
            'text-background-opacity': 0.96,
            'z-index': 999,
          },
        },
        {
          selector: 'node.neighbor',
          style: {
            'background-opacity': 0.98,
            'border-color': '#94a3b8',
            'text-opacity': 1,
            'text-background-opacity': 0.84,
          },
        },
        {
          selector: 'node.faded',
          style: {
            opacity: 0.17,
            'text-opacity': 0.08,
            'text-background-opacity': 0.06,
          },
        },
        {
          selector: 'edge',
          style: {
            width: 1.1,
            'line-color': '#334155',
            'line-opacity': 0.34,
            'target-arrow-color': '#475569',
            'target-arrow-shape': 'triangle',
            'arrow-scale': 0.75,
            'curve-style': 'bezier',
            label: 'data(label)',
            'font-size': '10px',
            color: '#94a3b8',
            'text-outline-width': 2,
            'text-outline-color': '#0b0d12',
            'text-opacity': 0,
            'text-background-opacity': 0,
            'transition-property': 'line-opacity width line-color target-arrow-color text-opacity',
            'transition-duration': 170,
            'transition-timing-function': 'ease-out',
          },
        },
        {
          selector: 'edge[relation_type = "similar"]',
          style: {
            width: 0.8,
            'line-style': 'dashed',
            'line-color': '#1e293b',
            'line-opacity': 0.18,
            'target-arrow-color': '#1e293b',
            'arrow-scale': 0.55,
          },
        },
        {
          selector: 'edge[weight >= 0.9]',
          style: { 'line-color': '#64748b', width: 1.6, 'line-opacity': 0.72 },
        },
        {
          selector: 'edge.neighbor',
          style: {
            width: 2.1,
            'line-color': '#94a3b8',
            'line-opacity': 0.9,
            'target-arrow-color': '#cbd5e1',
            'text-opacity': 0.95,
          },
        },
        {
          selector: 'edge.faded',
          style: {
            'line-opacity': 0.045,
            'text-opacity': 0,
            'target-arrow-color': '#1f2937',
          },
        },
      ],
      // IMPORTANT: don't auto-run cose here. The layout would own its own
      // RAF chain that we can't reliably stop on rapid remount, leading
      // to "Cannot read properties of null (reading 'notify')" when a
      // queued frame fires after cy.destroy(). The tracked `startLayout`
      // call below runs the real layout and stores it in
      // activeLayoutRef so cleanup can stop it cleanly.
      layout: { name: 'preset' },
    })
    const cyInternal = cy as cytoscape.Core & {
      headless?: () => boolean
      _private?: { renderer?: { isHeadless?: () => boolean } | null }
    }
    const originalHeadless = typeof cyInternal.headless === 'function'
      ? cyInternal.headless.bind(cy)
      : () => false
    cyInternal.headless = () => {
      const renderer = cyInternal._private?.renderer
      if (!renderer || typeof renderer.isHeadless !== 'function') {
        return true
      }
      return originalHeadless()
    }

    const clearIdleAutoplayTimers = () => {
      if (idleAutoplayDelayRef.current != null) {
        window.clearTimeout(idleAutoplayDelayRef.current)
        idleAutoplayDelayRef.current = null
      }
      if (idleAutoplayIntervalRef.current != null) {
        window.clearInterval(idleAutoplayIntervalRef.current)
        idleAutoplayIntervalRef.current = null
      }
    }

    const collectPaperAutoplayIds = () => shuffleIds(
      cy
        .nodes('[node_type = "paper"]')
        .toArray()
        .filter(node => node.connectedEdges().length > 0)
        .map(node => node.id()),
    )

    const pauseIdleAutoplay = (clearAutoHover = true) => {
      clearIdleAutoplayTimers()
      if (clearAutoHover && idleAutoplayNodeIdRef.current) {
        if (hoveredNodeIdRef.current === idleAutoplayNodeIdRef.current) {
          hoveredNodeIdRef.current = null
        }
        idleAutoplayNodeIdRef.current = null
      }
    }

    const runIdleAutoplayStep = () => {
      if (!cyRef.current || selectedNodeIdRef.current || hoveredNodeIdRef.current && hoveredNodeIdRef.current !== idleAutoplayNodeIdRef.current) {
        return
      }
      if (idleAutoplayPaperIdsRef.current.length === 0 || idleAutoplayIndexRef.current >= idleAutoplayPaperIdsRef.current.length) {
        idleAutoplayPaperIdsRef.current = collectPaperAutoplayIds()
        idleAutoplayIndexRef.current = 0
      }
      const nextNodeId = idleAutoplayPaperIdsRef.current[idleAutoplayIndexRef.current]
      if (!nextNodeId) return
      idleAutoplayIndexRef.current += 1
      idleAutoplayNodeIdRef.current = nextNodeId
      hoveredNodeIdRef.current = nextNodeId
      applyGraphEmphasis(cyRef.current, selectedNodeIdRef.current, nextNodeId)
    }

    const startIdleAutoplay = () => {
      if (!cyRef.current || selectedNodeIdRef.current || hoveredNodeIdRef.current) return
      pauseIdleAutoplay(false)
      idleAutoplayPaperIdsRef.current = collectPaperAutoplayIds()
      idleAutoplayIndexRef.current = 0
      if (idleAutoplayPaperIdsRef.current.length === 0) return
      runIdleAutoplayStep()
      idleAutoplayIntervalRef.current = window.setInterval(runIdleAutoplayStep, IDLE_AUTOPLAY_STEP_MS)
    }

    const scheduleIdleAutoplay = () => {
      pauseIdleAutoplay(true)
      if (selectedNodeIdRef.current || hoveredNodeIdRef.current) return
      idleAutoplayDelayRef.current = window.setTimeout(startIdleAutoplay, IDLE_AUTOPLAY_DELAY_MS)
    }

    pauseIdleAutoplayRef.current = pauseIdleAutoplay
    scheduleIdleAutoplayRef.current = scheduleIdleAutoplay

    const scheduleResponsiveRelayout = (delay = 180) => {
      if (relayoutTimerRef.current != null) {
        window.clearTimeout(relayoutTimerRef.current)
      }
      relayoutTimerRef.current = window.setTimeout(() => {
        const currentCy = cyRef.current
        if (!currentCy || currentCy.destroyed()) return
        currentCy.resize()
        startLayout(
          currentCy,
          graphDataRef.current,
          { fit: true, animate: true, numIter: 820 },
        )
        applyGraphEmphasis(currentCy, selectedNodeIdRef.current, hoveredNodeIdRef.current)
      }, delay)
    }

    const resizeObserver = new ResizeObserver(() => {
      if (!cyRef.current || cyRef.current.destroyed()) return
      scheduleResponsiveRelayout()
    })
    resizeObserver.observe(containerRef.current)

    cy.on('tap', 'node', evt => {
      pauseIdleAutoplay()
      hoveredNodeIdRef.current = null
      const nodeId = evt.target.id()
      handleNodeClick(nodeId)
    })
    cy.on('mouseover', 'node', evt => {
      pauseIdleAutoplay()
      hoveredNodeIdRef.current = evt.target.id()
      applyGraphEmphasis(cy, selectedNodeIdRef.current, hoveredNodeIdRef.current)
    })
    cy.on('mouseout', 'node', evt => {
      if (hoveredNodeIdRef.current === evt.target.id()) {
        hoveredNodeIdRef.current = null
      }
      applyGraphEmphasis(cy, selectedNodeIdRef.current, hoveredNodeIdRef.current)
      scheduleIdleAutoplay()
    })
    cy.on('grab', 'node', evt => {
      pauseIdleAutoplay()
      hoveredNodeIdRef.current = evt.target.id()
      applyGraphEmphasis(cy, selectedNodeIdRef.current, hoveredNodeIdRef.current)
    })
    cy.on('dragfree', 'node', evt => {
      pauseIdleAutoplay(false)
      hoveredNodeIdRef.current = evt.target.id()
      if (relayoutTimerRef.current != null) {
        window.clearTimeout(relayoutTimerRef.current)
      }
      relayoutTimerRef.current = window.setTimeout(() => {
        if (!cyRef.current) return
        startLayout(
          cyRef.current,
          graphDataRef.current,
          { fit: false, animate: true, numIter: 520 },
        )
        applyGraphEmphasis(cyRef.current, selectedNodeIdRef.current, hoveredNodeIdRef.current)
      }, 60)
    })
    cy.on('tap', evt => {
      if (evt.target === cy) {
        pauseIdleAutoplay()
        hoveredNodeIdRef.current = null
        applyGraphEmphasis(cy, selectedNodeIdRef.current, null)
        scheduleIdleAutoplay()
      }
    })

    cyRef.current = cy
    selectedNodeIdRef.current = selectedNodeId
    applyGraphEmphasis(cy, selectedNodeId, null)
    scheduleIdleAutoplay()
    startLayout(cy, data, { fit: true, animate: true, numIter: 2200 })

    return () => {
      if (relayoutTimerRef.current != null) {
        window.clearTimeout(relayoutTimerRef.current)
        relayoutTimerRef.current = null
      }
      clearIdleAutoplayTimers()
      resizeObserver.disconnect()
      pauseIdleAutoplayRef.current = null
      scheduleIdleAutoplayRef.current = null
      didInitialDataSyncRef.current = false
      stopRuntimeMotion(cy)
      try {
        cy.removeAllListeners()
      } catch {
        // already partially torn down
      }
      try {
        cy.destroy()
      } catch {
        // Belt-and-suspenders: a queued RAF can still call into a cy
        // mid-destroy on rapid remount; swallow rather than crash the
        // page. The cleanup above already stopped layouts and animations.
      }
      cyRef.current = null
    }
  }, [handleNodeClick, startLayout, stopRuntimeMotion]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const cy = cyRef.current
    if (!cy || cy.destroyed()) return
    if (!didInitialDataSyncRef.current) {
      didInitialDataSyncRef.current = true
      return
    }

    pauseIdleAutoplayRef.current?.(true)
    hoveredNodeIdRef.current = null
    idleAutoplayNodeIdRef.current = null
    focusedNodeIdRef.current = null
    stopRuntimeMotion(cy)

    cy.batch(() => {
      cy.elements().remove()
      const visuals = graphVisuals(data.nodes.length, measureCanvas(cy.container()))
      cy.minZoom(visuals.minZoom)
      cy.maxZoom(visuals.maxZoom)
      cy.add(graphElements(data, visuals))
    })
    cy.resize()
    startLayout(cy, data, { fit: true, animate: true, numIter: 2200 })
    applyGraphEmphasis(cy, selectedNodeIdRef.current, null)
    if (!selectedNodeIdRef.current) {
      scheduleIdleAutoplayRef.current?.()
    }
  }, [data, startLayout, stopRuntimeMotion])

  // Highlight selected node
  useEffect(() => {
    const cy = cyRef.current
    if (!cy || cy.destroyed()) return
    selectedNodeIdRef.current = selectedNodeId
    pauseIdleAutoplayRef.current?.(Boolean(selectedNodeId))
    applyGraphEmphasis(cy, selectedNodeId, hoveredNodeIdRef.current)
    if (!selectedNodeId) {
      focusedNodeIdRef.current = null
      if (!hoveredNodeIdRef.current) {
        scheduleIdleAutoplayRef.current?.()
      }
      return
    }
    const target = cy.getElementById(selectedNodeId)
    if (!target || target.empty()) return
    const shouldAnimate = focusedNodeIdRef.current !== selectedNodeId
    focusedNodeIdRef.current = selectedNodeId
    if (shouldAnimate) {
      const nextZoom = Math.max(cy.zoom(), 1.08)
      cy.animate(
        {
          center: { eles: target },
          zoom: nextZoom,
        },
        {
          duration: 280,
        },
      )
    }
  }, [selectedNodeId, data])

  return (
    <div className="relative w-full h-full">
      <div ref={containerRef} className="w-full h-full" />
      {/* Legend — compact horizontal strip at bottom-right; the left rail
          (PipelineConsole) handles all stage controls so this area can stay
          minimal. */}
      <div className="absolute bottom-3 right-3 bg-slate-900/70 backdrop-blur rounded-lg px-2.5 py-1.5 border border-slate-800/80">
        <div className="flex items-center gap-3">
          {['paper', 'concept', 'technique', 'dataset'].map(type => (
            <div key={type} className="flex items-center gap-1.5">
              <span
                className="w-2 h-2 rounded-full inline-block"
                style={{ background: NODE_COLORS[type] }}
              />
              <span className="text-[10.5px] text-slate-400">{NODE_LABELS[type]}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
