import { useMemo, useState } from 'react'
import { ArrowLeft, Layers, Network, Route, X } from 'lucide-react'
import type { GraphData, GraphEdge, GraphNode, PaperRecord } from '../api/client'
import { t as tr } from '../i18n/catalog'
import { useLocale } from '../i18n/preferences'
import { byTitle, detailGraph, groupTopics, neighborhood, originLabels, relationLabels, shortestConnection, topicColor, topicId, topicOverview, topicSubgraph, typeLabels, visibleRelations, withPaperCategories, type ExplorerMode, type ViewEdge } from '../lib/graphExplorer'
import KnowledgeGraph from './KnowledgeGraph'
import './GraphExplorer.css'

interface Props { data: GraphData; papers: PaperRecord[]; selectedNodeId: string | null; onNodeClick: (node: GraphNode) => void; onClearSelection: () => void }
interface Navigation { mode: ExplorerMode; root: string; topic: string; source: string; target: string; depth: number; limit: number }
const initial: Navigation = { mode: 'overview', root: '', topic: '', source: '', target: '', depth: 1, limit: 80 }

export default function GraphExplorer({ data, papers, selectedNodeId, onNodeClick, onClearSelection }: Props) {
  const locale = useLocale()
  const [nav, setNav] = useState<Navigation>(() => ({ ...initial, mode: selectedNodeId ? 'local' : 'overview', root: selectedNodeId || '' }))
  const [history, setHistory] = useState<Navigation[]>([])
  const [similar, setSimilar] = useState(true)
  const [toolsOpen, setToolsOpen] = useState(false)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [bridge, setBridge] = useState<[string, string] | null>(null)
  const [seenSelection, setSeenSelection] = useState(selectedNodeId)
  // A search, rescue or candidate-review selection is an explicit focus request.
  // Internal path selections update seenSelection before opening node details.
  if (seenSelection !== selectedNodeId) {
    setSeenSelection(selectedNodeId)
    if (selectedNodeId) {
      setHistory(previous => [...previous.slice(-19), nav])
      setNav(previous => ({ ...previous, mode: 'local', root: selectedNodeId, topic: '', limit: 80 }))
      setSelectedEdgeId(null)
      setBridge(null)
    }
  }
  const categorized = useMemo(() => withPaperCategories(data, papers), [data, papers])
  const graph = useMemo(() => visibleRelations(categorized, similar), [categorized, similar])
  const topics = useMemo(() => groupTopics(categorized), [categorized])
  const nodeMap = useMemo(() => new Map(categorized.nodes.map(n => [n.id, n])), [categorized.nodes])
  const sortedNodes = useMemo(() => [...categorized.nodes].sort(byTitle), [categorized.nodes])
  const activeTopic = topics.find(t => t.id === nav.topic)
  const local = useMemo(() => activeTopic ? topicSubgraph(graph, activeTopic, nav.limit) : neighborhood(graph, nav.root, nav.depth, nav.limit), [graph, activeTopic, nav.root, nav.depth, nav.limit])
  const path = useMemo(() => shortestConnection(graph, nav.source, nav.target), [graph, nav.source, nav.target])
  const view = useMemo(() => nav.mode === 'overview' ? topicOverview(topics, graph, tr('未分类')) : nav.mode === 'local' ? detailGraph(local.data, nav.root) : detailGraph(path.data, undefined, path.order), [nav.mode, nav.root, topics, graph, local.data, path, locale]) // eslint-disable-line react-hooks/exhaustive-deps
  const displayedEdges = bridge ? graph.edges.filter(e => {
    const a = topicId(nodeMap.get(e.source)!), b = topicId(nodeMap.get(e.target)!)
    return (a === bridge[0] && b === bridge[1]) || (a === bridge[1] && b === bridge[0])
  }) : nav.mode === 'path' ? path.data.edges : nav.mode === 'local' ? local.data.edges : []
  const evidenceEdge = graph.edges.find(e => e.id === selectedEdgeId)
  const canvasData = nav.mode === 'overview' ? graph : nav.mode === 'local' ? local.data : path.data
  const layoutKey = nav.mode === 'overview' ? 'overview' : nav.mode === 'local' ? `local:${nav.topic || nav.root}:${nav.depth}` : `path:${nav.source}:${nav.target}:${path.order.join(',')}`

  function navigate(next: Navigation) {
    setHistory(previous => [...previous.slice(-19), nav]); setNav(next); setBridge(null); setSelectedEdgeId(null); onClearSelection()
  }
  function pickNode(id: string) {
    const node = nodeMap.get(id)
    if (!node) return
    setSeenSelection(id); setSelectedEdgeId(null)
    if (nav.mode === 'local') { setHistory(previous => [...previous.slice(-19), nav]); setNav(previous => ({ ...previous, mode: 'local', root: id, topic: '', limit: 80 })) }
    onNodeClick(node)
  }
  function pickEdge(edge: ViewEdge) {
    onClearSelection()
    if (edge.original) setSelectedEdgeId(edge.original.id)
    else { setBridge([edge.source, edge.target]); setSelectedEdgeId(null) }
  }
  const missingRoot = nav.mode === 'local' && !activeTopic && !nodeMap.has(nav.root)
  const missingEndpoint = nav.mode === 'path' && (!nodeMap.has(nav.source) || !nodeMap.has(nav.target))

  return <section className="graph-explorer" aria-label={tr('图谱探索')}>
    <div className="ge-tool-toggle">
      <button type="button" aria-expanded={toolsOpen} aria-controls="graph-explorer-tools" onClick={() => setToolsOpen(open => !open)}>{tr('图谱工具')} · {toolsOpen ? tr('收起') : tr('展开')}</button>
      <span>{nav.mode === 'overview' ? tr('节点图谱') : nav.mode === 'local' ? tr('局部探索') : tr('关系与证据')} · {tr('{count} 个节点', { count: canvasData.nodes.length })}</span>
      {nav.mode !== 'overview' && <button type="button" onClick={() => navigate({ ...initial })}>{tr('返回全部节点')}</button>}
    </div>
    <div id="graph-explorer-tools" hidden={!toolsOpen}>
    <div className="ge-toolbar">
      <div className="ge-modes" role="group" aria-label={tr('图谱视角')}>
        {([{ id: 'overview', label: '主题总览', icon: Layers }, { id: 'local', label: '局部探索', icon: Network }, { id: 'path', label: '关系与证据', icon: Route }] as const).map(({ id, label, icon: Icon }) => <button type="button" key={id} aria-pressed={nav.mode === id} onClick={() => navigate({ ...nav, mode: id, source: nav.source || selectedNodeId || nav.root, limit: 80 })}><Icon size={15} />{tr(label)}</button>)}
      </div>
      <label className="ge-check"><input type="checkbox" checked={similar} onChange={e => { setSimilar(e.target.checked); setSelectedEdgeId(null) }} />{tr('显示相似关系')}</label>
      <button type="button" className="ge-back" disabled={!history.length} onClick={() => { setNav(history[history.length - 1]); setHistory(history.slice(0, -1)); setBridge(null); setSelectedEdgeId(null); onClearSelection() }}><ArrowLeft size={14} />{tr('返回上一步')}</button>
    </div>
    <div className="ge-context">
      {nav.mode === 'overview' ? <p>{tr('画布保留原有节点图谱；可在主题目录中筛选，或切换到局部探索与关系证据。')}</p> : nav.mode === 'local' ? <>
        {activeTopic ? <div className="ge-topic-heading"><span style={{ background: topicColor(activeTopic.id) }} />{activeTopic.label || tr('未分类')}<button type="button" onClick={() => navigate({ ...nav, topic: '', root: '', limit: 80 })}>{tr('选择中心节点')}</button></div> : <>
          <NodePicker label={tr('中心节点')} value={nav.root} nodes={sortedNodes} onChange={pickNode} />
          <label className="ge-depth">{tr('邻居层数')}<select value={nav.depth} onChange={e => setNav({ ...nav, depth: Number(e.target.value), limit: 80 })}><option value="1">1</option><option value="2">2</option></select></label>
        </>}
      </> : <>
        <NodePicker label={tr('起点')} value={nav.source} nodes={sortedNodes} onChange={source => { setNav({ ...nav, source }); setSelectedEdgeId(null) }} />
        <NodePicker label={tr('终点')} value={nav.target} nodes={sortedNodes} onChange={target => { setNav({ ...nav, target }); setSelectedEdgeId(null) }} />
        <p>{tr('在当前筛选范围内查找最多 6 跳的连接；忽略遍历方向，保留边的原始方向，不代表因果。')}</p>
      </>}
    </div>
    <div className="ge-status" role="status">
      {tr('当前画布：{nodes} 个节点 · {edges} 条关系', { nodes: canvasData.nodes.length, edges: canvasData.edges.length })}
      {nav.mode === 'local' && local.total > local.data.nodes.length && <button type="button" onClick={() => setNav({ ...nav, limit: nav.limit + 80 })}>{tr('已显示 {shown}/{total}，再显示 80 个', { shown: local.data.nodes.length, total: local.total })}</button>}
    </div>
    </div>
    <div className="ge-workspace">
      <div className="ge-main">
        <div className="ge-graph-region">
          <div className="ge-classic-canvas"><KnowledgeGraph data={canvasData} viewKey={layoutKey} selectedNodeId={selectedNodeId} selectedEdgeId={selectedEdgeId} onNodeClick={node => pickNode(node.id)} onEdgeClick={edge => pickEdge({ id: edge.id, source: edge.source, target: edge.target, relation: edge.relation_type, original: edge })} /></div>
          {!canvasData.nodes.length && <div className="ge-empty" role="status">{missingRoot ? tr('选择一个节点，查看它的局部关系。') : missingEndpoint ? tr('选择起点和终点，查看连接路径。') : nav.mode === 'path' ? tr('当前筛选范围内，6 跳内未找到连接。可调整节点、类型或相似关系开关。') : tr('当前筛选范围内没有可展示的节点。')}</div>}
        </div>
        <details hidden={!toolsOpen} className="ge-index" open={nav.mode === 'overview'} key={nav.mode}>
          <summary>{nav.mode === 'overview' ? tr('主题目录') : tr('节点与关系列表')} · {view.nodes.length}</summary>
          <div className="ge-node-list">
            {view.nodes.map(n => <button type="button" key={n.id} onClick={() => nav.mode === 'overview' ? navigate({ ...nav, mode: 'local', topic: n.id, root: '', limit: 80 }) : pickNode(n.id)} title={n.title} aria-pressed={selectedNodeId === n.id}>
              <span className={`ge-mark ge-mark-${n.type}`} style={{ background: topicColor(n.topic) }} />
              <span>{n.title}</span><small>{n.count !== undefined ? tr('{count} 个节点', { count: n.count }) : tr(typeLabels[n.type] || n.type)}{n.status === 'pending' ? ` · ${tr('待评审')}` : n.status === 'rejected' ? ` · ${tr('已淘汰')}` : ''}</small>
            </button>)}
          </div>
          {nav.mode === 'overview' && view.edges.length > 0 && <details className="ge-topic-connections">
            <summary>{tr('跨主题关系')} · {view.edges.length}</summary>
            <div className="ge-relation-list">
              {view.edges.map(e => <button type="button" key={e.id} onClick={() => pickEdge(e)}>
                <span>{topics.find(t => t.id === e.source)?.label || tr('未分类')}</span>
                <small>{tr('{count} 条关系', { count: e.count })}{e.relation === 'similar' ? ` · ${tr('相似')}` : ''}</small>
                <span>{topics.find(t => t.id === e.target)?.label || tr('未分类')}</span>
              </button>)}
            </div>
          </details>}
          {nav.mode === 'overview' && <details className="ge-all-nodes"><summary>{tr('节点与关系列表')}</summary><NodePicker label={tr('选择节点')} value={selectedNodeId || ''} nodes={sortedNodes} onChange={pickNode} /><RelationList edges={graph.edges} nodes={nodeMap} selectedId={selectedEdgeId} onPick={edge => { setSelectedEdgeId(edge.id); onClearSelection() }} /></details>}
          {nav.mode !== 'overview' && <RelationList edges={displayedEdges} nodes={nodeMap} selectedId={selectedEdgeId} onPick={edge => { setSelectedEdgeId(edge.id); onClearSelection() }} />}
        </details>
        {nav.mode === 'local' && view.nodes.length === 1 && local.data.edges.length === 0 && <p className="ge-hint">{tr('这个节点在当前筛选范围内没有可见邻居；可打开相似关系或调整类型筛选。')}</p>}
        {nav.mode === 'path' && path.found && <div className="ge-path-steps"><RelationList edges={path.data.edges} nodes={nodeMap} selectedId={selectedEdgeId} onPick={edge => { setSelectedEdgeId(edge.id); onClearSelection() }} /></div>}
        {bridge && <div className="ge-bridge"><div className="ge-panel-heading"><h3>{tr('跨主题关系')}</h3><button type="button" onClick={() => setBridge(null)} aria-label={tr('关闭跨主题关系')}><X size={16} /></button></div><RelationList edges={displayedEdges} nodes={nodeMap} selectedId={selectedEdgeId} onPick={edge => { setSelectedEdgeId(edge.id); onClearSelection() }} /></div>}

      </div>
      {evidenceEdge && <EdgeEvidence edge={evidenceEdge} nodes={nodeMap} onClose={() => setSelectedEdgeId(null)} onPickNode={pickNode} />}
    </div>
  </section>
}

