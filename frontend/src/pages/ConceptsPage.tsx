import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  BookMarked, Loader2, RefreshCw, Search, FileText, Clock, Sparkles,
} from 'lucide-react'
import {
  listConceptPages,
  getConceptPage,
  listPaperPages,
  getPaperPage,
  recompileConcept,
  recompileAllConcepts,
  recompileAllPaperPages,
  getWikiStatus,
  getWikiFreshness,
  type WikiPageMeta,
  type WikiPageDetail,
  type WikiKind,
  type WikiCompileState,
  type WikiFreshnessSummary,
} from '../api/client'

export default function ConceptsPage() {
  // The page hosts two kinds of wiki pages — paper (per-paper encyclopedia)
  // and concept (cross-paper synthesis). Each kind has its own list +
  // selection, but they share the right-hand detail panel and the global
  // compile status indicator.
  //
  // Default is 'papers' because that's what a fresh user has after running
  // the extraction pipeline — concept pages only appear once they've also
  // hit the "重编译概念" button, and an empty default tab feels broken.
  const [kind, setKind] = useState<WikiKind>('papers')
  const [items, setItems] = useState<WikiPageMeta[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedFilename, setSelectedFilename] = useState<string | null>(null)
  const [detail, setDetail] = useState<WikiPageDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [recompilingId, setRecompilingId] = useState<number | null>(null)
  const [recompilingAll, setRecompilingAll] = useState(false)
  const [recompilingPapers, setRecompilingPapers] = useState(false)
  const [status, setStatus] = useState<WikiCompileState | null>(null)
  const [freshness, setFreshness] = useState<WikiFreshnessSummary | null>(null)
  const wasRunningRef = useRef(false)

  const refreshFreshness = useCallback(async () => {
    try {
      setFreshness(await getWikiFreshness())
    } catch (error) {
      console.error('Failed to load wiki freshness', error)
    }
  }, [])

  const load = useCallback(async () => {
    try {
      const next = kind === 'concepts' ? await listConceptPages() : await listPaperPages()
      setItems(next)
      setSelectedFilename(prev => {
        if (prev && next.some(it => it.filename === prev)) return prev
        return next[0]?.filename ?? null
      })
    } catch (error) {
      console.error('Failed to load wiki pages', error)
      setActionMessage('加载列表失败：' + getErrorMessage(error))
    } finally {
      setLoading(false)
    }
  }, [kind])

  useEffect(() => {
    // Reset selection eagerly when switching kinds — otherwise the detail
    // effect briefly fetches the new kind with the old filename → 404.
    setSelectedFilename(null)
    setDetail(null)
    void load()
  }, [load])

  useEffect(() => {
    void refreshFreshness()
  }, [refreshFreshness])

  // Poll compile status. Backend exposes a single global lock, so this
  // covers both "全量重编译概念" and "编译论文页". When running flips back
  // to false, we refresh the list so newly-compiled pages show up.
  //
  // Backoff: when a long compile holds a uvicorn threadpool slot, status
  // polls occasionally race with in-flight OpenAI calls and time out at the
  // axios layer. We silently extend the next delay instead of error-logging
  // each transient timeout — the loop stays alive and the UI just lags by a
  // few seconds.
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let consecutiveErrors = 0

    const tick = async () => {
      let nextDelay = wasRunningRef.current ? 2000 : 5000
      try {
        const s = await getWikiStatus()
        if (cancelled) return
        consecutiveErrors = 0
        setStatus(s)
        if (wasRunningRef.current && !s.running) {
          void load()
          void refreshFreshness()
        }
        wasRunningRef.current = s.running
      } catch {
        consecutiveErrors += 1
        // Exponential-ish backoff capped at 10s so we don't spam the
        // backlog while uvicorn is busy with the compile task.
        nextDelay = Math.min(10000, 2000 * Math.max(1, consecutiveErrors))
      } finally {
        if (!cancelled) {
          timer = setTimeout(tick, nextDelay)
        }
      }
    }

    void tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [load, refreshFreshness])

  useEffect(() => {
    let cancelled = false
    if (!selectedFilename) {
      setDetail(null)
      return
    }
    setDetailLoading(true)
    const fetchDetail = kind === 'concepts'
      ? getConceptPage(selectedFilename)
      : getPaperPage(selectedFilename)
    fetchDetail
      .then(d => { if (!cancelled) setDetail(d) })
      .catch(err => {
        if (cancelled) return
        console.error('Failed to load wiki page detail', err)
        setActionMessage('打开页面失败：' + getErrorMessage(err))
        setDetail(null)
      })
      .finally(() => { if (!cancelled) setDetailLoading(false) })
    return () => { cancelled = true }
  }, [selectedFilename, kind])

  const filtered = useMemo(() => {
    if (!search.trim()) return items
    const q = search.toLowerCase()
    return items.filter(it =>
      it.title.toLowerCase().includes(q) ||
      (it.node_type || '').toLowerCase().includes(q) ||
      it.tags.some(t => t.toLowerCase().includes(q)) ||
      (it.authors || []).some(a => a.toLowerCase().includes(q))
    )
  }, [items, search])

  // Sets of stale ids for O(1) per-row lookups during list render.
  const staleIds = useMemo(() => {
    if (!freshness) return new Set<number>()
    const bucket = kind === 'concepts' ? freshness.concepts.stale : freshness.papers.stale
    return new Set(bucket.map(it => kind === 'concepts'
      ? (it as { concept_id: number }).concept_id
      : (it as { paper_id: number }).paper_id))
  }, [freshness, kind])

  const currentBucket = kind === 'concepts' ? freshness?.concepts : freshness?.papers
  const staleCount = currentBucket?.stale_count ?? 0
  const missingCount = currentBucket?.missing_count ?? 0
  const orphanCount = currentBucket?.orphan_count ?? 0
  const needsAttention = staleCount + missingCount + orphanCount > 0

  const handleRecompileOne = async (item: WikiPageMeta) => {
    if (item.concept_id == null) return
    setRecompilingId(item.concept_id)
    setActionMessage(null)
    try {
      await recompileConcept(item.concept_id)
      setActionMessage(`已重新编译：${item.title}`)
      await load()
      if (selectedFilename === item.filename) {
        const refreshed = await getConceptPage(item.filename)
        setDetail(refreshed)
      }
    } catch (error) {
      setActionMessage('重新编译失败：' + getErrorMessage(error))
    } finally {
      setRecompilingId(null)
    }
  }

  const handleRecompileAll = async () => {
    const ok = confirm('确认重新编译所有概念页？这会逐个调用 LLM，可能需要一段时间，并消耗 token。')
    if (!ok) return
    setRecompilingAll(true)
    setActionMessage(null)
    try {
      await recompileAllConcepts()
      setActionMessage('已在后台启动全量重编译。点击右上角刷新可查看进度。')
    } catch (error) {
      setActionMessage('启动失败：' + getErrorMessage(error))
    } finally {
      setRecompilingAll(false)
    }
  }

  const handleCompileAllPapers = async () => {
    const ok = confirm(
      '确认编译所有已处理论文的 wiki 页？每篇会调用一次 LLM，可能需要一段时间，并消耗 token。\n\n' +
      '生成位置：data/wiki/papers/。'
    )
    if (!ok) return
    setRecompilingPapers(true)
    setActionMessage(null)
    try {
      await recompileAllPaperPages()
      setActionMessage('已在后台启动论文页全量编译。完成后可在 data/wiki/papers/ 查看，或用 Obsidian 打开。')
    } catch (error) {
      setActionMessage('启动失败：' + getErrorMessage(error))
    } finally {
      setRecompilingPapers(false)
    }
  }

  return (
    <div className="flex h-full">
      {/* Left list */}
      <div className="w-[22rem] shrink-0 bg-[#0f1117] border-r border-slate-800/80 flex flex-col overflow-hidden">
        <header className="px-5 py-4 border-b border-slate-800/80">
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <h1 className="text-base font-semibold text-white tracking-tight flex items-center gap-2">
                <BookMarked size={16} className="text-indigo-400" /> Wiki
              </h1>
              <p className="text-[11px] text-slate-500 mt-1 leading-relaxed">
                由 LLM 编译自论文图谱，每篇 .md 都记录最近一次 compile 时间。
              </p>
            </div>
            <button
              onClick={load}
              className="p-2 text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 rounded-xl transition-colors shrink-0"
              title="刷新"
            >
              <RefreshCw size={14} />
            </button>
          </div>

          {/* Kind tabs */}
          <div className="mt-3 flex bg-slate-900/60 border border-slate-700/60 rounded-xl p-0.5">
            {([
              { id: 'papers', label: '论文页' },
              { id: 'concepts', label: '概念页' },
            ] as { id: WikiKind; label: string }[]).map(t => (
              <button
                key={t.id}
                onClick={() => setKind(t.id)}
                className={`flex-1 px-3 py-1.5 rounded-lg text-xs transition-colors ${
                  kind === t.id
                    ? 'bg-indigo-500/15 text-indigo-200'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div className="relative mt-2">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              placeholder={kind === 'concepts' ? '搜索 (标题 / 类型 / 标签)' : '搜索 (标题 / 作者 / 标签)'}
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl text-xs text-slate-200 pl-8 pr-3 py-1.5 focus:outline-none focus:border-indigo-500/60 transition-colors placeholder:text-slate-500"
            />
          </div>
          <div className="mt-2 grid grid-cols-2 gap-1.5">
            <button
              onClick={handleCompileAllPapers}
              disabled={recompilingPapers || status?.running}
              title="一次性为所有已处理论文生成 wiki 页 (data/wiki/papers/)"
              className="flex items-center justify-center gap-1.5 text-xs bg-slate-800/80 hover:bg-slate-700 text-slate-200 border border-slate-700/60 px-2 py-1.5 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {recompilingPapers || (status?.running && status.kind === 'papers')
                ? <Loader2 size={12} className="animate-spin" />
                : <FileText size={12} />}
              编译论文页
            </button>
            <button
              onClick={handleRecompileAll}
              disabled={recompilingAll || items.length === 0 || status?.running}
              title="重新编译所有概念页 (data/wiki/concepts/)"
              className="flex items-center justify-center gap-1.5 text-xs bg-slate-800/80 hover:bg-slate-700 text-slate-200 border border-slate-700/60 px-2 py-1.5 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {recompilingAll || (status?.running && status.kind === 'concepts')
                ? <Loader2 size={12} className="animate-spin" />
                : <Sparkles size={12} />}
              重编译概念
            </button>
          </div>
          <p className="mt-2 text-[10.5px] text-slate-600">
            共 {items.length} 篇 · 排序：最近编译
          </p>
          {needsAttention && !status?.running && (
            <FreshnessBanner
              kind={kind}
              stale={staleCount}
              missing={missingCount}
              orphan={orphanCount}
              recompileBusy={kind === 'concepts' ? recompilingAll : recompilingPapers}
              onRecompile={() => {
                if (kind === 'concepts') void handleRecompileAll()
                else void handleCompileAllPapers()
              }}
            />
          )}
          {status && <CompileStatusCard status={status} />}
        </header>

        <div className="flex-1 overflow-y-auto px-2 py-2">
          {loading ? (
            <div className="text-center text-xs text-slate-500 py-12">加载中…</div>
          ) : filtered.length === 0 ? (
            <div className="text-center text-xs text-slate-500 py-12">
              <FileText size={24} className="mx-auto text-slate-700 mb-2" />
              {items.length === 0
                ? kind === 'concepts'
                  ? '还没有概念页。点击「重编译概念」生成。'
                  : '还没有论文页。点击「编译论文页」生成。'
                : '没有匹配的页面。'}
            </div>
          ) : (
            <ul className="space-y-1">
              {filtered.map(it => {
                const itemId = kind === 'concepts' ? it.concept_id : it.paper_id
                const stale = itemId != null && staleIds.has(itemId)
                return (
                  <WikiListItem
                    key={it.filename}
                    kind={kind}
                    item={it}
                    stale={stale}
                    active={it.filename === selectedFilename}
                    onClick={() => setSelectedFilename(it.filename)}
                  />
                )
              })}
            </ul>
          )}
        </div>
      </div>

      {/* Right detail */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {actionMessage && (
          <div className="px-6 py-2 bg-slate-900/40 border-b border-slate-800 text-xs text-slate-400">
            {actionMessage}
          </div>
        )}

        {detailLoading && !detail ? (
          <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
            <Loader2 size={14} className="animate-spin mr-2" /> 加载中…
          </div>
        ) : !detail ? (
          <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
            {kind === 'concepts'
              ? '从左侧选择一个概念页查看'
              : '从左侧选择一篇论文页查看'}
          </div>
        ) : (
          <WikiDetailPanel
            kind={kind}
            detail={detail}
            recompiling={detail.concept_id != null && recompilingId === detail.concept_id}
            onRecompile={() => {
              const meta = items.find(it => it.filename === detail.filename)
              if (meta) void handleRecompileOne(meta)
            }}
          />
        )}
      </div>
    </div>
  )
}

function WikiListItem({
  kind, item, stale, active, onClick,
}: {
  kind: WikiKind
  item: WikiPageMeta
  stale: boolean
  active: boolean
  onClick: () => void
}) {
  const subtitle = kind === 'concepts'
    ? `${item.source_paper_ids.length} 篇`
    : (item.authors && item.authors.length > 0)
      ? item.authors.slice(0, 2).join(', ')
      : '论文'
  return (
    <li>
      <button
        onClick={onClick}
        className={`w-full text-left rounded-xl px-3 py-2.5 transition-colors border ${
          active
            ? 'bg-indigo-500/10 border-indigo-500/40 text-indigo-100'
            : 'bg-transparent border-transparent hover:bg-slate-800/40 text-slate-200'
        }`}
      >
        <div className="flex items-start justify-between gap-2">
          <p className="text-sm font-medium leading-snug line-clamp-2 text-safe-wrap flex items-start gap-1.5">
            {stale && (
              <span
                className="mt-1.5 shrink-0 w-1.5 h-1.5 rounded-full bg-amber-400"
                title="raw 数据已更新，此页待重编译"
              />
            )}
            <span className="min-w-0">{item.title}</span>
          </p>
          {kind === 'concepts' && item.node_type && (
            <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 uppercase tracking-wide">
              {item.node_type}
            </span>
          )}
          {kind === 'papers' && item.paper_id != null && (
            <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 font-mono tabular-nums">
              #{item.paper_id}
            </span>
          )}
        </div>
        <div className="mt-1.5 flex items-center gap-2 text-[10.5px] text-slate-500">
          <Clock size={10} />
          <span>{relTime(item.compiled_at)}</span>
          <span className="text-slate-700">·</span>
          <span className="line-clamp-1 text-safe-wrap">{subtitle}</span>
        </div>
      </button>
    </li>
  )
}

function FreshnessBanner({
  kind, stale, missing, orphan, recompileBusy, onRecompile,
}: {
  kind: WikiKind
  stale: number
  missing: number
  orphan: number
  recompileBusy: boolean
  onRecompile: () => void
}) {
  const kindLabel = kind === 'concepts' ? '概念页' : '论文页'
  const ctaLabel = kind === 'concepts' ? '重编译全部' : '编译全部'
  const parts: string[] = []
  if (missing > 0) parts.push(`${missing} 待编译`)
  if (stale > 0) parts.push(`${stale} 已过期`)
  if (orphan > 0) parts.push(`${orphan} 孤儿`)

  return (
    <div className="mt-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2.5 text-[11px] leading-relaxed">
      <div className="flex items-center gap-2 text-amber-200 font-medium">
        <span>⚠</span>
        <span>{kindLabel}与原始数据不一致</span>
      </div>
      <p className="mt-1 text-amber-100/80">
        {parts.join(' · ')}
      </p>
      <p className="mt-1 text-[10.5px] text-amber-200/60 leading-relaxed">
        {kind === 'concepts'
          ? '当 KnowledgeNode 引用的论文被重处理后，对应概念页就会标记为过期。'
          : '当论文被重新处理或新增后，对应论文页就会标记为待编译/过期。'}
      </p>
      <button
        onClick={onRecompile}
        disabled={recompileBusy}
        className="mt-2 w-full flex items-center justify-center gap-1.5 text-[11px] bg-amber-500/20 hover:bg-amber-500/30 text-amber-100 border border-amber-500/40 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {recompileBusy ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}
        {ctaLabel}
      </button>
    </div>
  )
}

function WikiDetailPanel({
  kind, detail, recompiling, onRecompile,
}: {
  kind: WikiKind
  detail: WikiPageDetail
  recompiling: boolean
  onRecompile: () => void
}) {
  const sectionLabel = kind === 'concepts' ? '概念条目' : '论文条目'
  return (
    <>
      <header className="px-6 py-4 border-b border-slate-800/80 flex items-start gap-4">
        <div className="min-w-0 flex-1">
          <p className="section-label mb-1">{sectionLabel}</p>
          <h2 className="text-lg text-white font-semibold leading-snug text-safe-wrap">
            {detail.title}
          </h2>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
            {kind === 'concepts' && detail.node_type && (
              <span className="text-slate-400">类型：{detail.node_type}</span>
            )}
            {kind === 'papers' && detail.paper_id != null && (
              <span className="text-slate-400 font-mono">paper #{detail.paper_id}</span>
            )}
            {kind === 'papers' && detail.authors && detail.authors.length > 0 && (
              <span className="text-slate-400 line-clamp-1 text-safe-wrap">
                {detail.authors.slice(0, 4).join(', ')}{detail.authors.length > 4 ? ' …' : ''}
              </span>
            )}
            <span title={detail.compiled_at ?? ''}>
              最近编译：{detail.compiled_at ? new Date(detail.compiled_at).toLocaleString() : '未知'}
              {detail.compiled_at && (
                <span className="text-slate-600"> · {relTime(detail.compiled_at)}</span>
              )}
            </span>
            {detail.compile_model && (
              <span className="text-slate-600">model: {detail.compile_model}</span>
            )}
            {kind === 'concepts' && (
              <span className="text-slate-600">{detail.source_paper_ids.length} 篇论文</span>
            )}
          </div>
          {detail.tags.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {detail.tags.map(t => (
                <span
                  key={t}
                  className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800/80 text-slate-400"
                >#{t}</span>
              ))}
            </div>
          )}
        </div>
        {kind === 'concepts' && (
          <button
            onClick={onRecompile}
            disabled={recompiling || detail.concept_id == null}
            className="shrink-0 flex items-center gap-1.5 text-xs bg-indigo-500/15 hover:bg-indigo-500/25 text-indigo-200 border border-indigo-500/30 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {recompiling ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
            重新编译
          </button>
        )}
        {kind === 'papers' && (
          <span
            className="shrink-0 text-[10.5px] text-slate-500 max-w-[12rem] leading-relaxed"
            title="单篇论文页随「论文 → 重新处理」自动重编译"
          >
            重编译此页：去「论文」点击「重新处理」
          </span>
        )}
      </header>

      {/* Where this wiki .md lives on disk. Project-relative for readability,
          plus a copy button for the absolute path so it's pasteable into
          Finder / Obsidian / any external tool. */}
      <PagePathStrip detail={detail} />

      <div className="flex-1 overflow-y-auto px-6 py-5">
        <div className="markdown-notes max-w-3xl text-sm leading-7 text-slate-200">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{detail.body}</ReactMarkdown>
        </div>

        <details className="mt-8 max-w-3xl text-xs text-slate-500">
          <summary className="cursor-pointer hover:text-slate-300">
            原始 .md（含 frontmatter） · {detail.path}
          </summary>
          <pre className="mt-3 p-3 bg-slate-900/60 border border-slate-800 rounded-lg overflow-x-auto whitespace-pre-wrap text-[11.5px] leading-5">
            {detail.raw}
          </pre>
        </details>
      </div>
    </>
  )
}

function PagePathStrip({ detail }: { detail: WikiPageDetail }) {
  const [copied, setCopied] = useState<'rel' | 'abs' | null>(null)
  const copy = async (kind: 'rel' | 'abs', value: string) => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(kind)
      setTimeout(() => setCopied(null), 1500)
    } catch {
      // Clipboard API can be blocked in non-secure contexts; fall back to
      // a transient hint so the user knows nothing happened.
      setCopied(null)
    }
  }
  return (
    <div className="px-6 py-2 bg-slate-900/30 border-b border-slate-800/80 text-[11px] text-slate-500 flex flex-wrap items-center gap-x-3 gap-y-1.5">
      <span className="text-slate-600">.md 路径</span>
      <code className="font-mono text-slate-300 bg-slate-900/60 border border-slate-800 rounded px-1.5 py-0.5 break-all">
        {detail.path}
      </code>
      <button
        onClick={() => copy('rel', detail.path)}
        className="text-slate-500 hover:text-slate-200 transition-colors text-[10.5px]"
        title="复制项目相对路径"
      >
        {copied === 'rel' ? '已复制 ✓' : '复制'}
      </button>
      <span className="text-slate-700">·</span>
      <button
        onClick={() => copy('abs', detail.disk_path)}
        className="text-slate-500 hover:text-slate-200 transition-colors text-[10.5px]"
        title={detail.disk_path}
      >
        {copied === 'abs' ? '已复制 ✓' : '复制绝对路径'}
      </button>
    </div>
  )
}

function CompileStatusCard({ status }: { status: WikiCompileState }) {
  // Keep the most recent finished run visible for context. Hide entirely if
  // we've never seen a job in this session.
  if (!status.running && !status.started_at) return null

  const total = Math.max(0, status.total)
  const done = Math.max(0, status.done)
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  const kindLabel = status.kind === 'papers' ? '论文页' : status.kind === 'concepts' ? '概念页' : 'wiki'

  const tone = status.running
    ? 'bg-indigo-500/10 border-indigo-500/30'
    : status.errors > 0
      ? 'bg-amber-500/10 border-amber-500/30'
      : 'bg-emerald-500/10 border-emerald-500/30'
  const barTone = status.running
    ? 'bg-indigo-400'
    : status.errors > 0
      ? 'bg-amber-400'
      : 'bg-emerald-400'

  return (
    <div className={`mt-3 rounded-xl border ${tone} px-3 py-2.5 text-[11px] leading-relaxed`}>
      <div className="flex items-center justify-between gap-2 text-slate-200">
        <span className="flex items-center gap-1.5 font-medium">
          {status.running && <Loader2 size={11} className="animate-spin" />}
          {status.running ? `正在编译${kindLabel}` : status.errors > 0 ? `${kindLabel}编译完成（有失败）` : `${kindLabel}编译完成`}
        </span>
        <span className="font-mono tabular-nums text-slate-400">{done}/{total || '?'}</span>
      </div>

      <div className="mt-1.5 h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
        <div
          className={`h-full ${barTone} transition-[width] duration-500 ease-out`}
          style={{ width: total > 0 ? `${pct}%` : status.running ? '40%' : '0%' }}
        />
      </div>

      {status.current && (
        <p className="mt-1.5 text-slate-400 line-clamp-2 text-safe-wrap">
          {status.current}
        </p>
      )}

      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[10.5px] text-slate-500">
        {status.model && <span>model · {status.model}</span>}
        {status.errors > 0 && <span className="text-amber-300">失败 {status.errors}</span>}
        {!status.running && status.finished_at && (
          <span>结束于 {new Date(status.finished_at).toLocaleTimeString()}</span>
        )}
      </div>

      {status.last_error && (
        <p className="mt-1.5 text-[10.5px] text-amber-300/90 break-words leading-relaxed">
          最近错误：{status.last_error}
        </p>
      )}
    </div>
  )
}

function relTime(iso: string | null): string {
  if (!iso) return '未编译'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return '未知'
  const diff = Date.now() - t
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return `${sec} 秒前`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} 分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 48) return `${hr} 小时前`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day} 天前`
  const mon = Math.floor(day / 30)
  if (mon < 12) return `${mon} 个月前`
  return `${Math.floor(mon / 12)} 年前`
}

function getErrorMessage(error: unknown): string {
  const apiError = error as {
    response?: { data?: { detail?: string } }
    message?: string
    code?: string
  }
  if (apiError.code === 'ECONNABORTED') return '请求超时'
  return apiError.response?.data?.detail || apiError.message || '未知错误'
}
