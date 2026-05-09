import { useEffect, useRef, useCallback } from 'react'
import cytoscape from 'cytoscape'
import type { GraphData, GraphNode } from '../api/client'

const NODE_COLORS: Record<string, string> = {
  paper: '#6366f1',
  technique: '#22c55e',
  dataset: '#f59e0b',
  problem_area: '#06b6d4',
  concept: '#14b8a6',
  entity: '#ec4899',
  topic: '#6366f1',
  fact: '#f59e0b',
}

const NODE_LABELS: Record<string, string> = {
  paper: '论文',
  technique: '技术',
  dataset: '数据集',
  problem_area: '研究领域',
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

function compactLabel(title: string, maxChars: number) {
  const normalized = title.replace(/\s+/g, ' ').trim()
  if (normalized.length <= maxChars) return normalized
  return `${normalized.slice(0, maxChars - 1)}…`
}

function graphRepulsion(nodeCount: number) {
  if (nodeCount >= 90) return 32000
  if (nodeCount >= 50) return 28000
  if (nodeCount >= 24) return 24000
  return 20000
}

export default function KnowledgeGraph({ data, onNodeClick, selectedNodeId }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)

  const handleNodeClick = useCallback(
    (nodeId: string) => {
      const node = data.nodes.find(n => n.id === nodeId)
      if (node) onNodeClick(node)
    },
    [data.nodes, onNodeClick]
  )

  useEffect(() => {
    if (!containerRef.current) return

    const cy = cytoscape({
      container: containerRef.current,
      elements: [
        ...data.nodes.map(n => ({
          data: {
            id: n.id,
            label: compactLabel(n.title, n.node_type === 'paper' ? 28 : 18),
            fullTitle: n.title,
            node_type: n.node_type,
            // promotion_status drives the candidate-mode visual layer:
            // pending nodes get a dashed border + lower opacity so the eye
            // immediately separates "needs review" from "already decided".
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
            label: e.relation_type !== 'similar' ? e.relation_type : '',
            weight: e.weight,
          },
        })),
      ],
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
            width: 52,
            height: 52,
            'text-wrap': 'wrap',
            'text-max-width': '112px',
            'border-width': 2,
            'border-color': '#0b0d12',
            'text-outline-width': 0,
            'text-background-color': '#020617',
            'text-background-opacity': 0.92,
            'text-background-shape': 'roundrectangle',
            'text-background-padding': '5px',
            'overlay-opacity': 0,
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
          selector: 'edge',
          style: {
            width: 1.1,
            'line-color': '#334155',
            'line-opacity': 0.5,
            'target-arrow-color': '#475569',
            'target-arrow-shape': 'triangle',
            'arrow-scale': 0.75,
            'curve-style': 'bezier',
            label: 'data(label)',
            'font-size': '10px',
            color: '#94a3b8',
            'text-outline-width': 2,
            'text-outline-color': '#0b0d12',
            'text-background-opacity': 0,
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
      ],
      layout: {
        name: 'cose',
        animate: false,
        fit: true,
        nodeRepulsion: () => graphRepulsion(data.nodes.length),
        idealEdgeLength: edge => edge.data('relation_type') === 'similar' ? 210 : 175,
        edgeElasticity: edge => edge.data('relation_type') === 'similar' ? 65 : 130,
        nodeOverlap: 20,
        componentSpacing: 170,
        gravity: 0.16,
        numIter: 2000,
        padding: 90,
        nodeDimensionsIncludeLabels: true,
        randomize: false,
      } as cytoscape.LayoutOptions,
    })

    cy.on('tap', 'node', evt => {
      const nodeId = evt.target.id()
      handleNodeClick(nodeId)
    })

    cyRef.current = cy

    return () => {
      cy.destroy()
      cyRef.current = null
    }
  }, [data]) // eslint-disable-line react-hooks/exhaustive-deps

  // Highlight selected node
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.nodes().removeClass('highlighted')
    if (selectedNodeId) {
      cy.getElementById(selectedNodeId).addClass('highlighted')
    }
  }, [selectedNodeId])

  return (
    <div className="relative w-full h-full">
      <div ref={containerRef} className="w-full h-full" />
      {/* Legend — compact horizontal strip at bottom-right; the bottom-left
          area is reserved for the CandidatePanel floating card. */}
      <div className="absolute bottom-3 right-3 bg-slate-900/70 backdrop-blur rounded-lg px-2.5 py-1.5 border border-slate-800/80">
        <div className="flex items-center gap-3">
          {['paper', 'concept', 'technique', 'dataset', 'problem_area'].map(type => (
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
