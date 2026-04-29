import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { Search, RefreshCw, Play, ScanLine, Loader2, Filter, X, Plus, Layers3 } from 'lucide-react'
import KnowledgeGraph from '../components/KnowledgeGraph'
import NodeDetail from '../components/NodeDetail'
import {
  createManualConcept,
  getGraph,
  getStatus,
  listPapers,
  processAll,
  scanPapers,
  searchNodes,
  suppressNode,
  updateManualConcept,
  type GraphData,
  type GraphNode,
  type ManualConceptInput,
  type PaperRecord,
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
  { id: 'concept', label: '概念', color: '#14b8a6' },
  { id: 'technique', label: '技术', color: '#22c55e' },
  { id: 'dataset', label: '数据集', color: '#f59e0b' },
  { id: 'problem_area', label: '研究领域', color: '#06b6d4' },
  { id: 'finding', label: '发现', color: '#a855f7' },
]

export default function GraphPage() {
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], edges: [] })
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<GraphNode[]>([])
  const [typeFilter, setTypeFilter] = useState('all')
  const [viewMode, setViewMode] = useState<'curated' | 'all'>('curated')
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

  const loadGraph = useCallback(async () => {
    try {
      const data = await getGraph()
      setGraphData(data)
      setSelectedNode(prev => {
        if (!prev) return prev
        return data.nodes.find(n => n.id === prev.id) || null
      })
    } catch (error) {
      console.error('Failed to load graph', error)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    let cancelled = false
    const loadInitial = async () => {
      try {
        const [data, papers] = await Promise.all([getGraph(), listPapers()])
        if (cancelled) return
        setGraphData(data)
        setPaperCatalog(papers)
      } catch (error) {
        console.error('Failed to load graph', error)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void loadInitial()
    return () => { cancelled = true }
  }, [])

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
    if (viewMode === 'all') return graphData
    const nodeIds = new Set(
      graphData.nodes
        .filter(n => n.node_type === 'paper' || n.publishable_concept)
        .map(n => n.id)
    )
    return {
      nodes: graphData.nodes.filter(n => nodeIds.has(n.id)),
      edges: graphData.edges.filter(e => nodeIds.has(e.source) && nodeIds.has(e.target)),
    }
  }, [graphData, viewMode])

  const filteredData = useMemo(() => {
    if (typeFilter === 'all') {
      return visibleData
    }

    const nodeIds = new Set(
      visibleData.nodes.filter(n => n.node_type === typeFilter).map(n => n.id)
    )
    return {
      nodes: visibleData.nodes.filter(n => nodeIds.has(n.id)),
      edges: visibleData.edges.filter(e => nodeIds.has(e.source) && nodeIds.has(e.target)),
    }
  }, [visibleData, typeFilter])

  const handleSearch = async (q: string) => {
    setSearchQuery(q)
    if (q.trim().length < 1) { setSearchResults([]); return }
    try {
      const results = await searchNodes(q)
      setSearchResults(results)
    } catch (error) {
      console.error('Failed to search nodes', error)
    }
  }

  const focusNode = useCallback((node: GraphNode) => {
    if (viewMode === 'curated' && node.node_type !== 'paper' && !node.publishable_concept) {
      setViewMode('all')
    }
    setSelectedNode(node)
  }, [viewMode])

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
      setViewMode('curated')
      setProcessResult(editingNode ? '概念已更新。' : '新概念已加入策展图。')
    } catch (error) {
      setProcessResult('概念保存失败: ' + getErrorMessage(error))
    } finally {
      setActionBusyId(null)
    }
  }

  const handleSuppressNode = async (node: GraphNode) => {
    const ok = confirm(`确认将「${node.title}」从概念层移除？`)
    if (!ok) return
    setActionBusyId(node.id)
    try {
      await suppressNode(Number(node.id))
      await loadGraph()
      if (selectedNode?.id === node.id) setSelectedNode(null)
      setProcessResult('节点已从概念层移除。')
    } catch (error) {
      setProcessResult('移除节点失败: ' + getErrorMessage(error))
    } finally {
      setActionBusyId(null)
    }
  }

  const nodeTypeCounts = NODE_TYPE_FILTERS.reduce((acc, f) => {
    acc[f.id] = f.id === 'all'
      ? visibleData.nodes.length
      : visibleData.nodes.filter(n => n.node_type === f.id).length
    return acc
  }, {} as Record<string, number>)

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
      {/* Toolbar */}
      <header className="bg-[#0f1117] border-b border-slate-800/80 px-6 py-4">
        <div className="flex flex-wrap items-start gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <h1 className="text-lg font-semibold text-white tracking-tight">知识图谱</h1>
              <span className="text-xs text-slate-500 tabular-nums">
                {filteredData.nodes.length} 节点 · {filteredData.edges.length} 边
              </span>
            </div>
            <p className="text-sm text-slate-500 mt-1">
              默认进入策展视图：聚焦论文与可发布概念，支持人工补概念、隐藏碎概念，再进入 wiki 编译。
            </p>
          </div>

          {/* Search */}
          <div className="relative min-w-[17rem] flex-1 max-w-md xl:ml-2">
            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 z-10 -translate-y-1/2 text-slate-500" />
            {!searchQuery && (
              <span className="pointer-events-none absolute left-9 top-1/2 z-10 -translate-y-1/2 text-sm leading-none text-slate-500">
                搜索知识节点
              </span>
            )}
            <input
              type="text"
              aria-label="搜索知识节点"
              value={searchQuery}
              onChange={e => handleSearch(e.target.value)}
              className="h-9 w-full rounded-xl border border-slate-700/60 bg-slate-900/60 pl-9 pr-9 text-sm leading-5 text-slate-200 transition-colors focus:border-indigo-500/60 focus:bg-slate-900 focus:outline-none"
            />
            {searchQuery && (
              <button
                onClick={() => { setSearchQuery(''); setSearchResults([]) }}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
              >
                <X size={12} />
              </button>
            )}
            {searchResults.length > 0 && (
              <div className="absolute top-full mt-2 left-0 w-full min-w-[18rem] bg-slate-900 border border-slate-700 rounded-xl shadow-2xl z-20 max-h-80 overflow-y-auto">
                {searchResults.map(n => (
                  <button
                    key={n.id}
                    onClick={() => { focusNode(n); setSearchQuery(''); setSearchResults([]) }}
                    className="w-full text-left px-3.5 py-3 hover:bg-slate-800 border-b border-slate-800 last:border-0 transition-colors"
                  >
                    <div className="text-sm text-slate-200 font-medium leading-snug line-clamp-2 text-safe-wrap">
                      {n.title}
                    </div>
                    <div className="text-xs text-slate-500 mt-1">
                      {n.origin === 'manual' ? '手动概念' : n.node_type}
                    </div>
                  </button>
                ))}
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
      <div className={`bg-[#0f1117]/60 border-b border-slate-800/60 px-6 py-2.5 flex flex-wrap items-center gap-2 transition-opacity ${isEmpty ? 'opacity-40' : ''}`}>
        <div className="inline-flex items-center rounded-xl border border-slate-800 bg-slate-900/60 p-1 mr-2">
          <button
            onClick={() => setViewMode('curated')}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors ${
              viewMode === 'curated'
                ? 'bg-slate-800 text-white'
                : 'text-slate-500 hover:text-slate-200'
            }`}
          >
            <Layers3 size={12} />
            策展图
          </button>
          <button
            onClick={() => setViewMode('all')}
            className={`rounded-lg px-3 py-1.5 text-xs transition-colors ${
              viewMode === 'all'
                ? 'bg-slate-800 text-white'
                : 'text-slate-500 hover:text-slate-200'
            }`}
          >
            全量图
          </button>
        </div>
        <div className="inline-flex items-center gap-2 pr-2">
          <Filter size={12} className="text-slate-500" />
          <span className="section-label">节点类型</span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {NODE_TYPE_FILTERS.map(f => {
            const active = typeFilter === f.id
            const count = nodeTypeCounts[f.id] || 0
            return (
              <button
                key={f.id}
                onClick={() => setTypeFilter(f.id)}
                className={`inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg transition-colors ${
                  active
                    ? 'bg-slate-800 text-white border border-slate-700/70'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 border border-transparent'
                }`}
              >
                {f.color && (
                  <span className="w-2 h-2 rounded-full" style={{ background: f.color }} />
                )}
                {f.label}
                <span className={`${active ? 'text-slate-400' : 'text-slate-600'} tabular-nums`}>{count}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Main canvas area */}
      <div className="flex-1 flex overflow-hidden">
        <div className="flex-1 min-w-0 relative bg-[#0b0d12]">
          {loading ? (
            <div className="flex items-center justify-center h-full text-slate-500 text-sm">
              加载中…
            </div>
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
          onClose={() => setSelectedNode(null)}
          onNavigate={handleNodeNavigate}
          onEditManualConcept={handleEditManualConcept}
          onSuppressNode={handleSuppressNode}
          busyNodeId={actionBusyId}
        />
      </div>

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
              新概念会进入策展图，并在后续概念编译时基于你勾选的论文生成 wiki 条目。
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
