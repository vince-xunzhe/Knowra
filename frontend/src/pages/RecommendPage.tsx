import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Sparkles, RefreshCw, Download, Check, Loader2, ExternalLink, Tag as TagIcon,
  ChevronDown, ChevronRight, Star, Wand2,
} from 'lucide-react'
import {
  cloudRecommendations, cloudRefreshRecommendations,
  type RecItem, type RecTag,
} from '../api/cloud'
import { downloadRecommendation, summarizeRecommendation, listPaperTeams } from '../api/client'
import { useCloudAuth } from '../hooks/useCloudAuth'

// Followed tags are a client-side display filter (the feed itself is global).
const FOLLOW_KEY = 'knowra.rec.followed'

// Session caches (module-level so they survive the 推荐 tab unmounting /
// remounting → re-opening the tab paints instantly; a full app reload clears
// them). recCache holds the last feed; summaryCache holds per-paper summaries.
let recCache: { tags: RecTag[]; items: RecItem[] } | null = null
const summaryCache = new Map<string, string>()

function loadFollowed(): string[] | null {
  try {
    const raw = localStorage.getItem(FOLLOW_KEY)
    const arr = raw ? JSON.parse(raw) : null
    return Array.isArray(arr) ? arr.map(String) : null
  } catch {
    return null
  }
}
function saveFollowed(tags: string[]) {
  try {
    localStorage.setItem(FOLLOW_KEY, JSON.stringify(tags))
  } catch {
    /* storage unavailable — follow filter just won't persist */
  }
}

function apiErr(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail || x?.message || String(e)
}

// Normalize an author name for team matching — mirror backend _name_key.
const nameKey = (s: string) => (s || '').toLowerCase().replace(/[\s.\-_,]+/g, '')

