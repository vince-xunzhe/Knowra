import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { Search, RefreshCw, Play, ScanLine, Loader2, Filter, X } from 'lucide-react'
import KnowledgeGraph from '../components/KnowledgeGraph'
import NodeDetail from '../components/NodeDetail'
import {
  getGraph, scanPapers, processAll, searchNodes, getStatus,
  type GraphData, type GraphNode,
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
  { id: 'problem_area', label: '研究领域', color: '#06b6d4' },
  { id: 'finding', label: '发现', color: '#a855f7' },
]

export default function GraphPage() {
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], edges: [] })
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<GraphNode[]>([])
  const [typeFilter, setTypeFilter] = useState('all')
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [starting, setStarting] = useState(false)
  const [status, setStatus] = useState<ProcStatus | null>(null)
  const [scanResult, setScanResult] = useState<string | null>(null)
  const wasRunningRef = useRef(false)

  const loadGraph = useCallback(async () => {
    try {
      const data = await getGraph()
      setGraphData(data)
    } catch (error) {
      console.error('Failed to load graph', error)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    let cancelled = false
    const loadInitialGraph = async () => {
      try {
        const data = await getGraph()
        if (!cancelled) setGraphData(data)
      } catch (error) {
        console.error('Failed to load graph', error)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void loadInitialGraph()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const s = await getStatus()
        if (cancelled) return
        setStatus(s)
        if (wasRunningRef.current && !s.running) {
          loadGraph()
        }
        wasRunningRef.current = s.running
        if (s.running) setStarting(false)
      } catch (error) {
        console.error('Failed to poll processing status', error)
      }
    }
    void poll()
    const id = setInterval(poll, 1500)
    return () => { cancelled = true; clearInterval(id) }
  }, [loadGraph])

  const filteredData = useMemo(() => {
    if (typeFilter === 'all') {
      return graphData
    }

    const nodeIds = new Set(
      graphData.nodes.filter(n => n.node_type === typeFilter).map(n => n.id)
    )
    return {
      nodes: graphData.nodes.filter(n => nodeIds.has(n.id)),
      edges: graphData.edges.filter(e => nodeIds.has(e.source) && nodeIds.has(e.target)),
    }
  }, [graphData, typeFilter])

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
    try {
      await processAll()
    } catch {
      setStarting(false)
    }
  }

  const handleNodeNavigate = (nodeId: string) => {
    const node = graphData.nodes.find(n => n.id === nodeId)
    if (node) setSelectedNode(node)
  }

  const nodeTypeCounts = NODE_TYPE_FILTERS.reduce((acc, f) => {
    acc[f.id] = f.id === 'all'
      ? graphData.nodes.length
      : graphData.nodes.filter(n => n.node_type === f.id).length
    return acc
  }, {} as Record<string, number>)

  const progressPct = status?.running && status.total > 0
    ? Math.round((status.done / status.total) * 100)
    : 0
  const isEmpty = !loading && graphData.nodes.length === 0

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
              浏览论文、技术与发现之间的关联，点击节点可查看来源与上下文。
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
                    onClick={() => { setSelectedNode(n); setSearchQuery(''); setSearchResults([]) }}
                    className="w-full text-left px-3.5 py-3 hover:bg-slate-800 border-b border-slate-800 last:border-0 transition-colors"
                  >
                    <div className="text-sm text-slate-200 font-medium leading-snug line-clamp-2 text-safe-wrap">
                      {n.title}
                    </div>
                    <div className="text-xs text-slate-500 mt-1">{n.node_type}</div>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
            {scanResult && (
              <span className="max-w-sm rounded-xl border border-slate-800 bg-slate-900/50 px-3 py-2 text-xs text-slate-400 leading-relaxed text-safe-wrap">
                {scanResult}
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
              onClick={loadGraph}
              className="p-2 text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 rounded-xl transition-colors shrink-0"
              title="刷新图谱"
            >
              <RefreshCw size={14} />
            </button>
          </div>
        </div>
      </header>

      {/* Type filter chips */}
      <div className={`bg-[#0f1117]/60 border-b border-slate-800/60 px-6 py-2.5 flex flex-wrap items-center gap-2 transition-opacity ${isEmpty ? 'opacity-40' : ''}`}>
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
        />
      </div>
    </div>
  )
}

function getErrorMessage(error: unknown): string {
  const apiError = error as { response?: { data?: { detail?: string } }; message?: string }
  return apiError.response?.data?.detail || apiError.message || '未知错误'
}
