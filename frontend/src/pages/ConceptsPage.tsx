import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  BookMarked, Loader2, RefreshCw, Search, FileText, Clock, Sparkles, Plus, X,
} from 'lucide-react'
import WikiKnowledgeMap from '../components/WikiKnowledgeMap'
import {
  createManualConcept,
  listConceptPages,
  getConceptPage,
  listPaperPages,
  getPaperPage,
  listHiddenGraphNodes,
  recompileConcept,
  recompileAllConcepts,
  recompileAllPaperPages,
  getWikiGraph,
  getWikiStatus,
  getWikiFreshness,
  restoreNode,
  searchWiki,
  suppressNode,
  type ManualConceptInput,
  type GraphNode,
  type WikiPageMeta,
  type WikiPageDetail,
  type WikiGraphData,
  type WikiGraphNode,
  type WikiKind,
  type WikiCompileState,
  type WikiFreshnessSummary,
  type WikiSearchHit,
} from '../api/client'

type WikiView = WikiKind | 'graph'

const HIDDEN_CATEGORY_STYLES: Record<string, string> = {
  LLM: 'border-slate-500/35 bg-slate-500/10 text-slate-100',
  VLM: 'border-indigo-500/35 bg-indigo-500/10 text-indigo-100',
  VLA: 'border-sky-500/35 bg-sky-500/10 text-sky-100',
  '三维重建-静态': 'border-amber-500/35 bg-amber-500/10 text-amber-100',
  '三维重建-动态': 'border-fuchsia-500/35 bg-fuchsia-500/10 text-fuchsia-100',
  '世界模型': 'border-violet-500/35 bg-violet-500/10 text-violet-100',
  其他: 'border-slate-700/80 bg-slate-900/80 text-slate-200',
}

function hiddenCategoryStyle(category?: string | null) {
  return HIDDEN_CATEGORY_STYLES[category || '其他'] || HIDDEN_CATEGORY_STYLES.其他
}

