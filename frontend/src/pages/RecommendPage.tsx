import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Sparkles, RefreshCw, Download, Check, Loader2, ExternalLink, Tag as TagIcon,
} from 'lucide-react'
import {
  cloudRecommendations, cloudRefreshRecommendations,
  type RecItem, type RecTag,
} from '../api/cloud'
import { downloadRecommendation } from '../api/client'
import { useCloudAuth } from '../hooks/useCloudAuth'

// Followed tags are a client-side display filter (the feed itself is global).
const FOLLOW_KEY = 'knowra.rec.followed'

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

export default function RecommendPage() {
  const auth = useCloudAuth()
  const [tags, setTags] = useState<RecTag[]>([])
  const [items, setItems] = useState<RecItem[]>([])
  const [followed, setFollowed] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // arxiv_id → 'downloading' | 'downloaded' | 'duplicate'
  const [dl, setDl] = useState<Map<string, string>>(new Map())

  const load = useCallback(async () => {
    setError(null)
    const data = await cloudRecommendations(30)
    setTags(data.tags)
    setItems(data.items)
    const saved = loadFollowed()
    setFollowed(new Set(saved ?? data.tags.map(t => t.name))) // default: follow all
  }, [])

  useEffect(() => {
    if (!auth.user) {
      setLoading(false)
      return
    }
    load()
      .catch(e => setError(apiErr(e)))
      .finally(() => setLoading(false))
  }, [auth.user, load])

  const toggleFollow = (name: string) =>
    setFollowed(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      saveFollowed([...next])
      return next
    })

  const refresh = async () => {
    setRefreshing(true)
    setError(null)
    try {
      await cloudRefreshRecommendations()
      await load()
    } catch (e) {
      setError(apiErr(e))
    } finally {
      setRefreshing(false)
    }
  }

  const handleDownload = async (it: RecItem) => {
    setDl(prev => new Map(prev).set(it.arxiv_id, 'downloading'))
    try {
      const res = await downloadRecommendation({
        arxiv_id: it.arxiv_id,
        pdf_url: it.pdf_url,
        title: it.title,
      })
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
            <h1 className="text-2xl font-semibold tracking-tight text-white">推荐</h1>
            <p className="mt-1 text-sm text-slate-500">
              每周一 / 三 / 五自动检索 arXiv，按方向推送新论文（保留近 30 天）。点关键词筛选，一键下载到本地 papers 目录。
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
          [...grouped.entries()].map(([tag, list]) => (
            <section key={tag} className="mb-8">
              <h2 className="mb-3 flex items-center gap-2 text-[15px] font-semibold text-slate-100">
                {tag}
                <span className="tabular-nums text-[12px] font-normal text-slate-500">{list.length}</span>
              </h2>
              <div className="space-y-3">
                {list.map(it => (
                  <RecCard
                    key={it.id}
                    it={it}
                    status={dl.get(it.arxiv_id)}
                    onDownload={() => handleDownload(it)}
                  />
                ))}
              </div>
            </section>
          ))
        )}
      </div>
    </div>
  )
}

function RecCard({
  it, status, onDownload,
}: {
  it: RecItem
  status?: string
  onDownload: () => void
}) {
  const [open, setOpen] = useState(false)
  const abstract = it.abstract || ''
  const short = abstract.length > 280 && !open ? `${abstract.slice(0, 280)}…` : abstract
  const done = status === 'downloaded' || status === 'duplicate'
  return (
    <div className="rounded-xl border border-slate-800 bg-[#0f1117] p-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-[14.5px] font-medium leading-snug text-slate-100">{it.title}</p>
          <p className="mt-1 truncate text-[12px] text-slate-500">
            {(it.authors || []).slice(0, 4).join(', ')}
            {it.authors.length > 4 ? ' 等' : ''}
            {it.primary_category ? ` · ${it.primary_category}` : ''}
            {it.published ? ` · ${it.published.slice(0, 10)}` : ''}
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
      {abstract && (
        <p
          onClick={() => setOpen(o => !o)}
          className="mt-2 cursor-pointer text-[12.5px] leading-relaxed text-slate-400"
          title={open ? '收起' : '展开'}
        >
          {short}
        </p>
      )}
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
