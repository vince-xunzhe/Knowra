import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  Sparkles, RefreshCw, Download, Check, Loader2, ExternalLink, Tag as TagIcon,
  Star, Wand2, Bookmark, BookmarkCheck, Search,
} from 'lucide-react'
import {
  cloudRecommendations, cloudRefreshRecommendations,
  cloudPushRecSummary, cloudRecMarks, cloudAddRecMark, cloudRemoveRecMark,
  type RecItem, type RecTag,
} from '../api/cloud'
import { downloadRecommendation, summarizeRecommendation, listPaperTeams } from '../api/client'
import { useCloudAuth } from '../hooks/useCloudAuth'

// Session caches (module-level so they survive the 推荐 tab unmounting /
// remounting). recCache holds the last feed; summaryCache holds per-paper summaries.
let recCache: { tags: RecTag[]; items: RecItem[] } | null = null
const summaryCache = new Map<string, string>()

type StatusFilter = 'all' | 'marked' | 'team' | 'downloadable'

const STATUS_FILTERS: { id: StatusFilter; label: string }[] = [
  { id: 'all', label: '全部' },
  { id: 'marked', label: '已收藏' },
  { id: 'team', label: '关注团队' },
  { id: 'downloadable', label: '可下载' },
]

interface DecoratedRecItem {
  it: RecItem
  matchedTeam: string | null
  marked: boolean
  downloadStatus?: string
  done: boolean
}

function apiErr(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail || x?.message || String(e)
}

// Normalize an author name for team matching — mirror backend _name_key.
const nameKey = (s: string) => (s || '').toLowerCase().replace(/[\s.\-_,]+/g, '')

// Concurrency-limited runner so switching selection quickly doesn't fire a burst
// of local-LLM summary calls at once.
function makeLimiter(max: number) {
  let active = 0
  const queue: (() => void)[] = []
  const pump = () => {
    if (active >= max) return
    const job = queue.shift()
    if (!job) return
    active += 1
    job()
  }
  return function run<T>(fn: () => Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      queue.push(() =>
        fn().then(resolve, reject).finally(() => {
          active -= 1
          pump()
        }),
      )
      pump()
    })
  }
}

