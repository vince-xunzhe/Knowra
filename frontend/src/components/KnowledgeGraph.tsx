import { useEffect, useRef, useCallback } from 'react'
import cytoscape from 'cytoscape'
import type { GraphData, GraphNode } from '../api/client'

const NODE_COLORS: Record<string, string> = {
  paper: '#6366f1',
  technique: '#22c55e',
  dataset: '#f59e0b',
  concept: '#14b8a6',
  entity: '#ec4899',
  topic: '#6366f1',
  fact: '#f59e0b',
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

function compactLabel(title: string, maxChars: number) {
  const normalized = title.replace(/\s+/g, ' ').trim()
  if (normalized.length <= maxChars) return normalized
  return `${normalized.slice(0, maxChars - 1)}…`
}

function graphRepulsion(nodeCount: number) {
  if (nodeCount >= 90) return 56000
  if (nodeCount >= 50) return 48000
  if (nodeCount >= 24) return 39000
  return 32000
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

function graphLayout(nodeCount: number, { fit, animate, numIter }: {
  fit: boolean
  animate: boolean
  numIter: number
}): cytoscape.LayoutOptions {
  return {
    name: 'cose',
    animate,
    animationDuration: animate ? (fit ? 900 : 520) : 0,
    fit,
    nodeRepulsion: () => graphRepulsion(nodeCount),
    idealEdgeLength: edge => edge.data('relation_type') === 'similar' ? 270 : 215,
    edgeElasticity: edge => edge.data('relation_type') === 'similar' ? 48 : 132,
    nodeOverlap: 12,
    componentSpacing: 230,
    gravity: 0.1,
    nestingFactor: 0.9,
    initialTemp: 120,
    coolingFactor: 0.96,
    minTemp: 1.0,
    numIter,
    padding: fit ? 112 : 56,
    nodeDimensionsIncludeLabels: true,
    randomize: false,
  }
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

function graphElements(data: GraphData) {
  return [
    ...data.nodes.map(n => ({
      data: {
        id: n.id,
        label: compactLabel(n.title, n.node_type === 'paper' ? 28 : 18),
        fullTitle: n.title,
        node_type: n.node_type,
        promotion_status: n.promotion_status || 'promoted',
        promoted_by: n.promoted_by || '',
        color: NODE_COLORS[n.node_type] || '#94a3b8',
      },
    })),
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

export default function KnowledgeGraph({ data, onNodeClick, selectedNodeId }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)
  const activeLayoutRef = useRef<cytoscape.Layouts | null>(null)
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
    dataNodesRef.current = data.nodes
  }, [data.nodes])

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
    nodeCount: number,
    options: { fit: boolean; animate: boolean; numIter: number },
  ) => {
    if (cy.destroyed()) return
    stopRuntimeMotion(cy)
    const layout = cy.layout(graphLayout(nodeCount, options))
    activeLayoutRef.current = layout
    layout.one('layoutstop', () => {
      if (activeLayoutRef.current === layout) {
        activeLayoutRef.current = null
      }
    })
    layout.run()
  }, [stopRuntimeMotion])

  useEffect(() => {
    if (!containerRef.current) return

    const cy = cytoscape({
      container: containerRef.current,
      minZoom: 0.28,
      maxZoom: 2.4,
      motionBlur: true,
      pixelRatio: 'auto',
      textureOnViewport: false,
      elements: graphElements(data),
      style: [
        {
          selector: 'node',
          style: {
            'background-color': 'data(color)',
            label: 'data(label)',
            color: '#f1f5f9',
            'font-size': '11px',
            'font-weight': 500,
            'text-valign': 'bottom',
            'text-halign': 'center',
            'text-margin-y': 12,
            opacity: 0.96,
            width: 52,
            height: 52,
            'text-wrap': 'wrap',
            'text-max-width': '112px',
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
            width: 60,
            height: 60,
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
            'border-color': '#fbbf24',
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
            width: 70,
            height: 70,
            'font-size': '13px',
            'text-max-width': '132px',
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
            width: 74,
            height: 74,
            'font-size': '13px',
            'text-max-width': '136px',
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
          cyRef.current.nodes().length,
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
    startLayout(cy, data.nodes.length, { fit: true, animate: true, numIter: 2200 })

    return () => {
      if (relayoutTimerRef.current != null) {
        window.clearTimeout(relayoutTimerRef.current)
        relayoutTimerRef.current = null
      }
      clearIdleAutoplayTimers()
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
      cy.add(graphElements(data))
    })
    cy.resize()
    startLayout(cy, data.nodes.length, { fit: true, animate: true, numIter: 2200 })
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
      {/* Legend — compact horizontal strip at bottom-right; the bottom-left
          area is reserved for the CandidatePanel floating card. */}
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