function NodePicker({ label, value, nodes, onChange }: { label: string; value: string; nodes: GraphNode[]; onChange: (id: string) => void }) {
  return <label className="ge-node-picker">{label}<select aria-label={label} value={nodes.some(n => n.id === value) ? value : ''} onChange={e => onChange(e.target.value)}><option value="" disabled>{tr('选择节点')}</option>{nodes.map(n => <option key={n.id} value={n.id}>{n.title} · {tr(typeLabels[n.node_type] || n.node_type)}</option>)}</select></label>
}
function RelationList({ edges, nodes, selectedId, onPick }: { edges: GraphEdge[]; nodes: Map<string, GraphNode>; selectedId: string | null; onPick: (edge: GraphEdge) => void }) {
  const [limit, setLimit] = useState(30)
  return <div className="ge-relation-list" aria-label={tr('关系列表')}>
    {edges.slice(0, limit).map(e => <button type="button" key={e.id} aria-pressed={selectedId === e.id} onClick={() => onPick(e)}><span>{nodes.get(e.source)?.title || e.source}</span><small>{tr(relationLabels[e.relation_type] || e.relation_type)} {e.relation_type === 'similar' ? '↔' : '→'}</small><span>{nodes.get(e.target)?.title || e.target}</span></button>)}
    {edges.length > limit && <button type="button" onClick={() => setLimit(limit + 30)}>{tr('更多关系（剩余 {count}）', { count: edges.length - limit })}</button>}
  </div>
}
function EdgeEvidence({ edge, nodes, onClose, onPickNode }: { edge: GraphEdge; nodes: Map<string, GraphNode>; onClose: () => void; onPickNode: (id: string) => void }) {
  const raw = edge.metadata?.provenance
  const contributions = Array.isArray(raw) ? raw.filter((item): item is Record<string, unknown> => !!item && typeof item === 'object' && !Array.isArray(item)) : []
  const entries: Record<string, unknown>[] = contributions.length ? contributions : [{ origin: edge.origin, evidence: edge.evidence, confidence: edge.confidence, source_paper_id: edge.source_paper_id, source_field: edge.source_field, extractor_version: edge.extractor_version }]
  return <aside className="ge-evidence" aria-label={tr('关系证据')}>
    <div className="ge-panel-heading"><h3>{tr('关系证据')}</h3><button type="button" onClick={onClose} aria-label={tr('关闭关系证据')}><X size={16} /></button></div>
    <button type="button" className="ge-endpoint" onClick={() => onPickNode(edge.source)}>{nodes.get(edge.source)?.title || edge.source}</button>
    <p className="ge-relation-name">{tr(relationLabels[edge.relation_type] || edge.relation_type)} {edge.relation_type === 'similar' ? '↔' : '→'}</p>
    <button type="button" className="ge-endpoint" onClick={() => onPickNode(edge.target)}>{nodes.get(edge.target)?.title || edge.target}</button>
    <p className="ge-hint">{tr('记录的关系与来源；推断或相似度不等同于已证实的事实。')}</p>
    {entries.map((entry, i) => {
      const origin = typeof entry.origin === 'string' ? tr(originLabels[entry.origin] || entry.origin) : tr('来源未记录')
      const evidence = typeof entry.evidence === 'string' ? entry.evidence : ''
      const paperId = entry.source_paper_id == null ? '' : String(entry.source_paper_id)
      const source = paperId ? [...nodes.values()].find(n => n.node_type === 'paper' && (String(n.paper_id) === paperId || n.source_paper_ids?.some(id => String(id) === paperId))) : undefined
      return <div className="ge-contribution" key={i}>
        <strong>{origin}</strong>
        {typeof entry.confidence === 'number' && Number.isFinite(entry.confidence) && <p>{tr('置信度')}：{entry.confidence.toFixed(3)}</p>}
        {edge.origin === 'embedding' && <p>{tr('相似度')}：{edge.weight.toFixed(3)}</p>}
        <p className="ge-evidence-text">{evidence || tr('未记录证据片段。')}</p>
        {paperId && (source ? <button type="button" className="ge-source" onClick={() => onPickNode(source.id)}>{tr('来源论文')}：{source.title}</button> : <p>{tr('来源论文')}：{paperId}</p>)}
        {typeof entry.source_field === 'string' && <p>{tr('来源字段')}：{entry.source_field}</p>}
        {typeof entry.extractor_version === 'string' && <p>{tr('提取版本')}：{entry.extractor_version}</p>}
      </div>
    })}
  </aside>
}
