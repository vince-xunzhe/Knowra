import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import {
  Search,
  RefreshCw,
  Play,
  ScanLine,
  Loader2,
  Filter,
  X,
  Plus,
  Sparkles,
} from 'lucide-react'
import KnowledgeGraph from '../components/KnowledgeGraph'
import NodeDetail from '../components/NodeDetail'
import RejectedRescueModal from '../components/RejectedRescueModal'
import CandidatePanel from '../components/CandidatePanel'
import PipelineStatusBar from '../components/PipelineStatusBar'
import WikiKnowledgeMap from '../components/WikiKnowledgeMap'
import ConceptListView from '../components/ConceptListView'
import AskDrawer from '../components/AskDrawer'
import {
  createManualConcept,
  getGraph,
  getStatus,
  getWikiGraph,
  listPapers,
  processAll,
  scanPapers,
  searchNodes,
  searchWiki,
  updateManualConcept,
  type GraphData,
  type GraphNode,
  type ManualConceptInput,
  type PaperRecord,
  type PromotionStatus,
  type WikiGraphData,
  type WikiGraphNode,
  type WikiSearchHit,
} from '../api/client'

interface ProcStatus {
  running: boolean
  total: number
  done: number
  errors: number
  current: string
}

const NODE_TYPE_FILTERS: { id: string; label: string; color: string }[] = [
  { id: 'all', label: '全部', color: '' },
  { id: 'paper', label: '论文', color: '#6366f1' },
  { id: 'technique', label: '技术', color: '#22c55e' },
  { id: 'dataset', label: '数据集', color: '#f59e0b' },
]

// Node types treated as "concepts" by the dedicated concept-list view.
// Subset of the backend's AUTO_CONCEPT_NODE_TYPES — problem_area is
// intentionally excluded so research-area nodes never surface in the
// node graph or the concept catalog.
const CONCEPT_ELIGIBLE_TYPES = new Set(['technique', 'dataset', 'concept'])

// Node types hidden from the structured node graph regardless of any
// filter. Stays in DB and rescue UI but never renders on the canvas.
const NODE_GRAPH_HIDDEN_TYPES = new Set(['problem_area'])

