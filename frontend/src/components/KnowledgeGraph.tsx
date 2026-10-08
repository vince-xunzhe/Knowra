import { useEffect, useRef, useState } from 'react'
import cytoscape from 'cytoscape'
import { Focus, Minus, Plus, Pin } from 'lucide-react'
import { t as tr } from '../i18n/catalog'
import { useLocale, useTheme } from '../i18n/preferences'
import { relationLabels, topicColor, type ExplorerGraph, type ViewEdge } from '../lib/graphExplorer'

interface Props {
  view: ExplorerGraph
  layoutKey: string
  selectedNodeId: string | null
  selectedEdgeId?: string | null
  onNodeClick: (id: string) => void
  onEdgeClick: (edge: ViewEdge) => void
}
function fitGraph(cy: cytoscape.Core) {
  if (!cy.nodes().length) return
  cy.fit(undefined, 64)
  if (cy.zoom() > 1) { cy.zoom(1); cy.center() }
}
type SavedLayout = { positions: Map<string, cytoscape.Position>; pan: cytoscape.Position; zoom: number }

/** Stable preset coordinates: interaction never starts a background force layout. */
export default function KnowledgeGraph({ view, layoutKey, selectedNodeId, selectedEdgeId, onNodeClick, onEdgeClick }: Props) {
  const locale = useLocale(), theme = useTheme()
  const host = useRef<HTMLDivElement>(null), cyRef = useRef<cytoscape.Core | null>(null)
  const callbacks = useRef({ onNodeClick, onEdgeClick, view })
  const layouts = useRef(new Map<string, SavedLayout>())
  const currentKey = useRef<string | null>(null)
  const autoFit = useRef(true)
  const [pinned, setPinned] = useState<string[]>([])
  useEffect(() => { callbacks.current = { onNodeClick, onEdgeClick, view } }, [onNodeClick, onEdgeClick, view])

  useEffect(() => {
    const container = host.current
    if (!container) return
    const cy = cytoscape({ container, elements: [], layout: { name: 'preset' }, minZoom: 0.04, maxZoom: 1.8, wheelSensitivity: 0.25, selectionType: 'single' })
    cyRef.current = cy
    cy.on('tap', 'node', e => callbacks.current.onNodeClick(e.target.id()))
    cy.on('tap', 'edge', e => {
      const edge = callbacks.current.view.edges.find(edge => edge.id === e.target.id())
      if (edge) callbacks.current.onEdgeClick(edge)
    })
    cy.on('mouseover', 'edge', e => e.target.addClass('revealed'))
    cy.on('mouseout', 'edge', e => e.target.removeClass('revealed'))
    cy.on('mouseover', 'node', e => { e.target.addClass('hovered'); e.target.connectedEdges().addClass('revealed') })
    cy.on('mouseout', 'node', e => { e.target.removeClass('hovered'); e.target.connectedEdges().removeClass('revealed') })
    cy.on('zoom', () => {
      cy.nodes().toggleClass('zoom-label', cy.zoom() >= 0.5)
      cy.nodes().style('font-size', Math.min(24, 12 / cy.zoom()))
    })
    cy.on('dragpan scrollzoom grab', () => { autoFit.current = false })
    const observer = new ResizeObserver(() => {
      const oldW = cy.width(), oldH = cy.height(), pan = cy.pan()
      cy.resize()
      if (autoFit.current) fitGraph(cy)
      else cy.pan({ x: pan.x + (cy.width() - oldW) / 2, y: pan.y + (cy.height() - oldH) / 2 })
    })
    observer.observe(container)
    return () => { observer.disconnect(); cy.destroy(); cyRef.current = null; currentKey.current = null }
  }, [])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    const css = getComputedStyle(document.documentElement)
    const token = (name: string) => css.getPropertyValue(name).trim()
    const text = token('--graph-label'), background = token('--graph-label-bg'), edge = token('--graph-edge')
    cy.style().fromJson([
      { selector: 'node', style: { 'background-color': 'data(color)', width: 26, height: 26, label: 'data(label)', color: text, 'font-size': 12, 'text-wrap': 'wrap', 'text-max-width': '160px', 'text-valign': 'bottom', 'text-margin-y': 10, 'text-background-color': background, 'text-background-opacity': 0.92, 'text-background-padding': '3px', 'text-opacity': 0, 'border-width': 2, 'border-color': background, 'overlay-opacity': 0 } },
      { selector: 'node[type="technique"], node[type="concept"]', style: { shape: 'round-rectangle', width: 30, height: 25 } },
      { selector: 'node[type="dataset"]', style: { shape: 'diamond', width: 33, height: 33 } },
      { selector: 'node[type="group"]', style: { width: 'data(size)', height: 'data(size)', 'background-opacity': 0.28, 'border-color': 'data(color)', 'border-width': 2, 'font-size': 14, 'text-max-width': '210px', 'text-opacity': 1 } },
      { selector: 'node[status="pending"]', style: { 'border-style': 'dashed', 'border-color': '#b8a36a', 'border-width': 3 } },
      { selector: 'node[status="rejected"]', style: { 'border-style': 'dotted', 'background-opacity': 0.3, 'border-color': edge } },
      { selector: 'node.zoom-label, node.highlighted, node.hovered', style: { 'text-opacity': 1 } },
      { selector: 'node.highlighted, node.hovered', style: { 'border-color': text, 'border-width': 3, label: 'data(activeLabel)', 'z-index': 10 } },
      { selector: 'edge', style: { width: 1.4, 'line-color': edge, 'target-arrow-color': edge, 'target-arrow-shape': 'triangle', 'arrow-scale': 0.8, 'curve-style': 'bezier', label: 'data(label)', color: text, 'font-size': 11, 'text-background-color': background, 'text-background-opacity': 1, 'text-background-padding': '3px', 'text-rotation': 'autorotate', 'text-opacity': 0, 'overlay-opacity': 0 } },
      { selector: 'edge[relation="similar"]', style: { 'line-style': 'dashed', 'line-opacity': 0.45, 'target-arrow-shape': 'none' } },
      { selector: 'edge[aggregate]', style: { width: 'data(lineWidth)', 'line-opacity': 0.45, 'target-arrow-shape': 'none', 'text-opacity': 0 } },
      { selector: 'edge.revealed, edge.highlighted, edge.path-edge', style: { 'text-opacity': 1, width: 2, 'line-color': text, 'target-arrow-color': text } },
    ]).update()
  }, [theme])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    const changedScope = currentKey.current !== layoutKey
    const topologyChanged = cy.nodes().length !== view.nodes.length || view.nodes.some(n => cy.getElementById(n.id).empty())
    if (changedScope && currentKey.current) {
      layouts.current.set(currentKey.current, { positions: new Map(cy.nodes().map(n => [n.id(), { ...n.position() }])), pan: { ...cy.pan() }, zoom: cy.zoom() })
      // Bound retained navigation layouts for long reading sessions.
      if (layouts.current.size > 30) layouts.current.delete(layouts.current.keys().next().value!)
    }
    const saved = changedScope ? layouts.current.get(layoutKey) : null
    const ids = new Set([...view.nodes, ...view.edges].map(n => n.id))
    cy.batch(() => {
      cy.elements().filter(n => !ids.has(n.id())).remove()
      for (const n of view.nodes) {
        const label = n.title.length > 54 ? n.title.slice(0, 53) + '…' : n.title
        const data = { id: n.id, label: n.count !== undefined ? `${label}\n${tr('{count} 个节点', { count: n.count })}` : label, activeLabel: n.title.length > 120 ? n.title.slice(0, 119) + '…' : n.title, type: n.type, status: n.status || '', color: topicColor(n.topic), size: Math.min(110, 62 + Math.sqrt(n.count || 1) * 3) }
        const existing = cy.getElementById(n.id)
        if (existing.empty()) cy.add({ group: 'nodes', data, position: saved?.positions.get(n.id) || { x: n.x, y: n.y } })
        else { existing.data(data); if (changedScope) existing.position(saved?.positions.get(n.id) || { x: n.x, y: n.y }) }
      }
      for (const e of view.edges) {
        const data = { id: e.id, source: e.source, target: e.target, relation: e.relation, label: e.count !== undefined ? tr('{count} 条关系', { count: e.count }) : tr(relationLabels[e.relation] || e.relation), ...(e.count !== undefined ? { aggregate: true, lineWidth: 1 + Math.log1p(e.count) * 0.4 } : {}) }
        const existing = cy.getElementById(e.id)
        if (existing.empty()) cy.add({ group: 'edges', data })
        else existing.data(data)
      }
      cy.edges().toggleClass('path-edge', layoutKey.startsWith('path:'))
    })
    cy.resize()
    if (changedScope) {
      if (saved) { cy.viewport({ zoom: saved.zoom, pan: saved.pan }); autoFit.current = false }
      else { fitGraph(cy); autoFit.current = true }
    }
    if (!changedScope && topologyChanged && autoFit.current) fitGraph(cy)
    cy.nodes().toggleClass('zoom-label', cy.zoom() >= 0.5)
    currentKey.current = layoutKey
  }, [view, layoutKey, locale])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.elements().removeClass('highlighted')
    if (selectedNodeId) cy.getElementById(selectedNodeId).addClass('highlighted')
    if (selectedEdgeId) cy.getElementById(selectedEdgeId).addClass('highlighted')
    cy.nodes().forEach(n => { if (pinned.includes(n.id())) n.lock(); else n.unlock() })
  }, [view, selectedNodeId, selectedEdgeId, pinned])

  const fit = () => { const cy = cyRef.current; if (cy) { fitGraph(cy); autoFit.current = true } }
  const zoom = (factor: number) => { const cy = cyRef.current; autoFit.current = false; if (cy) cy.zoom({ level: cy.zoom() * factor, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } }) }
  return <div className="ge-canvas-shell">
    <div ref={host} className="ge-canvas" data-testid="knowledge-graph-canvas" role="img" aria-label={tr('交互图谱；也可通过下方列表选择节点和关系。')} />
    <div className="ge-canvas-tools" aria-label={tr('图谱操作')}>
      <button type="button" onClick={fit} title={tr('适应画布')} aria-label={tr('适应画布')}><Focus size={16} /></button>
      <button type="button" onClick={() => zoom(1.25)} aria-label={tr('放大')}><Plus size={16} /></button>
      <button type="button" onClick={() => zoom(0.8)} aria-label={tr('缩小')}><Minus size={16} /></button>
      {selectedNodeId && view.nodes.some(n => n.id === selectedNodeId) && <button type="button" aria-pressed={pinned.includes(selectedNodeId)} aria-label={tr('固定节点')} title={tr('固定节点')} onClick={() => setPinned(ids => ids.includes(selectedNodeId) ? ids.filter(id => id !== selectedNodeId) : [...ids, selectedNodeId])}><Pin size={16} /></button>}
    </div>
  </div>
}
