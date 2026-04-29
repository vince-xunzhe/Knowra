import { useEffect, useState, useRef, useMemo, useCallback } from 'react'
import {
  CheckCircle2, XCircle, Clock, RefreshCw, Play, FileText,
  RotateCw, Loader2, Search, LayoutGrid, List as ListIcon,
} from 'lucide-react'
import {
  listPapers, processAll, processPaper, retryPaper, retryFailedPapers, reprocessPaper, firstPageUrl, getStatus,
  type PaperRecord,
} from '../api/client'
import PromptPanel from '../components/PromptPanel'

interface ProcStatus {
  running: boolean
  total: number
  done: number
  errors: number
  current: string
}

type ViewMode = 'grid' | 'list'
type Filter = 'all' | 'processed' | 'pending' | 'failed'

export default function PapersPage() {
  const [papers, setPapers] = useState<PaperRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<PaperRecord | null>(null)
  const [pendingIds, setPendingIds] = useState<Set<number>>(new Set())
  const [status, setStatus] = useState<ProcStatus | null>(null)
  const [view, setView] = useState<ViewMode>('grid')
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [bulkProcessingPending, setBulkProcessingPending] = useState(false)
  const [bulkRetrying, setBulkRetrying] = useState(false)
  const wasRunningRef = useRef(false)

  const load = useCallback(async () => {
    try {
      const ps = await listPapers()
      setPapers(ps)
      setSelected(prev => prev ? ps.find(p => p.id === prev.id) || null : null)
    } catch (error) {
      console.error('Failed to load papers', error)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    let cancelled = false
    const loadInitialPapers = async () => {
      try {
        const ps = await listPapers()
        if (cancelled) return
        setPapers(ps)
        setSelected(prev => prev ? ps.find(p => p.id === prev.id) || null : null)
      } catch (error) {
        console.error('Failed to load papers', error)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void loadInitialPapers()
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
          setPendingIds(new Set())
          setActionMessage(s.errors > 0 ? `处理结束，${s.errors} 个失败。` : '处理完成。')
          load()
        } else if (s.running && s.current) {
          load()
        }
        wasRunningRef.current = s.running
      } catch (error) {
        console.error('Failed to poll processing status', error)
        setPendingIds(new Set())
        setActionMessage('无法获取处理状态: ' + getErrorMessage(error))
      }
    }
    void poll()
    const id = setInterval(poll, 1500)
    return () => { cancelled = true; clearInterval(id) }
  }, [load])

  const handleProcessOne = async (p: PaperRecord) => {
    setPendingIds(prev => new Set(prev).add(p.id))
    setActionMessage(null)
    try {
      await processPaper(p.id)
      setActionMessage(`已提交处理: ${p.filename}`)
    } catch (error) {
      setActionMessage('处理启动失败: ' + getErrorMessage(error))
    } finally {
      setPendingIds(prev => { const n = new Set(prev); n.delete(p.id); return n })
    }
  }

  const handleRetry = async (p: PaperRecord) => {
    setPendingIds(prev => new Set(prev).add(p.id))
    setActionMessage(null)
    try {
      await retryPaper(p.id)
      setActionMessage(`已提交重试: ${p.filename}`)
    } catch (error) {
      setActionMessage('重试启动失败: ' + getErrorMessage(error))
    } finally {
      setPendingIds(prev => { const n = new Set(prev); n.delete(p.id); return n })
    }
  }

  const handleReprocess = async (p: PaperRecord) => {
    const ok = confirm('确认重新处理这篇论文？现有抽取结果和图谱节点会被清空，并重新调用大模型。')
    if (!ok) return

    setPendingIds(prev => new Set(prev).add(p.id))
    setActionMessage(null)
    try {
      await reprocessPaper(p.id)
      setActionMessage(`已提交重新处理: ${p.filename}`)
    } catch (error) {
      setActionMessage('重新处理启动失败: ' + getErrorMessage(error))
    } finally {
      setPendingIds(prev => { const n = new Set(prev); n.delete(p.id); return n })
    }
  }

  const handleRetryFailedAll = async () => {
    const failedPapers = papers.filter(p => p.error && !p.processed)
    if (failedPapers.length === 0) {
      setActionMessage('当前没有失败论文需要重试。')
      return
    }

    const ok = confirm(
      `确认重试全部 ${failedPapers.length} 篇失败论文？这会清空它们当前的错误状态和 OpenAI 缓存，并重新调用大模型。`,
    )
    if (!ok) return

    const failedIds = failedPapers.map(p => p.id)
    setBulkRetrying(true)
    setPendingIds(prev => new Set([...prev, ...failedIds]))
    setActionMessage(null)
    try {
      const result = await retryFailedPapers()
      if ((result.retried || 0) > 0) {
        setActionMessage(`已提交批量重试：${result.retried} 篇失败论文`)
      } else {
        setPendingIds(prev => {
          const next = new Set(prev)
          failedIds.forEach(id => next.delete(id))
          return next
        })
        setActionMessage('当前没有失败论文需要重试。')
      }
    } catch (error) {
      setPendingIds(prev => {
        const next = new Set(prev)
        failedIds.forEach(id => next.delete(id))
        return next
      })
      setActionMessage('批量重试启动失败: ' + getErrorMessage(error))
    } finally {
      setBulkRetrying(false)
    }
  }

  const handleProcessPendingAll = async () => {
    const pendingPapers = papers.filter(p => !p.processed && !p.error)
    if (pendingPapers.length === 0) {
      setActionMessage('当前没有待处理论文需要重试。')
      return
    }

    const ok = confirm(`确认处理全部 ${pendingPapers.length} 篇待处理论文？`)
    if (!ok) return

    const pendingPaperIds = pendingPapers.map(p => p.id)
    setBulkProcessingPending(true)
    setPendingIds(prev => new Set([...prev, ...pendingPaperIds]))
    setActionMessage(null)
    try {
      await processAll()
      setActionMessage(`已提交批量处理：${pendingPapers.length} 篇待处理论文`)
    } catch (error) {
      setPendingIds(prev => {
        const next = new Set(prev)
        pendingPaperIds.forEach(id => next.delete(id))
        return next
      })
      setActionMessage('批量处理启动失败: ' + getErrorMessage(error))
    } finally {
      setBulkProcessingPending(false)
    }
  }

  const isPending = (p: PaperRecord) =>
    pendingIds.has(p.id) || !!(status?.running && status.current === p.filename)

  const stats = useMemo(() => ({
    processed: papers.filter(p => p.processed).length,
    failed: papers.filter(p => p.error && !p.processed).length,
    pending: papers.filter(p => !p.processed && !p.error).length,
  }), [papers])

  const filtered = useMemo(() => {
    let list = papers
    if (filter === 'processed') list = list.filter(p => p.processed)
    else if (filter === 'pending') list = list.filter(p => !p.processed && !p.error)
    else if (filter === 'failed') list = list.filter(p => p.error && !p.processed)
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(p =>
        p.filename.toLowerCase().includes(q) ||
        (p.title || '').toLowerCase().includes(q) ||
        p.authors.some(a => a.toLowerCase().includes(q))
      )
    }
    return list
  }, [papers, filter, search])

  return (
    <div className="flex h-full">
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* Toolbar */}
        <header className="bg-[#0f1117] border-b border-slate-800/80 px-6 py-4">
          <div className="flex flex-wrap items-start gap-4">
            <div className="min-w-0">
              <h1 className="text-xl font-semibold text-white tracking-tight">论文库</h1>
              <p className="text-sm text-slate-500 mt-1">
                浏览扫描到的论文，查看处理状态，并快速进入详情或重试失败项。
              </p>
              <p className="text-xs text-slate-500 mt-1.5">
                共 {papers.length} 篇
                <span className="text-emerald-400"> · {stats.processed} 已处理</span>
                {stats.pending > 0 && <span className="text-slate-400"> · {stats.pending} 待处理</span>}
                {stats.failed > 0 && <span className="text-red-400"> · {stats.failed} 失败</span>}
              </p>
            </div>

            <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
              {actionMessage && (
                <span className="max-w-sm rounded-xl border border-slate-800 bg-slate-900/50 px-3 py-2 text-xs text-slate-400 leading-relaxed text-safe-wrap">
                  {actionMessage}
                </span>
              )}
              <div className="relative">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                <input
                  type="text"
                  placeholder="搜索论文"
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  className="bg-slate-900/60 border border-slate-700/60 rounded-xl text-sm text-slate-200 pl-9 pr-3 py-2 w-64 focus:outline-none focus:border-indigo-500/60 transition-colors placeholder:text-slate-500"
                />
              </div>
              <div className="flex items-center bg-slate-900/60 border border-slate-700/60 rounded-xl p-0.5">
                <button
                  onClick={() => setView('grid')}
                  className={`p-1.5 rounded-md transition-colors ${view === 'grid' ? 'bg-slate-800 text-slate-200' : 'text-slate-500 hover:text-slate-300'}`}
                  title="网格视图"
                >
                  <LayoutGrid size={14} />
                </button>
                <button
                  onClick={() => setView('list')}
                  className={`p-1.5 rounded-md transition-colors ${view === 'list' ? 'bg-slate-800 text-slate-200' : 'text-slate-500 hover:text-slate-300'}`}
                  title="列表视图"
                >
                  <ListIcon size={14} />
                </button>
              </div>
              <button
                onClick={handleProcessPendingAll}
                disabled={bulkProcessingPending || !!status?.running || stats.pending === 0}
                className="inline-flex items-center gap-2 rounded-xl border border-sky-500/30 bg-sky-500/10 px-3 py-2 text-sm text-sky-200 transition-colors hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:border-slate-800 disabled:bg-slate-900/50 disabled:text-slate-500"
                title={stats.pending > 0 ? `处理全部 ${stats.pending} 篇待处理论文` : '当前没有待处理论文'}
              >
                {bulkProcessingPending ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                重试全部待处理
              </button>
              <button
                onClick={handleRetryFailedAll}
                disabled={bulkRetrying || !!status?.running || stats.failed === 0}
                className="inline-flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200 transition-colors hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:border-slate-800 disabled:bg-slate-900/50 disabled:text-slate-500"
                title={stats.failed > 0 ? `重试全部 ${stats.failed} 篇失败论文` : '当前没有失败论文'}
              >
                {bulkRetrying ? <Loader2 size={14} className="animate-spin" /> : <RotateCw size={14} />}
                重试全部失败
              </button>
              <button
                onClick={load}
                className="p-2 text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 rounded-xl transition-colors"
                title="刷新"
              >
                <RefreshCw size={14} />
              </button>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-1.5 text-xs">
            {([
              ['all', '全部', papers.length],
              ['processed', '已处理', stats.processed],
              ['pending', '待处理', stats.pending],
              ['failed', '失败', stats.failed],
            ] as [Filter, string, number][]).map(([key, label, n]) => (
              <button
                key={key}
                onClick={() => setFilter(key)}
                className={`px-3 py-1.5 rounded-lg border transition-colors ${
                  filter === key
                    ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500/20'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50 border-transparent'
                }`}
              >
                {label} <span className="text-slate-600 tabular-nums">{n}</span>
              </button>
            ))}
          </div>
        </header>

        {/* Selected-paper strip — sits between toolbar and the grid as a
            compact horizontal panel. Replaces the previous full-height
            right-column detail view; the right column is now dedicated
            to the Prompt editor. */}
        {selected && (
          <PaperDetailStrip
            paper={selected}
            pending={isPending(selected)}
            onClose={() => setSelected(null)}
            onProcess={() => handleProcessOne(selected)}
            onRetry={() => handleRetry(selected)}
            onReprocess={() => handleReprocess(selected)}
          />
        )}

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-6">
          {loading ? (
            <div className="text-slate-500 text-center py-24 text-sm">加载中…</div>
          ) : filtered.length === 0 ? (
            <div className="text-slate-500 text-center py-24">
              <FileText size={32} className="mx-auto text-slate-700 mb-3" />
              <p className="text-sm">{papers.length === 0 ? '还没有论文' : '没有匹配的论文'}</p>
              {papers.length === 0 && (
                <p className="text-xs mt-2 text-slate-600">
                  前往「图谱」页点击「扫描目录」
                </p>
              )}
            </div>
          ) : view === 'grid' ? (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
              {filtered.map(p => (
                <PaperGridCard
                  key={p.id}
                  paper={p}
                  active={selected?.id === p.id}
                  pending={isPending(p)}
                  onClick={() => setSelected(p)}
                />
              ))}
            </div>
          ) : (
            <div className="max-w-4xl space-y-2">
              {filtered.map(p => (
                <PaperListRow
                  key={p.id}
                  paper={p}
                  active={selected?.id === p.id}
                  pending={isPending(p)}
                  onClick={() => setSelected(p)}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Right column: dedicated to the global extraction Prompt editor.
          Per-paper detail used to share this column, but it now lives as
          a compact strip in the left column above the grid. */}
      <aside className="w-[24rem] max-w-[38vw] bg-[#0f1117] border-l border-slate-800/80 flex flex-col overflow-hidden shrink-0">
        <div className="px-5 py-4 border-b border-slate-800/80">
          <p className="section-label mb-1">全局 Prompt</p>
          <p className="text-sm text-slate-500 leading-relaxed">
            论文抽取使用的指令，所有论文共享。
          </p>
        </div>
        <PromptPanel />
      </aside>
    </div>
  )
}

// Compact horizontal strip shown between the toolbar and the paper grid
// when a paper is selected. Replaces the full-height right-column detail
// view; the right column is now Prompt-only.
function PaperDetailStrip({
  paper, pending, onClose, onProcess, onRetry, onReprocess,
}: {
  paper: PaperRecord
  pending: boolean
  onClose: () => void
  onProcess: () => void
  onRetry: () => void
  onReprocess: () => void
}) {
  return (
    <div className="bg-[#0f1117] border-b border-slate-800/80 px-6 py-3">
      <div className="flex items-start gap-4">
        {/* Thumbnail */}
        <div className="w-14 h-[4.5rem] shrink-0 bg-[#0b0d12] rounded border border-slate-800 flex items-center justify-center overflow-hidden">
          {paper.processed ? (
            <img
              src={firstPageUrl(paper.id)}
              alt=""
              className="max-w-full max-h-full object-contain"
              onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
          ) : (
            <FileText size={18} className="text-slate-700" />
          )}
        </div>

        {/* Info */}
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm text-white font-semibold leading-snug line-clamp-2 text-safe-wrap">
              {paper.title || paper.filename}
            </p>
            <button
              onClick={onClose}
              title="收起"
              className="shrink-0 text-slate-500 hover:text-white text-sm rounded-md px-1.5 py-0.5 hover:bg-slate-800/60 transition-colors"
            >
              ✕
            </button>
          </div>

          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
            <PaperStatus paper={paper} pending={pending} />
            {paper.authors.length > 0 && (
              <span className="line-clamp-1 max-w-[28rem] text-safe-wrap">
                {paper.authors.slice(0, 4).join(', ')}
                {paper.authors.length > 4 ? ' …' : ''}
              </span>
            )}
            {paper.num_pages && (
              <span className="tabular-nums text-slate-400">{paper.num_pages} 页</span>
            )}
            {paper.processed_at && (
              <span title={paper.processed_at}>
                于 {new Date(paper.processed_at).toLocaleString()} 处理
              </span>
            )}
            <span className="font-mono text-slate-600 break-all">{paper.filename}</span>
          </div>

          {paper.error && (
            <div className="mt-2 bg-red-500/10 border border-red-500/30 rounded-md px-2.5 py-1.5 text-[11px] text-red-300 break-words leading-relaxed">
              <span className="font-semibold">错误：</span>{paper.error}
            </div>
          )}

          {/* Actions */}
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {!paper.processed && !paper.error && (
              <button
                onClick={onProcess}
                disabled={pending}
                className="inline-flex items-center gap-1 text-xs bg-indigo-500 hover:bg-indigo-400 text-white px-2.5 py-1 rounded-md transition-colors disabled:bg-slate-700 disabled:text-slate-400"
              >
                {pending ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} 立即处理
              </button>
            )}
            {paper.error && (
              <button
                onClick={onRetry}
                disabled={pending}
                className="inline-flex items-center gap-1 text-xs bg-amber-500 hover:bg-amber-400 text-white px-2.5 py-1 rounded-md transition-colors disabled:bg-slate-700 disabled:text-slate-400"
              >
                {pending ? <Loader2 size={11} className="animate-spin" /> : <RotateCw size={11} />} 重试
              </button>
            )}
            {paper.processed && (
              <button
                onClick={onReprocess}
                disabled={pending}
                className="inline-flex items-center gap-1 text-xs bg-slate-800/80 hover:bg-slate-700 text-slate-200 border border-slate-700/60 px-2.5 py-1 rounded-md transition-colors disabled:opacity-40"
              >
                {pending ? <Loader2 size={11} className="animate-spin" /> : <RotateCw size={11} />} 重新处理
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function PaperGridCard({
  paper, active, pending, onClick,
}: { paper: PaperRecord; active: boolean; pending: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`text-left group bg-slate-900/40 rounded-2xl overflow-hidden border transition-all ${
        active
          ? 'border-indigo-500/60 shadow-lg shadow-indigo-500/10'
          : 'border-slate-800 hover:border-slate-700 hover:bg-slate-900/70'
      }`}
    >
      <div className="relative aspect-[3/4] bg-[#0b0d12] flex items-center justify-center overflow-hidden">
        {paper.processed ? (
          <img
            src={firstPageUrl(paper.id)}
            alt={paper.filename}
            className="max-w-full max-h-full object-contain"
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
        ) : (
          <FileText size={40} className="text-slate-700" />
        )}
        <div className="absolute top-2 right-2">
          <StatusDot pending={pending} paper={paper} />
        </div>
      </div>
      <div className="p-3.5">
        <p className="text-sm text-slate-200 font-medium leading-snug line-clamp-3 min-h-[4rem] group-hover:text-white transition-colors text-safe-wrap">
          {paper.title || paper.filename}
        </p>
        {paper.authors.length > 0 && (
          <p className="text-xs text-slate-500 line-clamp-2 leading-relaxed mt-1.5 text-safe-wrap min-h-[2.5rem]">
            {paper.authors.slice(0, 3).join(', ')}
          </p>
        )}
        <div className="flex items-center gap-2 text-xs text-slate-600 mt-2.5">
          {paper.num_pages && <span className="tabular-nums">{paper.num_pages} 页</span>}
        </div>
      </div>
    </button>
  )
}

function PaperListRow({
  paper, active, pending, onClick,
}: { paper: PaperRecord; active: boolean; pending: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-start gap-4 px-4 py-3.5 rounded-xl border text-left transition-all ${
        active
          ? 'bg-indigo-500/5 border-indigo-500/40'
          : 'bg-slate-900/40 border-slate-800 hover:border-slate-700 hover:bg-slate-900/70'
      }`}
    >
      <div className="w-10 h-12 shrink-0 bg-[#0b0d12] rounded flex items-center justify-center overflow-hidden">
        {paper.processed ? (
          <img
            src={firstPageUrl(paper.id)}
            alt=""
            className="max-w-full max-h-full object-contain"
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
        ) : (
          <FileText size={16} className="text-slate-700" />
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-slate-200 font-medium leading-snug line-clamp-2 text-safe-wrap">
          {paper.title || paper.filename}
        </p>
        <p className="text-xs text-slate-500 line-clamp-2 leading-relaxed mt-1 text-safe-wrap">
          {paper.authors.length > 0 ? paper.authors.slice(0, 3).join(', ') : paper.filename}
          {paper.num_pages && <span className="text-slate-600"> · {paper.num_pages}p</span>}
        </p>
      </div>
      <div className="shrink-0">
        <PaperStatus paper={paper} pending={pending} />
      </div>
    </button>
  )
}

function PaperStatus({ paper, pending }: { paper: PaperRecord; pending: boolean }) {
  if (pending) {
    return (
      <span className="chip bg-indigo-500/15 text-indigo-300 text-xs">
        <Loader2 size={11} className="animate-spin" /> 处理中
      </span>
    )
  }
  if (paper.processed) {
    return (
      <span className="chip bg-emerald-500/15 text-emerald-300 text-xs">
        <CheckCircle2 size={11} /> 已处理
      </span>
    )
  }
  if (paper.error) {
    return (
      <span className="chip bg-red-500/15 text-red-300 text-xs">
        <XCircle size={11} /> 失败
      </span>
    )
  }
  return (
    <span className="chip bg-slate-800 text-slate-400 text-xs">
      <Clock size={11} /> 待处理
    </span>
  )
}

function StatusDot({ paper, pending }: { paper: PaperRecord; pending: boolean }) {
  if (pending) return <Loader2 size={16} className="text-indigo-400 animate-spin drop-shadow" />
  if (paper.processed) return <CheckCircle2 size={16} className="text-emerald-400 drop-shadow" />
  if (paper.error) return <XCircle size={16} className="text-red-400 drop-shadow" />
  return <Clock size={16} className="text-slate-500 drop-shadow" />
}

function getErrorMessage(error: unknown): string {
  const apiError = error as { response?: { data?: { detail?: string } }; message?: string; code?: string }
  if (apiError.code === 'ECONNABORTED') return '请求超时，后端没有在 30 秒内响应。'
  return apiError.response?.data?.detail || apiError.message || '未知错误'
}