export default function GraphPage() {
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], edges: [] })
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<GraphNode[]>([])
  const [wikiHits, setWikiHits] = useState<WikiSearchHit[]>([])
  // Drawer initial tab — set when a wiki search hit is clicked so the
  // drawer opens directly on the rendered .md instead of the detail view.
  const [drawerInitialTab, setDrawerInitialTab] = useState<'detail' | 'wiki'>('detail')
  const [typeFilter, setTypeFilter] = useState('all')
  // Candidate mode is the single knob that controls graph composition.
  // It used to coexist with a `viewMode` (策展图 / 全量图) toggle, but the
  // two filters had overlapping semantics — `candidateMode='off'` already
  // means "only promoted concepts + papers", which is exactly what the
  // old curated view did. We removed the redundant toggle so the user
  // doesn't have to reason about two cascaded filters.
  //   off       — only promoted concepts + papers (curated view)
  //   pending   — also surface pending nodes (dashed amber border)
  //   all       — also surface rejected nodes (ghosted) for rescue
  const [candidateMode, setCandidateMode] = useState<'off' | 'pending' | 'all'>('off')
  const [rescueOpen, setRescueOpen] = useState(false)
  const [askOpen, setAskOpen] = useState(false)
  // Three flavors of view:
  //   - graph    : structured Cytoscape canvas (KnowledgeGraph)
  //   - compiled : compile-aware swim-lane (WikiKnowledgeMap)
  //   - concepts : flat list grouped by node_type — concepts as a curated
  //                catalog rather than nodes in a graph
  const [viewKind, setViewKind] = useState<'graph' | 'compiled' | 'concepts'>('graph')
  const [wikiGraph, setWikiGraph] = useState<WikiGraphData | null>(null)
  const [wikiGraphLoading, setWikiGraphLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [starting, setStarting] = useState(false)
  const [status, setStatus] = useState<ProcStatus | null>(null)
  const [scanResult, setScanResult] = useState<string | null>(null)
  const [processResult, setProcessResult] = useState<string | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [paperCatalog, setPaperCatalog] = useState<PaperRecord[]>([])
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingNode, setEditingNode] = useState<GraphNode | null>(null)
  const [actionBusyId, setActionBusyId] = useState<string | null>(null)
  const wasRunningRef = useRef(false)
  // Mirror of graphData kept in a ref so async callbacks (rescue → focus)
  // can read the freshest version without re-binding.
  const graphDataRef = useRef<GraphData | null>(null)

  const loadGraph = useCallback(async () => {
    try {
      const data = await getGraph(candidateMode !== 'off')
      setGraphData(data)
      graphDataRef.current = data
      setSelectedNode(prev => {
        if (!prev) return prev
        return data.nodes.find(n => n.id === prev.id) || null
      })
    } catch (error) {
      console.error('Failed to load graph', error)
    }
    setLoading(false)
  }, [candidateMode])

  useEffect(() => {
    let cancelled = false
    const loadInitial = async () => {
      try {
        const [data, papers] = await Promise.all([
          getGraph(candidateMode !== 'off'),
          listPapers(),
        ])
        if (cancelled) return
        setGraphData(data)
        graphDataRef.current = data
        setPaperCatalog(papers)
      } catch (error) {
        console.error('Failed to load graph', error)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void loadInitial()
    return () => { cancelled = true }
  }, [candidateMode])

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const s = await getStatus()
        if (cancelled) return
        setStatusError(null)
        setStatus(s)
        if (wasRunningRef.current && !s.running) {
          setProcessResult(s.errors > 0 ? `处理结束，${s.errors} 个失败。` : '处理完成。')
          loadGraph()
        }
        wasRunningRef.current = s.running
        if (s.running) setStarting(false)
      } catch (error) {
        console.error('Failed to poll processing status', error)
        if (!cancelled) {
          setStatusError('无法获取处理状态: ' + getErrorMessage(error))
          setStarting(false)
        }
      }
    }
    void poll()
    const id = setInterval(poll, 1500)
    return () => { cancelled = true; clearInterval(id) }
  }, [loadGraph])

  const visibleData = useMemo(() => {
    // First strip types we never want on the node-graph canvas (e.g.
    // problem_area), regardless of promotion state. Backend keeps these
    // rows so historical data isn't lost; the UI just doesn't render
    // them.
    const allowed = graphData.nodes.filter(
      n => !NODE_GRAPH_HIDDEN_TYPES.has(n.node_type),
    )
    let allowedIds = new Set(allowed.map(n => n.id))

    // Backend already filters by candidateMode (include_candidates flag);
    // the only frontend job left is the optional "hide rejected" pass
    // when the user picked +待评 instead of +已淘汰.
    if (candidateMode === 'pending') {
      allowedIds = new Set(
        allowed.filter(n => n.promotion_status !== 'rejected').map(n => n.id),
      )
    }
    return {
      nodes: allowed.filter(n => allowedIds.has(n.id)),
      edges: graphData.edges.filter(
        e => allowedIds.has(e.source) && allowedIds.has(e.target),
      ),
    }
  }, [graphData, candidateMode])

  const filteredData = useMemo(() => {
    if (typeFilter === 'all') {
      return visibleData
    }
    const nodeIds = new Set(
      visibleData.nodes.filter(n => n.node_type === typeFilter).map(n => n.id),
    )
    return {
      nodes: visibleData.nodes.filter(n => nodeIds.has(n.id)),
      edges: visibleData.edges.filter(
        e => nodeIds.has(e.source) && nodeIds.has(e.target),
      ),
    }
  }, [visibleData, typeFilter])

  const handleSearch = async (q: string) => {
    setSearchQuery(q)
    const trimmed = q.trim()
    if (trimmed.length < 1) {
      setSearchResults([])
      setWikiHits([])
      return
    }
    // Two parallel searches: structured node lookup (fast, by title/alias)
    // and wiki FTS (full-text over compiled .md). Wiki search needs ≥2
    // chars per backend tokenizer.
    try {
      const [nodes, wiki] = await Promise.all([
        searchNodes(trimmed),
        trimmed.length >= 2
          ? searchWiki(trimmed, 12).catch(() => ({ query: trimmed, hits: [] as WikiSearchHit[] }))
          : Promise.resolve({ query: trimmed, hits: [] as WikiSearchHit[] }),
      ])
      setSearchResults(nodes)
      setWikiHits(wiki.hits)
    } catch (error) {
      console.error('Failed to search', error)
    }
  }

  const focusNode = useCallback((node: GraphNode) => {
    // If focusing a node that's currently filtered out (a pending /
    // rejected concept while candidateMode='off'), widen the candidate
    // mode just enough to make it visible — otherwise the user clicks
    // the search hit and nothing happens on screen.
    if (candidateMode === 'off' && node.promotion_status && node.promotion_status !== 'promoted') {
      setCandidateMode(node.promotion_status === 'rejected' ? 'all' : 'pending')
    }
    setSelectedNode(node)
  }, [candidateMode])

  // Lazy-load the compiled-graph view; only fetched when the user toggles
  // into it, refreshed alongside the main graph after compile finishes.
  useEffect(() => {
    if (viewKind !== 'compiled') return
    let cancelled = false
    setWikiGraphLoading(true)
    getWikiGraph()
      .then(data => { if (!cancelled) setWikiGraph(data) })
      .catch(error => { console.error('Failed to load wiki graph', error) })
      .finally(() => { if (!cancelled) setWikiGraphLoading(false) })
    return () => { cancelled = true }
  }, [viewKind])

  // When the user clicks a node in the WikiKnowledgeMap, jump to the
  // matching DB graph node + open Wiki tab. Falls through silently if
  // the wiki graph references something the DB graph doesn't have
  // (orphan compile, manual concept stub etc.).
  const handleWikiGraphPick = useCallback(
    (wikiNode: WikiGraphNode) => {
      const targetId = wikiNode.paper_id ?? wikiNode.concept_id
      if (targetId == null) return
      const node = graphData.nodes.find(n => n.id === String(targetId))
      if (node) {
        setDrawerInitialTab('wiki')
        focusNode(node)
      }
    },
    [graphData.nodes, focusNode],
  )

  // Convert the leading `0042-...md` filename → graph node id. Wiki pages
  // never carry the DB id directly, but their slug always starts with the
  // zero-padded id, so this is the cheapest map.
  const handleWikiHitClick = useCallback(
    (hit: WikiSearchHit) => {
      const match = /^(\d+)-/.exec(hit.filename)
      if (!match) return
      const id = String(parseInt(match[1], 10))
      const targetNode = graphData.nodes.find(n => n.id === id)
      if (targetNode) {
        setDrawerInitialTab('wiki')
        focusNode(targetNode)
        setSearchQuery('')
        setSearchResults([])
        setWikiHits([])
      }
    },
    [graphData.nodes, focusNode],
  )

  const handleScan = async () => {
    setScanning(true)
    setScanResult(null)
    try {
      const result = await scanPapers()
      setScanResult(`发现 ${result.new_found} 篇新论文，共 ${result.total} 篇，待处理 ${result.unprocessed} 篇`)
    } catch (error) {
      setScanResult('扫描失败: ' + getErrorMessage(error))
    }
    setScanning(false)
  }

  const handleProcess = async () => {
    setStarting(true)
    setProcessResult(null)
    try {
      await processAll()
      setProcessResult('已提交处理任务，正在等待后端状态更新。')
    } catch (error) {
      setProcessResult('处理启动失败: ' + getErrorMessage(error))
    } finally {
      setStarting(false)
    }
  }

  const handleNodeNavigate = (nodeId: string) => {
    const node = graphData.nodes.find(n => n.id === nodeId)
    if (node) focusNode(node)
  }

  const handleOpenCreate = () => {
    setEditingNode(null)
    setEditorOpen(true)
  }

  const handleEditManualConcept = (node: GraphNode) => {
    setEditingNode(node)
    setEditorOpen(true)
  }

  const handleSaveManualConcept = async (payload: ManualConceptInput) => {
    setActionBusyId(editingNode?.id || 'create')
    try {
      const response = editingNode
        ? await updateManualConcept(Number(editingNode.id), payload)
        : await createManualConcept(payload)
      await loadGraph()
      focusNode(response.node)
      setEditorOpen(false)
      setEditingNode(null)
      // Manually-created concepts are auto-promoted, so they appear in
      // the default (candidateMode='off') view without further toggling.
      setProcessResult(editingNode ? '概念已更新。' : '新概念已加入图谱。')
    } catch (error) {
      setProcessResult('概念保存失败: ' + getErrorMessage(error))
    } finally {
      setActionBusyId(null)
    }
  }

  // Optimistically reflect a status change made in the drawer so the
  // Cytoscape style updates without a full reload (which would lose
  // viewport / selection state). The bulk reload still happens on next
  // explicit action.
  const handlePromotionChanged = useCallback(
    (nodeId: string, status: PromotionStatus) => {
      setGraphData(prev => ({
        ...prev,
        nodes: prev.nodes.map(n =>
          n.id === nodeId ? { ...n, promotion_status: status } : n,
        ),
      }))
    },
    [],
  )

  const nodeTypeCounts = NODE_TYPE_FILTERS.reduce((acc, f) => {
    acc[f.id] =
      f.id === 'all'
        ? visibleData.nodes.length
        : visibleData.nodes.filter(n => n.node_type === f.id).length
    return acc
  }, {} as Record<string, number>)

  // Count of concept-eligible nodes that would render in the concepts
  // view. Drives the disabled state of the 概念 toggle and any "暂无"
  // empty-state hint downstream. Uses visibleData so the button reflects
  // what the user would see right now (respects candidateMode).
  const conceptCount = visibleData.nodes.filter(n =>
    CONCEPT_ELIGIBLE_TYPES.has(n.node_type),
  ).length

  const progressPct = status?.running && status.total > 0
    ? Math.round((status.done / status.total) * 100)
    : 0
  const isEmpty = !loading && visibleData.nodes.length === 0

  return (
    <div className="flex flex-col h-full relative">
      {/* Top progress bar (visible while processing) */}
      {status?.running && (
        <div className="absolute top-0 left-0 right-0 h-[2px] bg-slate-800/60 z-30 overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-indigo-500 via-indigo-400 to-violet-400 transition-all duration-500"
            style={{ width: `${Math.max(progressPct, 4)}%` }}
          />
        </div>
      )}

      {/* Pipeline status bar — surfaces wiki-compile state above the graph
          so the user always sees how fresh the layer below the graph is. */}
      <PipelineStatusBar onCompileFinished={loadGraph} />

      {/* Toolbar */}
      <header className="bg-[#0f1117] border-b border-slate-800/80 px-6 py-2.5">
        <div className="flex flex-wrap items-start gap-4">
          <div className="min-w-0 flex items-baseline gap-2">
            <h1 className="text-base font-semibold text-white tracking-tight">知识图谱</h1>
            <span className="text-xs text-slate-500 tabular-nums">
              {filteredData.nodes.length} 节点 · {filteredData.edges.length} 边
            </span>
          </div>

          {/* Search */}
          <div className="relative min-w-[17rem] flex-1 max-w-md xl:ml-2">
            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 z-10 -translate-y-1/2 text-slate-500" />
            {!searchQuery && (
              <span className="pointer-events-none absolute left-9 top-1/2 z-10 -translate-y-1/2 text-sm leading-none text-slate-500">
                搜索节点 / wiki 全文
              </span>
            )}
            <input
              type="text"
              aria-label="搜索节点 / wiki 全文"
              value={searchQuery}
              onChange={e => handleSearch(e.target.value)}
              className="h-9 w-full rounded-xl border border-slate-700/60 bg-slate-900/60 pl-9 pr-9 text-sm leading-5 text-slate-200 transition-colors focus:border-indigo-500/60 focus:bg-slate-900 focus:outline-none"
            />
            {searchQuery && (
              <button
                onClick={() => {
                  setSearchQuery('')
                  setSearchResults([])
                  setWikiHits([])
                }}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
              >
                <X size={12} />
              </button>
            )}
            {(searchResults.length > 0 || wikiHits.length > 0) && (
              <div className="absolute top-full mt-2 left-0 w-full min-w-[20rem] bg-slate-900 border border-slate-700 rounded-xl shadow-2xl z-20 max-h-[28rem] overflow-y-auto">
                {searchResults.length > 0 && (
                  <div>
                    <div className="px-3.5 py-1.5 text-[10.5px] uppercase tracking-wider text-slate-500 bg-slate-950/60 border-b border-slate-800">
                      节点 · {searchResults.length}
                    </div>
                    {searchResults.map(n => (
                      <button
                        key={n.id}
                        onClick={() => {
                          setDrawerInitialTab('detail')
                          focusNode(n)
                          setSearchQuery('')
                          setSearchResults([])
                          setWikiHits([])
                        }}
                        className="w-full text-left px-3.5 py-2.5 hover:bg-slate-800 border-b border-slate-800 last:border-0 transition-colors"
                      >
                        <div className="text-sm text-slate-200 font-medium leading-snug line-clamp-2 text-safe-wrap">
                          {n.title}
                        </div>
                        <div className="text-[11px] text-slate-500 mt-1">
                          {n.origin === 'manual' ? '手动概念' : n.node_type}
                        </div>
                      </button>
                    ))}
                  </div>
                )}
                {wikiHits.length > 0 && (
                  <div>
                    <div className="px-3.5 py-1.5 text-[10.5px] uppercase tracking-wider text-slate-500 bg-slate-950/60 border-b border-slate-800 border-t border-slate-800">
                      Wiki 全文 · {wikiHits.length}
                    </div>
                    {wikiHits.map(hit => (
                      <button
                        key={`${hit.kind}:${hit.filename}`}
                        onClick={() => handleWikiHitClick(hit)}
                        className="w-full text-left px-3.5 py-2.5 hover:bg-slate-800 border-b border-slate-800 last:border-0 transition-colors"
                      >
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] px-1.5 py-0.5 rounded border border-slate-700 text-slate-400 shrink-0">
                            {hit.kind === 'paper' ? '论文页' : '概念页'}
                          </span>
                          <span className="text-sm text-slate-200 font-medium line-clamp-1 text-safe-wrap">
                            {hit.title}
                          </span>
                        </div>
                        {hit.snippet && (
                          <div
                            className="text-[11.5px] text-slate-500 mt-1 leading-snug line-clamp-2"
                            dangerouslySetInnerHTML={{ __html: hit.snippet }}
                          />
                        )}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
            {(scanResult || processResult || statusError) && (
              <span className="max-w-sm rounded-xl border border-slate-800 bg-slate-900/50 px-3 py-2 text-xs text-slate-400 leading-relaxed text-safe-wrap">
                {statusError || processResult || scanResult}
              </span>
            )}

            <button
              onClick={() => setAskOpen(true)}
              title="向知识库提问 — agent 会跨论文综合答复"
              className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-gradient-to-br from-indigo-500 to-violet-500 hover:from-indigo-400 hover:to-violet-400 px-3.5 py-2 rounded-xl transition-colors shrink-0 shadow-lg shadow-indigo-500/20"
            >
              <Sparkles size={14} />
              Ask
            </button>

            <button
              onClick={handleScan}
              disabled={scanning}
              className="inline-flex items-center gap-1.5 text-sm text-slate-300 hover:text-white bg-slate-800/60 hover:bg-slate-700/80 px-3.5 py-2 rounded-xl transition-colors disabled:opacity-50 shrink-0"
            >
              {scanning ? <Loader2 size={14} className="animate-spin" /> : <ScanLine size={14} />}
              {scanning ? '扫描中' : '扫描目录'}
            </button>

            <button
              onClick={handleProcess}
              disabled={starting || !!status?.running}
              title={status?.current || ''}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-indigo-500 hover:bg-indigo-400 px-3.5 py-2 rounded-xl transition-colors disabled:bg-slate-700 disabled:text-slate-400 disabled:cursor-not-allowed shrink-0"
            >
              {status?.running ? (
                <>
                  <Loader2 size={14} className="animate-spin" />
                  <span>处理中 <span className="tabular-nums">{status.done}/{status.total}</span></span>
                  {status.errors > 0 && <span className="text-red-200">· {status.errors} 失败</span>}
                </>
              ) : starting ? (
                <><Loader2 size={14} className="animate-spin" /> 启动中</>
              ) : (
                <><Play size={14} /> 处理论文</>
              )}
            </button>

            <button
              onClick={handleOpenCreate}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-teal-500 hover:bg-teal-400 px-3.5 py-2 rounded-xl transition-colors shrink-0"
            >
              <Plus size={14} />
              新增概念
            </button>

            <button
              onClick={loadGraph}
              className="p-2 text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 rounded-xl transition-colors shrink-0"
              title="刷新图谱"
            >
              <RefreshCw size={14} />
            </button>
          </div>
        </div>
      </header>

      {/* View + type filter chips */}
      <div className={`bg-[#0f1117]/60 border-b border-slate-800/60 px-6 py-1.5 flex flex-wrap items-center gap-2 transition-opacity ${isEmpty ? 'opacity-40' : ''}`}>
        {/* Outer toggle — picks which graph engine to use */}
        <div className="inline-flex items-center rounded-xl border border-slate-800 bg-slate-900/60 p-1 mr-2">
          <button
            onClick={() => setViewKind('graph')}
            title="基于 KnowledgeNode + KnowledgeEdge 的关系图"
            className={`rounded-lg px-3 py-1.5 text-xs transition-colors ${
              viewKind === 'graph'
                ? 'bg-slate-800 text-white'
                : 'text-slate-500 hover:text-slate-200'
            }`}
          >
            节点图谱
          </button>
          <button
            onClick={() => setViewKind('compiled')}
            title="基于已编译 wiki .md 的时间线视图"
            className={`rounded-lg px-3 py-1.5 text-xs transition-colors ${
              viewKind === 'compiled'
                ? 'bg-slate-800 text-white'
                : 'text-slate-500 hover:text-slate-200'
            }`}
          >
            编译图谱
          </button>
          <button
            onClick={() => setViewKind('concepts')}
            disabled={conceptCount === 0 && viewKind !== 'concepts'}
            title={
              conceptCount === 0
                ? '当前没有可展示的概念（先处理论文 / 调整候选模式）'
                : '按类目展开的概念目录（列表形式）'
            }
            className={`rounded-lg px-3 py-1.5 text-xs transition-colors disabled:cursor-not-allowed ${
              viewKind === 'concepts'
                ? 'bg-slate-800 text-white'
                : conceptCount === 0
                  ? 'text-slate-700'
                  : 'text-slate-500 hover:text-slate-200'
            }`}
          >
            概念
          </button>
        </div>

        {/* Node-type filter chips only apply to the structured node graph.
            Compiled mode has its own internal swim-lane grouping; concepts
            mode is already grouped by category in the list — both collapse
            the chip row to a small inline hint. */}
        {viewKind === 'graph' ? (
          <>
            <Filter size={12} className="text-slate-600" />
            <div className="flex flex-wrap gap-1">
              {NODE_TYPE_FILTERS.map(f => {
                const active = typeFilter === f.id
                const count = nodeTypeCounts[f.id] || 0
                return (
                  <button
                    key={f.id}
                    onClick={() => setTypeFilter(f.id)}
                    className={`inline-flex items-center gap-1 text-[11.5px] px-2 py-1 rounded-md transition-colors ${
                      active
                        ? 'bg-slate-800 text-white'
                        : 'text-slate-500 hover:text-slate-200 hover:bg-slate-800/40'
                    }`}
                  >
                    {f.color && (
                      <span className="w-1.5 h-1.5 rounded-full" style={{ background: f.color }} />
                    )}
                    {f.label}
                    <span className="tabular-nums text-slate-600">{count}</span>
                  </button>
                )
              })}
            </div>
          </>
        ) : (
          <span
            className="inline-flex items-center gap-1.5 text-[11px] text-slate-600"
            title={
              viewKind === 'compiled'
                ? '编译图谱按类目分泳道，无需类型筛选；切回节点图谱可展开'
                : '概念视图已按类目分组，无需类型筛选；切回节点图谱可展开'
            }
          >
            <Filter size={11} />
            类型筛选 — 仅节点图谱
          </span>
        )}
      </div>

      {/* Main canvas area */}
      <div className="flex-1 flex overflow-hidden">
        <div className="flex-1 min-w-0 relative bg-[#0b0d12]">
          {loading ? (
            <div className="flex items-center justify-center h-full text-slate-500 text-sm">
              加载中…
            </div>
          ) : viewKind === 'compiled' ? (
            // Compiled-graph view has its own data source (wiki .md → swim
            // lane). Branch first so the node-graph filter state can't make
            // it look "empty" when there are 70 compiled concept pages.
            wikiGraphLoading && !wikiGraph ? (
              <div className="flex h-full items-center justify-center text-sm text-slate-500">
                <Loader2 size={14} className="animate-spin mr-2" /> 加载编译图谱…
              </div>
            ) : wikiGraph && (wikiGraph.nodes?.length ?? 0) > 0 ? (
              <WikiKnowledgeMap
                data={wikiGraph}
                selectedId={selectedNode?.id || null}
                onPick={handleWikiGraphPick}
              />
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-slate-500">
                没有可用的编译图谱（先在管道里编译论文页 / 概念页）
              </div>
            )
          ) : viewKind === 'concepts' ? (
            <ConceptListView
              nodes={visibleData.nodes.filter(n => CONCEPT_ELIGIBLE_TYPES.has(n.node_type))}
              selectedId={selectedNode?.id || null}
              onPick={focusNode}
            />
          ) : filteredData.nodes.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-slate-500 gap-5 px-6 text-center">
              {/* SVG illustration: dotted grid with floating nodes */}
              <svg width="148" height="100" viewBox="0 0 148 100" fill="none" className="opacity-80">
                <defs>
                  <radialGradient id="nodeGrad" cx="50%" cy="50%" r="50%">
                    <stop offset="0%" stopColor="#818cf8" stopOpacity="0.9" />
                    <stop offset="100%" stopColor="#6366f1" stopOpacity="0.2" />
                  </radialGradient>
                </defs>
                {/* dotted grid */}
                {Array.from({ length: 6 }).map((_, r) =>
                  Array.from({ length: 9 }).map((_, c) => (
                    <circle key={`${r}-${c}`} cx={10 + c * 16} cy={10 + r * 16} r="0.9" fill="#334155" />
                  ))
                )}
                {/* edges */}
                <line x1="38" y1="38" x2="78" y2="62" stroke="#475569" strokeWidth="0.8" strokeDasharray="2 2" />
                <line x1="78" y1="62" x2="118" y2="34" stroke="#475569" strokeWidth="0.8" strokeDasharray="2 2" />
                <line x1="38" y1="38" x2="118" y2="34" stroke="#475569" strokeWidth="0.5" strokeDasharray="2 3" />
                {/* nodes */}
                <circle cx="38" cy="38" r="6" fill="url(#nodeGrad)" />
                <circle cx="78" cy="62" r="8" fill="url(#nodeGrad)" />
                <circle cx="118" cy="34" r="5" fill="url(#nodeGrad)" />
              </svg>
              <div className="space-y-1.5">
                <p className="text-base text-slate-300 font-medium">还没有知识节点</p>
                <p className="text-sm max-w-sm text-slate-500 leading-relaxed">
                  扫描你的论文目录，让大模型把每篇 PDF 转成结构化知识，自动生成图谱。
                </p>
              </div>
              <div className="flex flex-wrap items-center justify-center gap-2 mt-1">
                <button
                  onClick={handleScan}
                  disabled={scanning}
                  className="inline-flex items-center gap-2 text-sm font-medium text-white bg-indigo-500 hover:bg-indigo-400 px-4 py-2.5 rounded-xl transition-colors disabled:opacity-50 shadow-lg shadow-indigo-500/20"
                >
                  {scanning ? <Loader2 size={14} className="animate-spin" /> : <ScanLine size={14} />}
                  {scanning ? '扫描中…' : '扫描目录'}
                </button>
                <button
                  onClick={handleProcess}
                  disabled={starting || !!status?.running}
                  className="inline-flex items-center gap-2 text-sm text-slate-300 hover:text-white bg-slate-800/60 hover:bg-slate-700/80 px-4 py-2.5 rounded-xl transition-colors disabled:opacity-50"
                >
                  <Play size={14} />
                  开始处理
                </button>
              </div>
            </div>
          ) : (
            <KnowledgeGraph
              data={filteredData}
              onNodeClick={setSelectedNode}
              selectedNodeId={selectedNode?.id || null}
            />
          )}
        </div>

        <NodeDetail
          node={selectedNode}
          onClose={() => {
            setSelectedNode(null)
            setDrawerInitialTab('detail')
          }}
          onNavigate={handleNodeNavigate}
          onEditManualConcept={handleEditManualConcept}
          onPromotionChanged={handlePromotionChanged}
          busyNodeId={actionBusyId}
          initialTab={drawerInitialTab}
        />
      </div>

      <CandidatePanel
        candidateMode={candidateMode}
        onCandidateModeChange={setCandidateMode}
        onOpenRescue={() => setRescueOpen(true)}
        onPromotionRunFinished={loadGraph}
        viewKind={viewKind}
      />

      <RejectedRescueModal
        open={rescueOpen}
        onClose={() => setRescueOpen(false)}
        onRecalled={async (nodeId) => {
          await loadGraph()
          // Auto-jump to the rescued node so the user can see the context
          // they just rescued.
          const node = graphDataRef.current?.nodes.find(n => n.id === String(nodeId))
          if (node) {
            setCandidateMode('pending')
            setSelectedNode(node)
          }
        }}
      />

      <AskDrawer
        open={askOpen}
        onClose={() => setAskOpen(false)}
        onSynthesisCreated={async () => {
          // New synthesis concept is auto-promoted manual; reload the
          // graph so it shows up in 节点图谱 / 概念 immediately.
          await loadGraph()
        }}
      />

      <ConceptEditorModal
        open={editorOpen}
        busy={actionBusyId === (editingNode?.id || 'create')}
        papers={paperCatalog}
        initialNode={editingNode}
        onClose={() => {
          setEditorOpen(false)
          setEditingNode(null)
        }}
        onSubmit={handleSaveManualConcept}
      />
    </div>
  )
}

function ConceptEditorModal({
  open,
  busy,
  papers,
  initialNode,
  onClose,
  onSubmit,
}: {
  open: boolean
  busy: boolean
  papers: PaperRecord[]
  initialNode: GraphNode | null
  onClose: () => void
  onSubmit: (payload: ManualConceptInput) => Promise<void>
}) {
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [paperQuery, setPaperQuery] = useState('')
  const [tagsText, setTagsText] = useState('')
  const [selectedPaperIds, setSelectedPaperIds] = useState<number[]>([])

  useEffect(() => {
    if (!open) return
    setTitle(initialNode?.title || '')
    setContent(initialNode?.content || '')
    setTagsText((initialNode?.tags || []).join(', '))
    setSelectedPaperIds(initialNode?.source_paper_ids || [])
    setPaperQuery('')
  }, [open, initialNode])

  if (!open) return null

  const filteredPapers = papers.filter(paper => {
    const q = paperQuery.trim().toLowerCase()
    if (!q) return true
    return (paper.title || paper.filename).toLowerCase().includes(q)
      || paper.filename.toLowerCase().includes(q)
      || String(paper.id).includes(q)
  })

  const togglePaper = (paperId: number) => {
    setSelectedPaperIds(current => (
      current.includes(paperId)
        ? current.filter(id => id !== paperId)
        : [...current, paperId]
    ))
  }

  const submit = async () => {
    await onSubmit({
      title: title.trim(),
      content: content.trim(),
      paper_ids: selectedPaperIds,
      tags: tagsText.split(',').map(s => s.trim()).filter(Boolean),
    })
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/80 px-4 backdrop-blur-sm"
      onClick={e => {
        if (e.target === e.currentTarget && !busy) onClose()
      }}
    >
      <div className="flex h-[82vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-slate-800 bg-[#0f1117] shadow-2xl">
        <div className="flex items-start justify-between gap-4 border-b border-slate-800 px-6 py-4">
          <div>
            <h2 className="text-lg font-semibold text-white tracking-tight">
              {initialNode ? '编辑手动概念' : '新增手动概念'}
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              新概念会自动标为「精选」并加入图谱，后续概念编译会基于你勾选的论文生成 wiki 条目。
            </p>
          </div>
          <button
            onClick={onClose}
            disabled={busy}
            className="rounded-lg p-2 text-slate-500 hover:bg-slate-800/70 hover:text-slate-200 disabled:opacity-50"
          >
            <X size={16} />
          </button>
        </div>

        <div className="grid flex-1 gap-0 overflow-hidden lg:grid-cols-[minmax(0,0.95fr)_minmax(20rem,1.05fr)]">
          <div className="overflow-y-auto border-b border-slate-800 px-6 py-5 lg:border-b-0 lg:border-r">
            <div className="space-y-4">
              <div>
                <label className="mb-2 block text-sm text-slate-300">概念名称</label>
                <input
                  value={title}
                  onChange={e => setTitle(e.target.value)}
                  placeholder="例如：世界模型 / 闭环驾驶 / 3D grounding"
                  className="w-full rounded-xl border border-slate-700/60 bg-slate-900/60 px-3 py-2.5 text-sm text-slate-100 focus:border-teal-500/60 focus:outline-none"
                />
              </div>
              <div>
                <label className="mb-2 block text-sm text-slate-300">概念简介</label>
                <textarea
                  value={content}
                  onChange={e => setContent(e.target.value)}
                  rows={8}
                  placeholder="写下你对这个概念的定义、边界或想保留的先验知识。"
                  className="w-full rounded-xl border border-slate-700/60 bg-slate-900/60 px-3 py-2.5 text-sm text-slate-100 focus:border-teal-500/60 focus:outline-none"
                />
              </div>
              <div>
                <label className="mb-2 block text-sm text-slate-300">标签</label>
                <input
                  value={tagsText}
                  onChange={e => setTagsText(e.target.value)}
                  placeholder="逗号分隔，例如 规划, 世界模型, 驾驶"
                  className="w-full rounded-xl border border-slate-700/60 bg-slate-900/60 px-3 py-2.5 text-sm text-slate-100 focus:border-teal-500/60 focus:outline-none"
                />
              </div>
            </div>
          </div>

          <div className="flex min-h-0 flex-col overflow-hidden px-6 py-5">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-slate-200">关联论文</p>
                <p className="text-xs text-slate-500">这些论文会作为后续概念编译的证据来源。</p>
              </div>
              <span className="rounded-xl border border-slate-800 bg-slate-900/60 px-3 py-1.5 text-xs text-slate-400">
                已选 {selectedPaperIds.length}
              </span>
            </div>
            <input
              value={paperQuery}
              onChange={e => setPaperQuery(e.target.value)}
              placeholder="按标题 / 文件名 / paper id 搜索"
              className="mb-3 w-full rounded-xl border border-slate-700/60 bg-slate-900/60 px-3 py-2 text-sm text-slate-100 focus:border-teal-500/60 focus:outline-none"
            />
            <div className="min-h-0 flex-1 overflow-y-auto rounded-2xl border border-slate-800 bg-slate-950/40 p-2">
              <div className="space-y-2">
                {filteredPapers.map(paper => {
                  const checked = selectedPaperIds.includes(paper.id)
                  return (
                    <label
                      key={paper.id}
                      className={`block cursor-pointer rounded-xl border px-3 py-3 transition-colors ${
                        checked
                          ? 'border-teal-500/40 bg-teal-500/10'
                          : 'border-slate-800 bg-slate-900/40 hover:border-slate-700 hover:bg-slate-900/70'
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => togglePaper(paper.id)}
                          className="mt-0.5 h-4 w-4 rounded border-slate-700 bg-slate-900 text-teal-400"
                        />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm leading-snug text-slate-100 text-safe-wrap">
                            {paper.title || paper.filename}
                          </p>
                          <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
                            paper #{paper.id} · {paper.processed ? '已处理' : '未处理'} · {paper.filename}
                          </p>
                        </div>
                      </div>
                    </label>
                  )
                })}
                {filteredPapers.length === 0 && (
                  <div className="rounded-xl border border-dashed border-slate-800 px-4 py-8 text-center text-sm text-slate-500">
                    没有匹配的论文
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-slate-800 px-6 py-4">
          <p className="text-xs leading-relaxed text-slate-500">
            自动抽取出来的碎概念可以隐藏；手动新增的概念会稳定保留，并通过已选论文参与后续 wiki 编译。
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              disabled={busy}
              className="rounded-xl border border-slate-700 bg-slate-900/60 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              取消
            </button>
            <button
              onClick={submit}
              disabled={busy || !title.trim()}
              className="inline-flex items-center gap-2 rounded-xl bg-teal-500 px-4 py-2 text-sm font-medium text-white hover:bg-teal-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
            >
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              {busy ? '保存中…' : initialNode ? '保存概念' : '创建概念'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function getErrorMessage(error: unknown): string {
  const apiError = error as { response?: { data?: { detail?: string } }; message?: string; code?: string }
  if (apiError.code === 'ECONNABORTED') return '请求超时，后端没有在 30 秒内响应。'
  return apiError.response?.data?.detail || apiError.message || '未知错误'
}