// Concurrency-limited runner so expanding a 40-paper section doesn't fire 40
// local-LLM summary calls at once.
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
  const [tags, setTags] = useState<RecTag[]>([])
  const [items, setItems] = useState<RecItem[]>([])
  const [followed, setFollowed] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [teamMap, setTeamMap] = useState<Map<string, string>>(new Map())
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // arxiv_id → 'downloading' | 'downloaded' | 'duplicate'
  const [dl, setDl] = useState<Map<string, string>>(new Map())
  const [revalidating, setRevalidating] = useState(false)
  const initedRef = useRef(false)
  const limiterRef = useRef(makeLimiter(3))

  const applyData = useCallback((data: { tags: RecTag[]; items: RecItem[] }) => {
    setTags(data.tags)
    setItems(data.items)
    recCache = { tags: data.tags, items: data.items }
    if (initedRef.current) return
    initedRef.current = true
    const saved = loadFollowed()
    const follow = new Set(saved ?? data.tags.map(t => t.name)) // default: follow all
    setFollowed(follow)
    // Expand the first followed tag so the page isn't blank; the rest stay
    // collapsed — collapsed sections don't render cards, so no summary calls
    // fire until the user opens a section.
    const first = data.tags.map(t => t.name).find(n => follow.has(n))
    setExpanded(new Set(first ? [first] : []))
  }, [])

  const revalidate = useCallback(async () => {
    applyData(await cloudRecommendations(7))
  }, [applyData])

  // Local team registry → normalized author → team name, for highlighting
  // papers written by a team the user follows.
  const loadTeams = useCallback(async () => {
    try {
      const res = await listPaperTeams()
      const m = new Map<string, string>()
      for (const t of res.teams) for (const a of t.authors || []) m.set(nameKey(a), t.name)
      setTeamMap(m)
    } catch {
      /* team registry is optional for highlighting */
    }
  }, [])

  useEffect(() => {
    if (!auth.user) {
      setLoading(false)
      return
    }
    // Instant paint from the session cache, then refresh in the background — so
    // switching back to 推荐 doesn't show a blocking "加载中" every time.
    const hadCache = !!recCache
    if (hadCache) {
      applyData(recCache!)
      setLoading(false)
      setRevalidating(true)
    }
    revalidate()
      .catch(e => {
        if (!hadCache) setError(apiErr(e))
      })
      .finally(() => {
        setLoading(false)
        setRevalidating(false)
      })
    void loadTeams()
  }, [auth.user, applyData, revalidate, loadTeams])

  const toggleFollow = (name: string) =>
    setFollowed(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      saveFollowed([...next])
      return next
    })

  const toggleExpand = (name: string) =>
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })

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

  const grouped = useMemo(() => {
    const m = new Map<string, RecItem[]>()
    for (const it of items) {
      if (!followed.has(it.tag)) continue
      if (!m.has(it.tag)) m.set(it.tag, [])
      m.get(it.tag)!.push(it)
    }
    return m
  }, [items, followed])

  if (!auth.user) {
    return (
      <CenteredNote
        title="请先登录云端"
        msg="推荐来自云端每周的 arXiv 检索。到 设置 → 云同步 登录后即可查看。"
      />
    )
  }
  if (loading) return <CenteredNote title="加载中…" spinner />

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-6 py-8">
        <header className="mb-6 flex items-start gap-3">
          <Sparkles size={20} className="mt-0.5 shrink-0 text-indigo-300" />
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-semibold tracking-tight text-white">
              推荐
              {revalidating && (
                <Loader2 size={14} className="ml-2 inline animate-spin align-[-1px] text-slate-500" />
              )}
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              每周一 / 三 / 五自动检索 arXiv（保留近 7 天）。展开方向查看，摘要由本地模型归纳，关注团队的论文会高亮。
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
        </header>

        <div className="mb-6 flex flex-wrap items-center gap-2">
          {tags.map(t => {
            const on = followed.has(t.name)
            const n = items.filter(i => i.tag === t.name).length
            return (
              <button
                key={t.name}
                onClick={() => toggleFollow(t.name)}
                className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[12px] transition-colors ${
                  on
                    ? 'border-indigo-500/50 bg-indigo-500/15 text-indigo-100'
                    : 'border-slate-700 bg-slate-900/40 text-slate-500 hover:text-slate-300'
                }`}
              >
                <TagIcon size={11} />
                {t.name}
                <span className="tabular-nums opacity-70">{n}</span>
              </button>
            )
          })}
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-[12px] text-rose-200">
            {error}
          </div>
        )}

        {grouped.size === 0 ? (
          <CenteredNote
            title="还没有推荐"
            msg="调度器会在周一 / 三 / 五早上自动检索；想立刻拉取，点右上角「立即检索」。"
          />
        ) : (
          [...grouped.entries()].map(([tag, list]) => {
            const isOpen = expanded.has(tag)
            const followedCount = list.reduce((acc, it) => acc + (matchTeam(it) ? 1 : 0), 0)
            return (
              <section key={tag} className="mb-4">
                <button
                  onClick={() => toggleExpand(tag)}
                  className="flex w-full items-center gap-2 rounded-lg px-1 py-2 text-left hover:bg-slate-800/30"
                >
                  {isOpen ? (
                    <ChevronDown size={16} className="shrink-0 text-slate-500" />
                  ) : (
                    <ChevronRight size={16} className="shrink-0 text-slate-500" />
                  )}
                  <span className="text-[15px] font-semibold text-slate-100">{tag}</span>
                  <span className="tabular-nums text-[12px] font-normal text-slate-500">{list.length}</span>
                  {followedCount > 0 && (
                    <span className="inline-flex items-center gap-1 rounded-full border border-amber-400/40 bg-amber-400/10 px-2 py-0.5 text-[10px] text-amber-200">
                      <Star size={9} /> {followedCount} 篇关注团队
                    </span>
                  )}
                </button>
                {isOpen && (
                  <div className="mt-2 space-y-3">
                    {list.map(it => (
                      <RecCard
                        key={it.id}
                        it={it}
                        status={dl.get(it.arxiv_id)}
                        matchedTeam={matchTeam(it)}
                        summarize={summarize}
                        onDownload={() => handleDownload(it)}
                      />
                    ))}
                  </div>
                )}
              </section>
            )
          })
        )}
      </div>
    </div>
  )
}

function RecCard({
  it, status, matchedTeam, summarize, onDownload,
}: {
  it: RecItem
  status?: string
  matchedTeam: string | null
  summarize: (it: RecItem) => Promise<string>
  onDownload: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [inView, setInView] = useState(false)
  const [summary, setSummary] = useState<string | null>(null)
  const [sumState, setSumState] = useState<'idle' | 'loading' | 'done' | 'error'>('idle')
  const [showAbstract, setShowAbstract] = useState(false)
  const abstract = it.abstract || ''
  const done = status === 'downloaded' || status === 'duplicate'

  // Only summarize once the card scrolls into view, so expanding a 40-paper
  // section doesn't fire 40 (possibly slow, e.g. local Codex) LLM calls at once.
  useEffect(() => {
    const el = ref.current
    if (!el || inView) return
    const obs = new IntersectionObserver(
      entries => {
        if (entries.some(e => e.isIntersecting)) {
          setInView(true)
          obs.disconnect()
        }
      },
      { rootMargin: '300px' },
    )
    obs.observe(el)
    return () => obs.disconnect()
  }, [inView])

  // Local-LLM summary (server-side cached by arXiv id). The abstract stays
  // visible until the summary lands and simply gets replaced — so a slow model
  // never leaves the card looking stuck.
  useEffect(() => {
    if (!inView || !abstract) return
    let cancelled = false
    setSumState('loading')
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
  }, [inView, it, abstract, summarize])

  return (
    <div
      ref={ref}
      className={`rounded-xl border p-4 ${
        matchedTeam
          ? 'border-amber-400/50 bg-amber-500/[0.06] ring-1 ring-amber-400/20'
          : 'border-slate-800 bg-[#0f1117]'
      }`}
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          {matchedTeam && (
            <span className="mb-1 inline-flex items-center gap-1 rounded-full border border-amber-400/40 bg-amber-400/10 px-2 py-0.5 text-[10px] font-medium text-amber-200">
              <Star size={9} /> 关注团队 · {matchedTeam}
            </span>
          )}
          <p className="text-[14.5px] font-medium leading-snug text-slate-100">{it.title}</p>
          <p className="mt-1 truncate text-[12px] text-slate-500">
            {(it.authors || []).slice(0, 4).join(', ')}
            {it.authors.length > 4 ? ' 等' : ''}
            {it.primary_category ? ` · ${it.primary_category}` : ''}
            {it.published ? ` · 发布 ${it.published.slice(0, 10)}` : ''}
            {it.created_at ? ` · 检索 ${it.created_at.slice(0, 10)}` : ''}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {it.pdf_url && (
            <a
              href={it.pdf_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 rounded-lg border border-slate-700 px-2 py-1 text-[11px] text-slate-400 hover:text-slate-200"
            >
              <ExternalLink size={11} /> arXiv
            </a>
          )}
          <button
            onClick={onDownload}
            disabled={status === 'downloading' || done}
            className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] font-medium transition-colors ${
              done
                ? 'bg-emerald-500/15 text-emerald-300'
                : 'bg-indigo-500 text-white hover:bg-indigo-400 disabled:opacity-50'
            }`}
          >
            {status === 'downloading' ? (
              <>
                <Loader2 size={11} className="animate-spin" /> 下载中
              </>
            ) : status === 'downloaded' ? (
              <>
                <Check size={11} /> 已下载
              </>
            ) : status === 'duplicate' ? (
              <>
                <Check size={11} /> 已存在
              </>
            ) : (
              <>
                <Download size={11} /> 下载到本地
              </>
            )}
          </button>
        </div>
      </div>

      <div className="mt-2 text-[12.5px] leading-relaxed">
        {sumState === 'done' && summary ? (
          <>
            <p className="text-slate-300">
              <Wand2 size={11} className="mr-1 inline align-[-1px] text-indigo-300" />
              {summary}
            </p>
            {abstract && (
              <button
                onClick={() => setShowAbstract(s => !s)}
                className="mt-1 text-[11px] text-slate-500 hover:text-slate-300"
              >
                {showAbstract ? '收起原始摘要' : '查看原始摘要'}
              </button>
            )}
            {showAbstract && <p className="mt-1 text-[12px] leading-relaxed text-slate-500">{abstract}</p>}
          </>
        ) : abstract ? (
          <>
            <p className="text-slate-400">{abstract.length > 280 ? `${abstract.slice(0, 280)}…` : abstract}</p>
            {sumState === 'loading' && (
              <p className="mt-1 flex items-center gap-1.5 text-[11px] text-indigo-300/80">
                <Loader2 size={10} className="animate-spin" /> 本地模型总结中…
              </p>
            )}
          </>
        ) : null}
      </div>
    </div>
  )
}

function CenteredNote({ title, msg, spinner }: { title: string; msg?: string; spinner?: boolean }) {
  return (
    <div className="flex h-full flex-col items-center justify-center p-10 text-center">
      {spinner && <Loader2 size={20} className="mb-3 animate-spin text-indigo-300" />}
      <p className="text-[15px] font-medium text-slate-200">{title}</p>
      {msg && <p className="mt-2 max-w-md text-[13px] leading-relaxed text-slate-500">{msg}</p>}
    </div>
  )
}