export default function ConceptsPage() {
  // The page hosts two kinds of wiki pages — paper (per-paper encyclopedia)
  // and concept (cross-paper synthesis). Each kind has its own list +
  // selection, but they share the right-hand detail panel and the global
  // compile status indicator.
  //
  // Default is 'papers' because that's what a fresh user has after running
  // the extraction pipeline — concept pages only appear once they've also
  // hit the "重编译概念" button, and an empty default tab feels broken.
  const [kind, setKind] = useState<WikiView>('papers')
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
  const [graph, setGraph] = useState<WikiGraphData | null>(null)
  const [graphLoading, setGraphLoading] = useState(true)
  const [hiddenNodes, setHiddenNodes] = useState<GraphNode[]>([])
  const [hiddenLoading, setHiddenLoading] = useState(true)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editorLane, setEditorLane] = useState<string | null>(null)
  const [editorSeedPaperIds, setEditorSeedPaperIds] = useState<number[]>([])
  const [graphActionBusyId, setGraphActionBusyId] = useState<string | null>(null)
  // Phase 2A — when `searchHits` is non-null, the left panel renders global
  // FTS5 results across both kinds instead of the per-kind list. Cleared
  // back to null when the search input is empty.
  const [searchHits, setSearchHits] = useState<WikiSearchHit[] | null>(null)
  const [searchLoading, setSearchLoading] = useState(false)
  const wasRunningRef = useRef(false)
  // Pending selection survives the kind-change → load → reset cycle so a
  // search hit click into a different tab actually selects the file once
  // the new list loads.
  const pendingSelectionRef = useRef<string | null>(null)

  const refreshFreshness = useCallback(async () => {
    try {
      setFreshness(await getWikiFreshness())
    } catch (error) {
      console.error('Failed to load wiki freshness', error)
    }
  }, [])

  const refreshGraph = useCallback(async () => {
    try {
      setGraph(await getWikiGraph())
    } catch (error) {
      console.error('Failed to load wiki graph', error)
    } finally {
      setGraphLoading(false)
    }
  }, [])

  const refreshHiddenNodes = useCallback(async () => {
    try {
      setHiddenNodes(await listHiddenGraphNodes())
    } catch (error) {
      console.error('Failed to load hidden graph nodes', error)
    } finally {
      setHiddenLoading(false)
    }
  }, [])

  const load = useCallback(async () => {
    try {
      if (kind === 'graph') {
        setItems([])
        setSelectedFilename(null)
        return
      }
      const next = kind === 'concepts' ? await listConceptPages() : await listPaperPages()
      setItems(next)
      // Pending selection from a search-hit click takes precedence over
      // both the previous selection and the default-to-first behavior.
      const pending = pendingSelectionRef.current
      pendingSelectionRef.current = null
      setSelectedFilename(prev => {
        if (pending && next.some(it => it.filename === pending)) return pending
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

  useEffect(() => {
    void refreshGraph()
  }, [refreshGraph])

  useEffect(() => {
    void refreshHiddenNodes()
  }, [refreshHiddenNodes])

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
        if (s.running) {
          void refreshGraph()
        }
        if (wasRunningRef.current && !s.running) {
          void load()
          void refreshFreshness()
          void refreshGraph()
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
  }, [load, refreshFreshness, refreshGraph])

  useEffect(() => {
    let cancelled = false
    if (kind === 'graph' || !selectedFilename) {
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

  // Local title-only filter (instant, no network). Used as a UX bridge while
  // the FTS5 query is in flight or for short queries (<2 chars) — the
  // global search needs trigrams so it can't do anything useful with 1 char.
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

  // Debounced FTS5 search across both kinds. Fires when query >= 2 chars;
  // cleared when input is empty. The 250ms debounce avoids a request on
  // every keystroke.
  useEffect(() => {
    const q = search.trim()
    if (q.length < 2) {
      setSearchHits(null)
      setSearchLoading(false)
      return
    }
    setSearchLoading(true)
    const t = setTimeout(async () => {
      try {
        const r = await searchWiki(q, 30)
        setSearchHits(r.hits)
      } catch (error) {
        console.error('wiki search failed', error)
        setSearchHits([])
      } finally {
        setSearchLoading(false)
      }
    }, 250)
    return () => clearTimeout(t)
  }, [search])

  const handleHitClick = (hit: WikiSearchHit) => {
    const targetKind: WikiKind = hit.kind === 'paper' ? 'papers' : 'concepts'
    if (targetKind !== kind) {
      // Stash the desired filename so `load` picks it up after the kind
      // change triggers a fresh list fetch.
      pendingSelectionRef.current = hit.filename
      setKind(targetKind)
    } else {
      setSelectedFilename(hit.filename)
    }
    setSearch('')
    setSearchHits(null)
  }

  const handleGraphPick = useCallback((node: WikiGraphNode) => {
    if (!node.filename || !node.page_kind) return
    const targetKind: WikiKind = node.page_kind
    if (targetKind !== kind) {
      pendingSelectionRef.current = node.filename
      setKind(targetKind)
    } else {
      setSelectedFilename(node.filename)
    }
  }, [kind])

  const graphPaperCatalog = useMemo(() => (
    (graph?.nodes || [])
      .filter((node): node is WikiGraphNode & { kind: 'paper'; paper_id: number } =>
        node.kind === 'paper' && node.paper_id != null,
      )
      .sort((a, b) => {
        if ((a.year || 0) !== (b.year || 0)) return (a.year || 0) - (b.year || 0)
        return (a.paper_id || 0) - (b.paper_id || 0)
      })
  ), [graph])

  const handleOpenGraphConceptEditor = useCallback((category?: string | null, paperIds?: number[]) => {
    setEditorLane(category || null)
    setEditorSeedPaperIds([...(paperIds || [])])
    setEditorOpen(true)
  }, [])

  const handleSaveGraphConcept = useCallback(async (payload: ManualConceptInput) => {
    setGraphActionBusyId('create')
    setActionMessage(null)
    try {
      await createManualConcept(payload)
      setEditorOpen(false)
      setEditorLane(null)
      setEditorSeedPaperIds([])
      setActionMessage('新概念已加入编译图谱。')
      await refreshGraph()
      void refreshHiddenNodes()
      void refreshFreshness()
    } catch (error) {
      setActionMessage('新增概念失败：' + getErrorMessage(error))
    } finally {
      setGraphActionBusyId(null)
    }
  }, [refreshFreshness, refreshGraph, refreshHiddenNodes])

  const handleSuppressGraphNode = useCallback(async (node: WikiGraphNode) => {
    if (node.concept_id == null) return
    const ok = confirm(`确认隐藏「${node.title}」？它会从当前编译图谱和后续概念编译里移除。`)
    if (!ok) return
    setGraphActionBusyId(node.id)
    setActionMessage(null)
    try {
      await suppressNode(node.concept_id)
      setActionMessage(`已隐藏概念：${node.title}`)
      await Promise.all([refreshGraph(), refreshHiddenNodes()])
      void refreshFreshness()
    } catch (error) {
      setActionMessage('隐藏概念失败：' + getErrorMessage(error))
    } finally {
      setGraphActionBusyId(null)
    }
  }, [refreshFreshness, refreshGraph, refreshHiddenNodes])

  const handleRestoreHiddenNode = useCallback(async (node: GraphNode) => {
    setGraphActionBusyId(node.id)
    setActionMessage(null)
    try {
      await restoreNode(Number(node.id))
      setActionMessage(`已恢复概念：${node.title}`)
      await refreshGraph()
      await refreshHiddenNodes()
      void refreshFreshness()
    } catch (error) {
      setActionMessage('恢复概念失败：' + getErrorMessage(error))
    } finally {
      setGraphActionBusyId(null)
    }
  }, [refreshFreshness, refreshGraph, refreshHiddenNodes])

  const selectedGraphId = useMemo(() => {
    if (!detail) return null
    if (detail.paper_id != null) return `paper:${detail.paper_id}`
    if (detail.concept_id != null) return `concept:${detail.concept_id}`
    return null
  }, [detail])

  // Sets of stale ids for O(1) per-row lookups during list render.
  const staleIds = useMemo(() => {
    if (!freshness || kind === 'graph') return new Set<number>()
    const bucket = kind === 'concepts' ? freshness.concepts.stale : freshness.papers.stale
    return new Set(bucket.map(it => kind === 'concepts'
      ? (it as { concept_id: number }).concept_id
      : (it as { paper_id: number }).paper_id))
  }, [freshness, kind])

  const currentBucket = kind === 'graph'
    ? null
    : kind === 'concepts'
      ? freshness?.concepts
      : freshness?.papers
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
    const ok = confirm('确认重新编译所有活跃概念页？系统会跳过输入未变化的页面，但对脏页面仍会逐个调用 LLM。')
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
              onClick={() => {
                void load()
                void refreshFreshness()
                void refreshGraph()
                void refreshHiddenNodes()
              }}
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
              { id: 'graph', label: '编译图谱' },
              { id: 'concepts', label: '概念页' },
            ] as { id: WikiView; label: string }[]).map(t => (
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
              placeholder="全 wiki 搜索 (≥2 字)"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl text-xs text-slate-200 pl-8 pr-7 py-1.5 focus:outline-none focus:border-indigo-500/60 transition-colors placeholder:text-slate-500"
            />
            {search && (
              <button
                onClick={() => setSearch('')}
                title="清空搜索"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-200 text-xs leading-none"
              >
                ✕
              </button>
            )}
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
              disabled={recompilingAll || !!status?.running}
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
            {kind === 'graph'
              ? `共 ${graph?.categories.length || 0} 个大类 · 时间轴视图`
              : `共 ${items.length} 篇 · 排序：最近编译`}
          </p>
          {kind !== 'graph' && needsAttention && !status?.running && (
            <FreshnessBanner
              kind={kind as WikiKind}
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
          {kind === 'graph' ? (
            <GraphSidebar
              graph={graph}
              loading={graphLoading}
              status={status}
            />
          ) : searchHits !== null ? (
            // Phase 2A: global FTS5 results take over the list area while
            // the search input is non-empty. Tab is ignored — hits across
            // both kinds render with a small kind badge.
            <SearchHitList
              hits={searchHits}
              loading={searchLoading}
              query={search}
              onPick={handleHitClick}
            />
          ) : loading ? (
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

        {kind === 'graph' ? (
          <WikiGraphPanel
            graph={graph}
            loading={graphLoading}
            status={status}
            selectedId={selectedGraphId}
            onPick={handleGraphPick}
            onSuppressNode={handleSuppressGraphNode}
            suppressingNodeId={graphActionBusyId}
            onOpenCreate={handleOpenGraphConceptEditor}
            hiddenNodes={hiddenNodes}
            hiddenLoading={hiddenLoading}
            onRestoreNode={handleRestoreHiddenNode}
          />
        ) : (
          <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
            {detailLoading && !detail ? (
              <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
                <Loader2 size={14} className="animate-spin mr-2" /> 加载中…
              </div>
            ) : !detail ? (
              <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
                {kind === 'concepts'
                  ? '从左侧列表或编译图谱选择一个概念页查看'
                  : '从左侧列表或编译图谱选择一篇论文页查看'}
              </div>
            ) : (
              <WikiDetailPanel
                kind={kind as WikiKind}
                detail={detail}
                recompiling={detail.concept_id != null && recompilingId === detail.concept_id}
                onRecompile={() => {
                  const meta = items.find(it => it.filename === detail.filename)
                  if (meta) void handleRecompileOne(meta)
                }}
              />
            )}
          </div>
        )}
      </div>

      <WikiConceptEditorModal
        open={editorOpen}
        busy={graphActionBusyId === 'create'}
        lane={editorLane}
        seedPaperIds={editorSeedPaperIds}
        papers={graphPaperCatalog}
        onClose={() => {
          setEditorOpen(false)
          setEditorLane(null)
          setEditorSeedPaperIds([])
        }}
        onSubmit={handleSaveGraphConcept}
      />
    </div>
  )
}

function WikiGraphPanel({
  graph,
  loading,
  status,
  selectedId,
  onPick,
  onSuppressNode,
  suppressingNodeId,
  onOpenCreate,
  hiddenNodes,
  hiddenLoading,
  onRestoreNode,
}: {
  graph: WikiGraphData | null
  loading: boolean
  status: WikiCompileState | null
  selectedId: string | null
  onPick: (node: WikiGraphNode) => void
  onSuppressNode: (node: WikiGraphNode) => void
  suppressingNodeId: string | null
  onOpenCreate: (category?: string | null, paperIds?: number[]) => void
  hiddenNodes: GraphNode[]
  hiddenLoading: boolean
  onRestoreNode: (node: GraphNode) => void
}) {
  const updatedLabel = graph?.updated_at ? new Date(graph.updated_at).toLocaleTimeString() : null
  const headline = status?.running
    ? `${status.kind === 'papers' ? '论文页' : status.kind === 'concepts' ? '概念页' : 'Wiki'} 编译进行中`
    : 'Wiki 知识图谱'

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-slate-800/80 px-6 py-4">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="section-label mb-1">编译图谱</p>
            <h2 className="text-base font-semibold tracking-tight text-white">{headline}</h2>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
              论文按技术大类分组，并沿时间轴串成链；数据集、技术和概念节点挂接到对应论文上。
            </p>
          </div>
          <div className="text-right text-[11px] text-slate-500">
            {updatedLabel ? <div>更新于 {updatedLabel}</div> : <div>尚未生成图谱</div>}
            {status?.running && status.current && (
              <div className="mt-1 text-indigo-300/90">{status.current}</div>
            )}
            <button
              onClick={() => onOpenCreate(null, [])}
              className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-teal-500/30 bg-teal-500/10 px-2.5 py-1 text-[11px] text-teal-200 transition-colors hover:bg-teal-500/20"
            >
              <Plus size={11} />
              新增概念
            </button>
          </div>
        </div>
        {graph && graph.categories.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {graph.categories.map(category => (
              <span
                key={category.name}
                className="rounded-lg border border-slate-800 bg-slate-900/70 px-2 py-1 text-[10.5px] text-slate-400"
              >
                {category.name} · {category.paper_count} 论文 · {category.concept_count} 节点
              </span>
            ))}
          </div>
        )}
      </header>

      <div className="min-h-0 flex-1">
        <div className="flex h-full flex-col overflow-hidden">
          <div className="min-h-0 flex-1">
            {loading ? (
              <div className="flex h-full items-center justify-center text-sm text-slate-500">
                <Loader2 size={14} className="mr-2 animate-spin" /> 正在载入图谱…
              </div>
            ) : !graph || graph.nodes.length === 0 ? (
              <div className="flex h-full items-center justify-center px-6 text-center text-sm text-slate-500">
                还没有可展示的 wiki 图谱。先编译论文页或概念页，图谱会实时出现在这里。
              </div>
            ) : (
              <WikiKnowledgeMap
                data={graph}
                selectedId={selectedId}
                onPick={onPick}
                onSuppressNode={onSuppressNode}
                suppressingNodeId={suppressingNodeId}
                onCreateConcept={onOpenCreate}
              />
            )}
          </div>
          <HiddenConceptsPanel
            nodes={hiddenNodes}
            loading={hiddenLoading}
            busyNodeId={suppressingNodeId}
            onRestoreNode={onRestoreNode}
          />
        </div>
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

function GraphSidebar({
  graph,
  loading,
  status,
}: {
  graph: WikiGraphData | null
  loading: boolean
  status: WikiCompileState | null
}) {
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-slate-500">
        <Loader2 size={14} className="mr-2 animate-spin" /> 载入图谱摘要…
      </div>
    )
  }

  return (
    <div className="space-y-3 px-2">
      <div className="rounded-xl border border-slate-800 bg-slate-900/50 px-3 py-3 text-[11px] leading-relaxed text-slate-400">
        <p className="text-slate-200 font-medium">编译图谱说明</p>
        <p className="mt-1">
          这里把已编译论文按技术大类分组，并沿年份串成时间链；每组下面的小框是挂接到这条论文链上的概念与相关节点。
        </p>
        {status?.running && status.current && (
          <p className="mt-2 text-indigo-300/90">
            正在更新：{status.current}
          </p>
        )}
      </div>

      {graph?.categories?.length ? (
        <ul className="space-y-2">
          {graph.categories.map(category => (
            <li
              key={category.name}
              className="rounded-xl border border-slate-800 bg-slate-900/35 px-3 py-2.5"
            >
              <p className="text-sm font-medium text-slate-100">{category.name}</p>
              <p className="mt-1 text-[11px] text-slate-500">
                {category.paper_count} 篇论文 · {category.concept_count} 个挂接节点
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <div className="rounded-xl border border-dashed border-slate-800 px-3 py-8 text-center text-xs text-slate-500">
          还没有可展示的大类
        </div>
      )}
    </div>
  )
}

function HiddenConceptsPanel({
  nodes,
  loading,
  busyNodeId,
  onRestoreNode,
}: {
  nodes: GraphNode[]
  loading: boolean
  busyNodeId: string | null
  onRestoreNode: (node: GraphNode) => void
}) {
  const sortedNodes = useMemo(() => (
    [...nodes].sort((aNode, bNode) => {
      const categoryCompare = (aNode.category || '其他').localeCompare(bNode.category || '其他', 'zh-Hans-CN')
      if (categoryCompare !== 0) return categoryCompare
      return aNode.title.localeCompare(bNode.title, 'zh-Hans-CN')
    })
  ), [nodes])

  return (
    <section className="shrink-0 border-t border-slate-800/80 bg-slate-950/92 px-5 py-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium text-slate-100">已隐藏概念</h3>
          <p className="mt-0.5 text-[10.5px] leading-relaxed text-slate-500">
            颜色代表所属大类，点右侧 `+` 恢复。
          </p>
        </div>
        <span className="rounded-lg border border-slate-800 bg-slate-900/60 px-2 py-1 text-[10.5px] text-slate-500">
          {nodes.length} 个
        </span>
      </div>

      {loading ? (
        <div className="mt-2 flex items-center text-xs text-slate-500">
          <Loader2 size={13} className="mr-2 animate-spin" /> 载入隐藏概念…
        </div>
      ) : sortedNodes.length === 0 ? (
        <div className="mt-2 rounded-xl border border-dashed border-slate-800 bg-slate-900/35 px-3 py-2.5 text-[11px] text-slate-500">
          暂时没有已隐藏的概念。
        </div>
      ) : (
        <div className="mt-2 overflow-x-auto pb-1">
          <div className="flex min-w-max items-center gap-1.5 pr-1">
          {sortedNodes.map(node => (
            <div
              key={node.id}
              className={`flex items-center gap-1 rounded-md border px-2 py-1 ${hiddenCategoryStyle(node.category)}`}
              title={`${node.title} · ${node.category || '其他'}`}
            >
              <div className="min-w-0">
                <p className="max-w-[9rem] truncate text-[11.5px] font-medium leading-4">
                  {node.title}
                </p>
              </div>
              <button
                onClick={() => onRestoreNode(node)}
                disabled={busyNodeId === node.id}
                className="shrink-0 rounded p-0.5 opacity-70 transition hover:bg-black/10 hover:opacity-100 disabled:opacity-50"
                title="恢复这个概念"
              >
                {busyNodeId === node.id ? <Loader2 size={10} className="animate-spin" /> : <Plus size={10} />}
              </button>
            </div>
          ))}
          </div>
        </div>
      )}
    </section>
  )
}

function WikiConceptEditorModal({
  open,
  busy,
  lane,
  seedPaperIds,
  papers,
  onClose,
  onSubmit,
}: {
  open: boolean
  busy: boolean
  lane: string | null
  seedPaperIds: number[]
  papers: (WikiGraphNode & { kind: 'paper'; paper_id: number })[]
  onClose: () => void
  onSubmit: (payload: ManualConceptInput) => Promise<void>
}) {
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [tagsText, setTagsText] = useState('')
  const [paperQuery, setPaperQuery] = useState('')
  const [selectedPaperIds, setSelectedPaperIds] = useState<number[]>([])

  useEffect(() => {
    if (!open) return
    setTitle('')
    setContent(lane ? `归入「${lane}」这条论文链的人工概念。` : '')
    setTagsText(lane && lane !== '其他' ? lane : '')
    setPaperQuery('')
    setSelectedPaperIds(seedPaperIds)
  }, [open, lane, seedPaperIds])

  if (!open) return null

  const filteredPapers = papers.filter(paper => {
    const q = paperQuery.trim().toLowerCase()
    if (!q) return true
    return paper.title.toLowerCase().includes(q)
      || (paper.filename || '').toLowerCase().includes(q)
      || String(paper.paper_id).includes(q)
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
      tags: tagsText.split(',').map(value => value.trim()).filter(Boolean),
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
            <h2 className="text-lg font-semibold text-white tracking-tight">新增概念</h2>
            <p className="mt-1 text-sm text-slate-500">
              手动概念会直接进入当前编译图谱，并在后续概念编译时使用你勾选的论文作为证据源。
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
              {lane && (
                <div className="rounded-xl border border-teal-500/30 bg-teal-500/10 px-3 py-2 text-xs text-teal-200">
                  默认挂到「{lane}」这条论文链
                </div>
              )}
              <div>
                <label className="mb-2 block text-sm text-slate-300">概念名称</label>
                <input
                  value={title}
                  onChange={e => setTitle(e.target.value)}
                  placeholder="例如：闭环驾驶世界模型 / 3D grounding"
                  className="w-full rounded-xl border border-slate-700/60 bg-slate-900/60 px-3 py-2.5 text-sm text-slate-100 focus:border-teal-500/60 focus:outline-none"
                />
              </div>
              <div>
                <label className="mb-2 block text-sm text-slate-300">概念简介</label>
                <textarea
                  value={content}
                  onChange={e => setContent(e.target.value)}
                  rows={8}
                  placeholder="写下你想保留的定义、边界或判断标准。"
                  className="w-full rounded-xl border border-slate-700/60 bg-slate-900/60 px-3 py-2.5 text-sm text-slate-100 focus:border-teal-500/60 focus:outline-none"
                />
              </div>
              <div>
                <label className="mb-2 block text-sm text-slate-300">标签</label>
                <input
                  value={tagsText}
                  onChange={e => setTagsText(e.target.value)}
                  placeholder="逗号分隔，例如 世界模型, 驾驶"
                  className="w-full rounded-xl border border-slate-700/60 bg-slate-900/60 px-3 py-2.5 text-sm text-slate-100 focus:border-teal-500/60 focus:outline-none"
                />
              </div>
            </div>
          </div>

          <div className="flex min-h-0 flex-col overflow-hidden px-6 py-5">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-slate-200">关联论文</p>
                <p className="text-xs text-slate-500">这些论文会成为后续概念编译的依据。</p>
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
                  const checked = selectedPaperIds.includes(paper.paper_id)
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
                          onChange={() => togglePaper(paper.paper_id)}
                          className="mt-0.5 h-4 w-4 rounded border-slate-700 bg-slate-900 text-teal-400"
                        />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm leading-snug text-slate-100 text-safe-wrap">
                            {paper.title}
                          </p>
                          <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
                            paper #{paper.paper_id} · {paper.category || '其他'}
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
            这里新增的是“人工概念层”，不会改动模型原始抽取结果。
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
              {busy ? '保存中…' : '创建概念'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function SearchHitList({
  hits, loading, query, onPick,
}: {
  hits: WikiSearchHit[]
  loading: boolean
  query: string
  onPick: (hit: WikiSearchHit) => void
}) {
  if (query.trim().length < 2) {
    return (
      <div className="text-center text-xs text-slate-500 py-10">
        <Search size={20} className="mx-auto text-slate-700 mb-2" />
        输入 2 字以上开始搜索 wiki
      </div>
    )
  }
  if (loading && hits.length === 0) {
    return (
      <div className="text-center text-xs text-slate-500 py-10">
        <Loader2 size={16} className="mx-auto animate-spin mb-2" /> 搜索中…
      </div>
    )
  }
  if (hits.length === 0) {
    return (
      <div className="text-center text-xs text-slate-500 py-10">
        <Search size={20} className="mx-auto text-slate-700 mb-2" />
        没有匹配
      </div>
    )
  }
  return (
    <ul className="space-y-1.5">
      <li className="px-2 py-1 text-[10.5px] text-slate-500">
        命中 {hits.length} 条
      </li>
      {hits.map(h => (
        <SearchHitRow key={`${h.kind}:${h.filename}`} hit={h} onClick={() => onPick(h)} />
      ))}
    </ul>
  )
}

function SearchHitRow({ hit, onClick }: { hit: WikiSearchHit; onClick: () => void }) {
  const kindLabel = hit.kind === 'paper' ? '论文' : '概念'
  const kindClass = hit.kind === 'paper'
    ? 'bg-blue-500/10 text-blue-300 border-blue-500/30'
    : 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30'
  return (
    <li>
      <button
        onClick={onClick}
        className="w-full text-left rounded-xl px-3 py-2.5 transition-colors border border-transparent hover:bg-slate-800/40 hover:border-slate-700"
      >
        <div className="flex items-start justify-between gap-2">
          <p className="text-sm font-medium leading-snug line-clamp-2 text-slate-100 text-safe-wrap">
            {hit.title}
          </p>
          <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded border ${kindClass}`}>
            {kindLabel}
          </span>
        </div>
        {/* Snippet contains <mark>...</mark> from FTS5; safe to render as
            HTML because the source comes from .md files we wrote ourselves
            and FTS5 itself emits the markers. */}
        <p
          className="mt-1.5 text-[11px] text-slate-400 leading-relaxed line-clamp-3 text-safe-wrap [&_mark]:bg-amber-300/30 [&_mark]:text-amber-100 [&_mark]:rounded [&_mark]:px-0.5"
          dangerouslySetInnerHTML={{ __html: hit.snippet }}
        />
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