export default function RecommendPage() {
  const auth = useCloudAuth()
  const [tags, setTags] = useState<RecTag[]>(() => recCache?.tags ?? [])
  const [items, setItems] = useState<RecItem[]>(() => recCache?.items ?? [])
  const [teamMap, setTeamMap] = useState<Map<string, string>>(new Map())
  const [loading, setLoading] = useState(() => !recCache)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // arxiv_id -> 'downloading' | 'downloaded' | 'duplicate'
  const [dl, setDl] = useState<Map<string, string>>(new Map())
  const [marks, setMarks] = useState<Set<string>>(new Set())
  const [revalidating, setRevalidating] = useState(() => !!recCache)
  const [selectedTag, setSelectedTag] = useState('all')
  const [selectedFilter, setSelectedFilter] = useState<StatusFilter>('all')
  const [selectedId, setSelectedId] = useState<string | null>(() => recCache?.items[0]?.id ?? null)
  const [query, setQuery] = useState('')
  const initedRef = useRef(!!recCache)
  const limiterRef = useRef(makeLimiter(3))

  const applyData = useCallback((data: { tags: RecTag[]; items: RecItem[] }) => {
    setTags(data.tags)
    setItems(data.items)
    recCache = { tags: data.tags, items: data.items }
    if (!initedRef.current) {
      initedRef.current = true
      setSelectedId(data.items[0]?.id ?? null)
    }
  }, [])

  const revalidate = useCallback(async () => {
    applyData(await cloudRecommendations(7))
  }, [applyData])

  useEffect(() => {
    if (!auth.user) {
      return
    }
    const hadCache = !!recCache
    cloudRecommendations(7)
      .then(data => applyData(data))
      .catch(e => {
        if (!hadCache) setError(apiErr(e))
      })
      .finally(() => {
        setLoading(false)
        setRevalidating(false)
      })
    // Local team registry -> normalized author -> team name, for highlighting
    // papers written by a team the user follows.
    void listPaperTeams()
      .then(res => {
        const m = new Map<string, string>()
        for (const t of res.teams) for (const a of t.authors || []) m.set(nameKey(a), t.name)
        setTeamMap(m)
      })
      .catch(() => {})
    void cloudRecMarks().then(ids => setMarks(new Set(ids))).catch(() => {})
  }, [auth.user, applyData])

  const toggleMark = useCallback((arxivId: string) => {
    setMarks(prev => {
      const next = new Set(prev)
      if (next.has(arxivId)) {
        next.delete(arxivId)
        cloudRemoveRecMark(arxivId).catch(() => {})
      } else {
        next.add(arxivId)
        cloudAddRecMark(arxivId).catch(() => {})
      }
      return next
    })
  }, [])

  const matchTeam = useCallback(
    (it: RecItem): string | null => {
      for (const a of it.authors || []) {
        const t = teamMap.get(nameKey(a))
        if (t) return t
      }
      return null
    },
    [teamMap],
  )

  const summarize = useCallback((it: RecItem): Promise<string> => {
    const hit = summaryCache.get(it.arxiv_id)
    if (hit) return Promise.resolve(hit)
    return limiterRef.current(() =>
      summarizeRecommendation({ arxiv_id: it.arxiv_id, title: it.title, abstract: it.abstract }).then(r => {
        summaryCache.set(it.arxiv_id, r.summary)
        // Push up so mobile (which runs no local model) shows the same summary.
        cloudPushRecSummary(it.arxiv_id, r.summary).catch(() => {})
        return r.summary
      }),
    )
  }, [])

  const refresh = async () => {
    setRefreshing(true)
    setError(null)
    try {
      await cloudRefreshRecommendations()
      await revalidate()
    } catch (e) {
      setError(apiErr(e))
    } finally {
      setRefreshing(false)
    }
  }

  const handleDownload = async (it: RecItem) => {
    setDl(prev => new Map(prev).set(it.arxiv_id, 'downloading'))
    try {
      const res = await downloadRecommendation({ arxiv_id: it.arxiv_id, pdf_url: it.pdf_url, title: it.title })
      setDl(prev => new Map(prev).set(it.arxiv_id, res.status))
    } catch (e) {
      setError(apiErr(e))
      setDl(prev => {
        const n = new Map(prev)
        n.delete(it.arxiv_id)
        return n
      })
    }
  }

  const decorated = useMemo<DecoratedRecItem[]>(() => items.map(it => {
    const downloadStatus = dl.get(it.arxiv_id)
    return {
      it,
      matchedTeam: matchTeam(it),
      marked: marks.has(it.arxiv_id),
      downloadStatus,
      done: downloadStatus === 'downloaded' || downloadStatus === 'duplicate',
    }
  }), [items, dl, marks, matchTeam])

  const tagCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const item of decorated) {
      counts.set(item.it.tag, (counts.get(item.it.tag) || 0) + 1)
    }
    return counts
  }, [decorated])

  const scoped = useMemo(
    () => decorated.filter(item => selectedTag === 'all' || item.it.tag === selectedTag),
    [decorated, selectedTag],
  )

  const filterCounts = useMemo(() => ({
    all: scoped.length,
    marked: scoped.filter(item => item.marked).length,
    team: scoped.filter(item => item.matchedTeam).length,
    downloadable: scoped.filter(item => item.it.pdf_url && !item.done).length,
  }), [scoped])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return scoped.filter(item => {
      if (selectedFilter === 'marked' && !item.marked) return false
      if (selectedFilter === 'team' && !item.matchedTeam) return false
      if (selectedFilter === 'downloadable' && (!item.it.pdf_url || item.done)) return false
      if (!q) return true
      return (
        item.it.title.toLowerCase().includes(q) ||
        item.it.arxiv_id.toLowerCase().includes(q) ||
        (item.it.primary_category || '').toLowerCase().includes(q) ||
        (item.it.authors || []).some(a => a.toLowerCase().includes(q))
      )
    })
  }, [scoped, selectedFilter, query])

  const selected = useMemo(
    () => filtered.find(item => item.it.id === selectedId) ?? filtered[0] ?? null,
    [filtered, selectedId],
  )

  if (!auth.user) {
    return (
      <CenteredNote
        title="请先登录云端"
        msg="推荐来自云端每周的 arXiv 检索。到 设置 -> 云同步 登录后即可查看。"
      />
    )
  }
  if (loading) return <CenteredNote title="加载中..." spinner />

  return (
    <div className="flex h-full overflow-hidden bg-[#0b0d12] text-slate-200">
      <FilterSidebar
        className="hidden lg:flex"
        tags={tags}
        tagCounts={tagCounts}
        total={decorated.length}
        selectedTag={selectedTag}
        onTag={setSelectedTag}
        selectedFilter={selectedFilter}
        onFilter={setSelectedFilter}
        filterCounts={filterCounts}
      />

      <section className="flex min-w-0 flex-1 flex-col">
        <header className="shrink-0 border-b border-slate-800/80 bg-[#0f1117] px-5 py-4">
          <div className="flex items-start gap-3">
            <Sparkles size={19} className="mt-1 shrink-0 text-indigo-300" />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-xl font-semibold tracking-tight text-white">推荐</h1>
                {revalidating && <Loader2 size={13} className="animate-spin text-slate-500" />}
                <span className="rounded-full border border-slate-700 px-2 py-0.5 text-[11px] text-slate-500">
                  {filtered.length} / {decorated.length}
                </span>
              </div>
              <p className="mt-1 text-sm text-slate-500">
                每周一 / 三 / 五自动检索 arXiv（保留近 7 天）。按方向筛选，中间快速扫读，右侧查看摘要和下载。
              </p>
            </div>
            <button
              onClick={refresh}
              disabled={refreshing}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-1.5 text-[12px] text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              {refreshing ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
              立即检索
            </button>
          </div>

          <div className="mt-3 flex gap-2 overflow-x-auto pb-1 lg:hidden">
            <FilterPill active={selectedTag === 'all'} label="全部方向" count={decorated.length} onClick={() => setSelectedTag('all')} />
            {tags.map(t => (
              <FilterPill
                key={t.name}
                active={selectedTag === t.name}
                label={t.name}
                count={tagCounts.get(t.name) || 0}
                onClick={() => setSelectedTag(t.name)}
              />
            ))}
          </div>
        </header>

        {error && (
          <div className="shrink-0 border-b border-rose-500/30 bg-rose-500/10 px-5 py-2 text-[12px] text-rose-200">
            {error}
          </div>
        )}

        <div className="flex min-h-0 flex-1 flex-col xl:flex-row">
          <main className="flex min-h-[18rem] min-w-0 flex-1 flex-col border-b border-slate-800/80 xl:border-b-0 xl:border-r">
            <div className="shrink-0 border-b border-slate-800/70 bg-[#0d1016] px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-[14rem] flex-1">
                  <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
                  <input
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                    placeholder="搜索标题、作者、arXiv ID"
                    className="w-full rounded-lg border border-slate-800 bg-slate-950/60 py-1.5 pl-8 pr-3 text-[12px] text-slate-200 outline-none placeholder:text-slate-600 focus:border-indigo-500/60"
                  />
                </div>
                <div className="flex flex-wrap gap-1">
                  {STATUS_FILTERS.map(f => (
                    <button
                      key={f.id}
                      onClick={() => setSelectedFilter(f.id)}
                      className={`rounded-lg border px-2.5 py-1 text-[11px] transition-colors ${
                        selectedFilter === f.id
                          ? 'border-indigo-500/40 bg-indigo-500/15 text-indigo-100'
                          : 'border-slate-800 text-slate-500 hover:text-slate-300'
                      }`}
                    >
                      {f.label}
                      <span className="ml-1 tabular-nums opacity-70">{filterCounts[f.id]}</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              {filtered.length === 0 ? (
                <div className="flex h-full items-center justify-center px-6 text-center text-[13px] text-slate-500">
                  没有匹配当前筛选条件的论文。
                </div>
              ) : (
                <div className="space-y-2">
                  {filtered.map(item => (
                    <RecListRow
                      key={item.it.id}
                      item={item}
                      selected={selected?.it.id === item.it.id}
                      onSelect={() => setSelectedId(item.it.id)}
                    />
                  ))}
                </div>
              )}
            </div>
          </main>

          <RecDetailPanel
            key={selected?.it.id ?? 'empty'}
            item={selected}
            summarize={summarize}
            onToggleMark={toggleMark}
            onDownload={handleDownload}
          />
        </div>
      </section>
    </div>
  )
}

function FilterSidebar({
  tags, tagCounts, total, selectedTag, onTag, selectedFilter, onFilter, filterCounts, className,
}: {
  tags: RecTag[]
  tagCounts: Map<string, number>
  total: number
  selectedTag: string
  onTag: (tag: string) => void
  selectedFilter: StatusFilter
  onFilter: (filter: StatusFilter) => void
  filterCounts: Record<StatusFilter, number>
  className?: string
}) {
  return (
    <aside className={`${className || ''} w-64 shrink-0 flex-col border-r border-slate-800/80 bg-[#0d1016]`}>
      <div className="border-b border-slate-800/80 px-4 py-3">
        <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-slate-500">方向</p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        <button
          onClick={() => onTag('all')}
          className={`mb-1 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12px] transition-colors ${
            selectedTag === 'all'
              ? 'bg-indigo-500/15 text-indigo-100'
              : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
          }`}
        >
          <Sparkles size={13} />
          <span className="min-w-0 flex-1 truncate">全部方向</span>
          <span className="tabular-nums text-slate-500">{total}</span>
        </button>
        {tags.map(t => (
          <button
            key={t.name}
            onClick={() => onTag(t.name)}
            className={`mb-1 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12px] transition-colors ${
              selectedTag === t.name
                ? 'bg-indigo-500/15 text-indigo-100'
                : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'
            }`}
          >
            <TagIcon size={12} className="shrink-0" />
            <span className="min-w-0 flex-1 truncate">{t.name}</span>
            <span className="tabular-nums text-slate-500">{tagCounts.get(t.name) || 0}</span>
          </button>
        ))}
      </div>
      <div className="border-t border-slate-800/80 p-2">
        <p className="mb-1 px-2 text-[11px] font-medium uppercase tracking-[0.08em] text-slate-500">筛选</p>
        {STATUS_FILTERS.map(f => (
          <button
            key={f.id}
            onClick={() => onFilter(f.id)}
            className={`mb-1 flex w-full items-center rounded-lg px-2.5 py-1.5 text-left text-[12px] transition-colors ${
              selectedFilter === f.id
                ? 'bg-slate-800 text-slate-100'
                : 'text-slate-500 hover:bg-slate-800/50 hover:text-slate-300'
            }`}
          >
            <span className="min-w-0 flex-1 truncate">{f.label}</span>
            <span className="tabular-nums text-slate-600">{filterCounts[f.id]}</span>
          </button>
        ))}
      </div>
    </aside>
  )
}

function FilterPill({
  active, label, count, onClick,
}: {
  active: boolean
  label: string
  count: number
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1 text-[12px] transition-colors ${
        active
          ? 'border-indigo-500/50 bg-indigo-500/15 text-indigo-100'
          : 'border-slate-700 bg-slate-900/40 text-slate-500'
      }`}
    >
      {label}
      <span className="tabular-nums opacity-70">{count}</span>
    </button>
  )
}

function RecListRow({
  item, selected, onSelect,
}: {
  item: DecoratedRecItem
  selected: boolean
  onSelect: () => void
}) {
  const { it, matchedTeam, marked, downloadStatus, done } = item
  const publishedAt = formatRecTime(it.published)
  const retrievedAt = formatRecTime(it.created_at)
  const preview = it.summary || it.abstract || ''

  return (
    <button
      onClick={onSelect}
      className={`w-full rounded-lg border p-3 text-left transition-colors ${
        selected
          ? 'border-indigo-400/60 bg-indigo-500/[0.08] ring-1 ring-indigo-400/30'
          : marked
            ? 'border-indigo-400/35 bg-indigo-500/[0.05] hover:border-indigo-400/60'
            : matchedTeam
              ? 'border-amber-400/35 bg-amber-500/[0.04] hover:border-amber-400/60'
              : 'border-slate-800 bg-[#0f1117] hover:border-slate-700'
      }`}
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            {marked && <Badge tone="indigo" icon={<Bookmark size={9} />} label="收藏" />}
            {matchedTeam && <Badge tone="amber" icon={<Star size={9} />} label={matchedTeam} />}
            {done && <Badge tone="emerald" icon={<Check size={9} />} label={downloadStatus === 'duplicate' ? '已存在' : '已下载'} />}
          </div>
          <p className="text-[13.5px] font-medium leading-snug text-slate-100">{it.title}</p>
          <p className="mt-1 truncate text-[11.5px] text-slate-500">
            {(it.authors || []).slice(0, 3).join(', ')}
            {it.authors.length > 3 ? ' 等' : ''}
            {it.primary_category ? ` · ${it.primary_category}` : ''}
          </p>
          {(publishedAt || retrievedAt) && (
            <p className="mt-1 text-[11px] text-slate-600">
              {publishedAt ? `发布 ${publishedAt}` : ''}
              {publishedAt && retrievedAt ? ' · ' : ''}
              {retrievedAt ? `检索 ${retrievedAt}` : ''}
            </p>
          )}
          {preview && (
            <p className="mt-2 max-h-10 overflow-hidden text-[12px] leading-5 text-slate-500">
              {preview}
            </p>
          )}
        </div>
        <span className="shrink-0 rounded-md border border-slate-800 px-1.5 py-0.5 text-[10px] text-slate-500">
          {it.tag}
        </span>
      </div>
    </button>
  )
}

function RecDetailPanel({
  item, summarize, onToggleMark, onDownload,
}: {
  item: DecoratedRecItem | null
  summarize: (it: RecItem) => Promise<string>
  onToggleMark: (arxivId: string) => void
  onDownload: (it: RecItem) => void
}) {
  const it = item?.it ?? null
  const abstract = it?.abstract || ''
  const [summary, setSummary] = useState<string | null>(it?.summary ?? null)
  const [sumState, setSumState] = useState<'idle' | 'loading' | 'done' | 'error'>(
    it?.summary ? 'done' : abstract ? 'loading' : 'idle',
  )
  const [showAbstract, setShowAbstract] = useState(false)

  useEffect(() => {
    if (!it || it.summary || !abstract) return
    let cancelled = false
    summarize(it)
      .then(s => {
        if (!cancelled) {
          setSummary(s)
          setSumState('done')
        }
      })
      .catch(() => {
        if (!cancelled) setSumState('error')
      })
    return () => {
      cancelled = true
    }
  }, [it, abstract, summarize])

  if (!item || !it) {
    return (
      <aside className="flex min-h-[22rem] w-full shrink-0 items-center justify-center overflow-y-auto bg-[#0f1117] px-8 text-center xl:w-[28rem]">
        <p className="max-w-xs text-[13px] leading-relaxed text-slate-500">
          选择一篇推荐论文后，这里会展示完整摘要、本地模型总结和下载操作。
        </p>
      </aside>
    )
  }

  const { matchedTeam, marked, downloadStatus, done } = item
  const publishedAt = formatRecTime(it.published)
  const retrievedAt = formatRecTime(it.created_at)
  const displayedSummary = summary || it.summary || null
  const displayedSumState = displayedSummary ? 'done' : sumState

  return (
    <aside className="min-h-[22rem] w-full shrink-0 overflow-y-auto bg-[#0f1117] xl:w-[28rem]">
      <div className="border-b border-slate-800/80 px-5 py-4">
        <div className="mb-2 flex flex-wrap gap-1.5">
          <Badge tone="slate" icon={<TagIcon size={9} />} label={it.tag} />
          {marked && <Badge tone="indigo" icon={<Bookmark size={9} />} label="已收藏" />}
          {matchedTeam && <Badge tone="amber" icon={<Star size={9} />} label={`关注团队 · ${matchedTeam}`} />}
          {done && <Badge tone="emerald" icon={<Check size={9} />} label={downloadStatus === 'duplicate' ? '已存在' : '已下载'} />}
        </div>
        <h2 className="text-[17px] font-semibold leading-snug text-white">{it.title}</h2>
        <p className="mt-2 text-[12px] leading-relaxed text-slate-500">
          {(it.authors || []).join(', ') || '未知作者'}
        </p>
      </div>

      <div className="space-y-5 px-5 py-4">
        <div className="grid grid-cols-2 gap-2 text-[12px]">
          <MetaTile label="arXiv" value={it.arxiv_id} />
          <MetaTile label="分类" value={it.primary_category || '未标注'} />
          <MetaTile label="发布时间" value={publishedAt || '未知'} />
          <MetaTile label="检索时间" value={retrievedAt || '未知'} />
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => onToggleMark(it.arxiv_id)}
            title={marked ? '取消收藏' : '收藏（在移动端/桌面同步，提醒下载）'}
            className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] transition-colors ${
              marked
                ? 'border-indigo-400/50 bg-indigo-500/15 text-indigo-200'
                : 'border-slate-700 text-slate-300 hover:bg-slate-800'
            }`}
          >
            {marked ? <BookmarkCheck size={13} /> : <Bookmark size={13} />}
            {marked ? '已收藏' : '收藏'}
          </button>
          {it.pdf_url && (
            <a
              href={it.pdf_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-[12px] text-slate-300 hover:bg-slate-800"
            >
              <ExternalLink size={13} /> arXiv
            </a>
          )}
          <button
            onClick={() => onDownload(it)}
            disabled={downloadStatus === 'downloading' || done}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] font-medium transition-colors ${
              done
                ? 'bg-emerald-500/15 text-emerald-300'
                : 'bg-indigo-500 text-white hover:bg-indigo-400 disabled:opacity-50'
            }`}
          >
            {downloadStatus === 'downloading' ? (
              <>
                <Loader2 size={13} className="animate-spin" /> 下载中
              </>
            ) : downloadStatus === 'downloaded' ? (
              <>
                <Check size={13} /> 已下载
              </>
            ) : downloadStatus === 'duplicate' ? (
              <>
                <Check size={13} /> 已存在
              </>
            ) : (
              <>
                <Download size={13} /> 下载到本地
              </>
            )}
          </button>
        </div>

        <section>
          <div className="mb-2 flex items-center gap-2">
            <Wand2 size={13} className="text-indigo-300" />
            <h3 className="text-[13px] font-semibold text-slate-100">本地总结</h3>
          </div>
          {displayedSumState === 'done' && displayedSummary ? (
            <p className="text-[13px] leading-relaxed text-slate-300">{displayedSummary}</p>
          ) : displayedSumState === 'loading' ? (
            <p className="flex items-center gap-1.5 text-[12px] text-indigo-300/80">
              <Loader2 size={12} className="animate-spin" /> 本地模型总结中...
            </p>
          ) : displayedSumState === 'error' ? (
            <p className="text-[12px] text-rose-300">总结失败，可稍后重新选择该论文。</p>
          ) : (
            <p className="text-[12px] text-slate-500">没有可总结的摘要。</p>
          )}
        </section>

        {abstract && (
          <section>
            <div className="mb-2 flex items-center gap-2">
              <h3 className="text-[13px] font-semibold text-slate-100">原始摘要</h3>
              <button
                onClick={() => setShowAbstract(s => !s)}
                className="ml-auto text-[11px] text-slate-500 hover:text-slate-300"
              >
                {showAbstract ? '收起' : '展开'}
              </button>
            </div>
            <p className={`text-[12.5px] leading-relaxed text-slate-500 ${showAbstract ? '' : 'max-h-24 overflow-hidden'}`}>
              {abstract}
            </p>
          </section>
        )}
      </div>
    </aside>
  )
}

function Badge({ tone, icon, label }: { tone: 'slate' | 'indigo' | 'amber' | 'emerald'; icon: ReactNode; label: string }) {
  const styles = {
    slate: 'border-slate-700 bg-slate-900/60 text-slate-400',
    indigo: 'border-indigo-400/50 bg-indigo-500/15 text-indigo-200',
    amber: 'border-amber-400/40 bg-amber-400/10 text-amber-200',
    emerald: 'border-emerald-400/40 bg-emerald-400/10 text-emerald-200',
  }
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium ${styles[tone]}`}>
      {icon}
      <span className="max-w-[9rem] truncate">{label}</span>
    </span>
  )
}

function MetaTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-slate-800 bg-slate-950/40 px-2.5 py-2">
      <p className="mb-1 text-[10px] uppercase tracking-[0.08em] text-slate-600">{label}</p>
      <p className="truncate text-[12px] text-slate-300" title={value}>{value}</p>
    </div>
  )
}

function formatRecTime(value: string | null | undefined): string | null {
  if (!value) return null
  const trimmed = String(value).trim()
  if (!trimmed) return null
  const compact = trimmed.replace('T', ' ').replace(/\.\d+Z?$/, '').replace(/Z$/, '')
  return compact.length >= 16 ? compact.slice(0, 16) : compact.slice(0, 10)
}

function CenteredNote({ title, msg, spinner }: { title: string; msg?: string; spinner?: boolean }) {
  return (
    <div className="flex h-full flex-col items-center justify-center bg-[#0b0d12] p-10 text-center">
      {spinner && <Loader2 size={20} className="mb-3 animate-spin text-indigo-300" />}
      <p className="text-[15px] font-medium text-slate-200">{title}</p>
      {msg && <p className="mt-2 max-w-md text-[13px] leading-relaxed text-slate-500">{msg}</p>}
    </div>
  )
}
