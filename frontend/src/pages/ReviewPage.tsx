import { useEffect, useState, useMemo } from 'react'
import {
  CheckCircle2, XCircle, Clock, Search, ExternalLink,
  BookOpen, Users, Calendar, Sparkles, Target, Lightbulb,
  Wrench, Database, Swords, Award, Flag, Hash, FileText,
} from 'lucide-react'
import {
  listPapers, getPaper, pdfFileUrl, firstPageUrl,
  type PaperRecord, type PaperDetail,
} from '../api/client'

type Filter = 'all' | 'processed' | 'pending' | 'failed'
type Technique = { name?: string; aliases?: string[]; role?: string; builds_on?: string[] }
type DatasetValue = string | { name?: string; purpose?: string; usage?: string }
type NamedValue = string | { name?: string }
type TextValue = string | { short?: string; detail?: string }

interface PaperExtraction {
  title?: string
  authors?: string[]
  venue?: string
  year?: string | number
  abstract_summary?: string
  problem?: string
  motivation?: string
  problem_area?: string
  techniques?: Technique[]
  datasets?: DatasetValue[]
  baselines?: NamedValue[]
  contributions?: TextValue[]
  key_findings?: TextValue[]
  keywords?: string[]
}

export default function ReviewPage() {
  const [papers, setPapers] = useState<PaperRecord[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<PaperDetail | null>(null)
  const [filter, setFilter] = useState<Filter>('processed')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [showRaw, setShowRaw] = useState(false)

  useEffect(() => {
    listPapers().then(ps => {
      setPapers(ps)
      const first = ps.find(p => p.processed)
      if (first) setSelectedId(first.id)
      setLoading(false)
    })
  }, [])

  useEffect(() => {
    if (selectedId === null) return

    let cancelled = false
    const loadDetail = async () => {
      try {
        const result = await getPaper(selectedId)
        if (!cancelled) setDetail(result)
      } catch (error) {
        console.error('Failed to load paper detail', error)
        if (!cancelled) setDetail(null)
      }
    }

    void loadDetail()
    return () => { cancelled = true }
  }, [selectedId])

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

  const visibleDetail = selectedId !== null && detail?.id === selectedId ? detail : null
  const rawResponse = visibleDetail?.raw_llm_response || ''
  const parsed = useMemo<PaperExtraction | null>(() => {
    if (!rawResponse) return null
    try { return JSON.parse(rawResponse) as PaperExtraction } catch { return null }
  }, [rawResponse])

  return (
    <div className="flex h-full text-slate-200">
      {/* Left list */}
      <aside className="w-80 bg-[#0f1117] border-r border-slate-800/80 flex flex-col overflow-hidden shrink-0">
        <div className="p-4 border-b border-slate-800/80 space-y-3">
          <div className="flex items-baseline justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-white tracking-tight">论文回顾</h2>
              <p className="text-sm text-slate-500 mt-1">按结构化字段阅读论文摘要、方法与结论。</p>
            </div>
            <span className="text-xs text-slate-500 tabular-nums">{filtered.length} / {papers.length}</span>
          </div>
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              placeholder="搜索标题、作者、文件名"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl text-sm text-slate-200 pl-9 pr-3 py-2 focus:outline-none focus:border-indigo-500/60 focus:bg-slate-900 transition-colors placeholder:text-slate-500"
            />
          </div>
          <div className="flex flex-wrap gap-1.5 text-xs">
            {([
              ['all', '全部'],
              ['processed', '已处理'],
              ['pending', '待处理'],
              ['failed', '失败'],
            ] as [Filter, string][]).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setFilter(key)}
                className={`px-3 py-1.5 rounded-lg border transition-colors ${
                  filter === key
                    ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500/20'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 border-transparent'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <p className="text-sm text-slate-500 p-6 text-center">加载中…</p>
          ) : filtered.length === 0 ? (
            <p className="text-sm text-slate-500 p-6 text-center">没有匹配的论文</p>
          ) : (
            <ul className="py-1">
              {filtered.map(p => {
                const active = selectedId === p.id
                return (
                  <li key={p.id}>
                    <button
                      onClick={() => setSelectedId(p.id)}
                      className={`w-full flex flex-col gap-1.5 px-4 py-3.5 text-left border-l-2 transition-colors ${
                        active
                          ? 'bg-indigo-500/10 border-l-indigo-400'
                          : 'border-l-transparent hover:bg-slate-900/60'
                      }`}
                    >
                      <p className={`text-sm leading-snug line-clamp-3 text-safe-wrap ${active ? 'text-white font-medium' : 'text-slate-300'}`}>
                        {p.title || p.filename}
                      </p>
                      {p.authors.length > 0 && (
                        <p className="text-xs text-slate-500 line-clamp-2 leading-relaxed text-safe-wrap">
                          {p.authors.slice(0, 3).join(', ')}
                        </p>
                      )}
                      <div className="flex flex-wrap items-center gap-2 text-xs">
                        <StatusBadge paper={p} />
                        {p.num_pages && <span className="text-slate-600">· {p.num_pages} 页</span>}
                      </div>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </aside>

      {/* Main reading area */}
      <section className="flex-1 min-w-0 overflow-y-auto">
        {!visibleDetail ? (
          <div className="h-full flex items-center justify-center text-slate-500">
            {papers.length === 0 ? '还没有论文' : '选择左侧论文查看详情'}
          </div>
        ) : (
          <article className="max-w-[58rem] mx-auto px-6 xl:px-10 py-10 fade-in">
            {/* Paper header */}
            <header className="mb-8">
              <div className="flex flex-wrap items-center gap-2 mb-4">
                <StatusBadge paper={visibleDetail} large />
                {visibleDetail.processed_at && (
                  <span className="text-xs text-slate-500">
                    于 {new Date(visibleDetail.processed_at).toLocaleString()} 处理
                  </span>
                )}
                <a
                  href={pdfFileUrl(visibleDetail.id)}
                  target="_blank"
                  rel="noreferrer"
                  className="ml-auto inline-flex items-center gap-1.5 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
                >
                  <ExternalLink size={12} /> 打开 PDF
                </a>
              </div>

              <h1 className="text-2xl font-semibold text-white leading-tight tracking-tight">
                {parsed?.title || visibleDetail.title || visibleDetail.filename}
              </h1>

              <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-sm text-slate-400">
                {Array.isArray(parsed?.authors) && parsed.authors.length > 0 && (
                  <span className="inline-flex items-center gap-1.5">
                    <Users size={13} className="text-slate-500" />
                    {parsed.authors.join(', ')}
                  </span>
                )}
                {(parsed?.venue || parsed?.year) && (
                  <span className="inline-flex items-center gap-1.5">
                    <Calendar size={13} className="text-slate-500" />
                    {parsed.venue}{parsed.venue && parsed.year && ' · '}{parsed.year}
                  </span>
                )}
                {visibleDetail.num_pages && (
                  <span className="inline-flex items-center gap-1.5">
                    <FileText size={13} className="text-slate-500" />
                    {visibleDetail.num_pages} 页
                  </span>
                )}
              </div>
            </header>

            {visibleDetail.error ? (
              <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-sm text-red-300">
                <p className="font-semibold mb-2 flex items-center gap-2">
                  <XCircle size={14} /> 处理失败
                </p>
                <p className="break-words leading-relaxed">{visibleDetail.error}</p>
              </div>
            ) : !visibleDetail.raw_llm_response ? (
              <div className="text-sm text-slate-500 py-8 text-center">
                该论文尚未处理。回到论文库点击「立即处理」。
              </div>
            ) : parsed ? (
              <StructuredBody data={parsed} detail={visibleDetail} />
            ) : (
              <div>
                <p className="text-sm text-amber-400 mb-2">⚠ 无法解析为 JSON，显示原文</p>
                <pre className="text-xs text-slate-300 bg-slate-900/60 rounded-xl p-4 whitespace-pre-wrap break-words font-mono leading-relaxed">
                  {visibleDetail.raw_llm_response}
                </pre>
              </div>
            )}

            {/* Debug drawer */}
            {visibleDetail.raw_llm_response && (
              <details className="mt-10 border-t border-slate-800 pt-6" open={showRaw} onToggle={e => setShowRaw((e.target as HTMLDetailsElement).open)}>
                <summary className="text-xs text-slate-500 cursor-pointer hover:text-slate-300 select-none">
                  查看原始模型输出 / 提取文本
                </summary>
                <div className="mt-4 grid gap-4 xl:grid-cols-2">
                  <div>
                    <p className="section-label mb-2">模型原文 JSON</p>
                    <pre className="text-[11px] text-slate-400 bg-slate-900/60 rounded-lg p-3 max-h-96 overflow-auto whitespace-pre-wrap break-words font-mono leading-relaxed">
                      {visibleDetail.raw_llm_response}
                    </pre>
                  </div>
                  <div>
                    <p className="section-label mb-2">PDF 文本（前 5000 字）</p>
                    <pre className="text-[11px] text-slate-400 bg-slate-900/60 rounded-lg p-3 max-h-96 overflow-auto whitespace-pre-wrap break-words font-mono leading-relaxed">
                      {(visibleDetail.extracted_text || '').slice(0, 5000)}
                    </pre>
                  </div>
                </div>
              </details>
            )}
          </article>
        )}
      </section>

      {/* Right: first page preview */}
      {visibleDetail?.has_first_page_image && (
        <aside className="hidden xl:flex w-72 bg-[#0f1117] border-l border-slate-800/80 flex-col overflow-hidden shrink-0">
          <div className="px-4 py-3 border-b border-slate-800/80">
            <p className="section-label">首页预览</p>
          </div>
          <div className="flex-1 overflow-y-auto p-3">
            <a href={pdfFileUrl(visibleDetail.id)} target="_blank" rel="noreferrer" className="block">
              <img
                src={firstPageUrl(visibleDetail.id)}
                alt="first page"
                className="w-full rounded-lg border border-slate-800 hover:border-indigo-500/60 transition-colors shadow-xl"
              />
            </a>
            {Array.isArray(parsed?.keywords) && parsed.keywords.length > 0 && (
              <div className="mt-4">
                <p className="section-label mb-2">关键词</p>
                <div className="flex flex-wrap gap-1">
                  {parsed.keywords.map((k: string, i: number) => (
                    <span key={i} className="chip bg-slate-800/80 text-slate-400 border border-slate-700/40">
                      <Hash size={10} />{k}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </aside>
      )}
    </div>
  )
}

function StatusBadge({ paper, large }: { paper: PaperRecord | PaperDetail; large?: boolean }) {
  const sz = large ? 14 : 11
  const cls = large ? 'text-xs px-2 py-0.5' : 'text-[11px] px-1.5 py-0'
  if (paper.processed) {
    return (
      <span className={`chip bg-emerald-500/15 text-emerald-300 ${cls}`}>
        <CheckCircle2 size={sz} /> 已处理
      </span>
    )
  }
  if (paper.error) {
    return (
      <span className={`chip bg-red-500/15 text-red-300 ${cls}`}>
        <XCircle size={sz} /> 失败
      </span>
    )
  }
  return (
    <span className={`chip bg-slate-700/40 text-slate-400 ${cls}`}>
      <Clock size={sz} /> 待处理
    </span>
  )
}

function StructuredBody({ data, detail }: { data: PaperExtraction; detail: PaperDetail }) {
  return (
    <div className="space-y-8">
      {/* TL;DR / summary */}
      {data.abstract_summary && (
        <ReviewBlock icon={<Sparkles size={14} />} title="摘要">
          <p className="prose-reading text-[14px]">{data.abstract_summary}</p>
        </ReviewBlock>
      )}

      {/* Problem + motivation side by side */}
      {(data.problem || data.motivation) && (
        <div className="grid gap-6 md:grid-cols-2">
          {data.problem && (
            <ReviewBlock icon={<Target size={14} />} title="研究问题">
              <p className="leading-7 text-slate-200">{data.problem}</p>
            </ReviewBlock>
          )}
          {data.motivation && (
            <ReviewBlock icon={<Lightbulb size={14} />} title="研究动机">
              <p className="leading-7 text-slate-200">{data.motivation}</p>
            </ReviewBlock>
          )}
        </div>
      )}

      {/* Problem area */}
      {data.problem_area && (
        <ReviewBlock icon={<Flag size={14} />} title="研究领域">
          <span className="inline-flex rounded-md border border-slate-700/70 bg-slate-950/35 px-2.5 py-1 text-sm font-medium text-cyan-200">
            {data.problem_area}
          </span>
        </ReviewBlock>
      )}

      {/* Techniques */}
      {Array.isArray(data.techniques) && data.techniques.length > 0 && (
        <ReviewBlock icon={<Wrench size={14} />} title="技术方法" meta={`${data.techniques.length}`}>
          <div className="grid md:grid-cols-2 gap-3">
            {data.techniques.map((t, i) => (
              <div
                key={i}
                className="rounded-lg border border-slate-800/80 bg-slate-950/35 px-3.5 py-3 transition-colors hover:border-slate-700"
              >
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <span className="text-sm font-semibold leading-6 text-emerald-200">{t.name}</span>
                  {t.role && <span className="text-xs leading-5 text-slate-500">{t.role}</span>}
                </div>
                {Array.isArray(t.aliases) && t.aliases.length > 0 && (
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    <span className="text-slate-600">别名 </span>
                    {t.aliases.join(' · ')}
                  </p>
                )}
                {Array.isArray(t.builds_on) && t.builds_on.length > 0 && (
                  <p className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
                    <span className="text-slate-600">基于</span>
                    {t.builds_on.map((b, j) => (
                      <span key={j} className="rounded-md border border-slate-700/70 bg-slate-900 px-2 py-0.5 text-[11px] text-emerald-200/80">
                        {b}
                      </span>
                    ))}
                  </p>
                )}
              </div>
            ))}
          </div>
        </ReviewBlock>
      )}

      {/* Datasets + Baselines */}
      {((Array.isArray(data.datasets) && data.datasets.length) ||
        (Array.isArray(data.baselines) && data.baselines.length)) && (
        <div className="grid gap-6 md:grid-cols-2">
          {Array.isArray(data.datasets) && data.datasets.length > 0 && (
            <ReviewBlock icon={<Database size={14} />} title="数据集" meta={`${data.datasets.length}`}>
              <ul className="space-y-2.5">
                {data.datasets.map((d, i) => {
                  const name = typeof d === 'string' ? d : d.name
                  const purpose = typeof d === 'object' ? d.purpose || d.usage : null
                  return (
                    <li key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800/80 bg-slate-950/35 px-3 py-2">
                      <span className="text-sm font-medium text-amber-200">
                        {name}
                      </span>
                      {purpose && <span className="text-xs text-slate-500">{purpose}</span>}
                    </li>
                  )
                })}
              </ul>
            </ReviewBlock>
          )}
          {Array.isArray(data.baselines) && data.baselines.length > 0 && (
            <ReviewBlock icon={<Swords size={14} />} title="对比基线" meta={`${data.baselines.length}`}>
              <div className="flex flex-wrap gap-2">
                {data.baselines.map((b, i) => (
                  <span key={i} className="rounded-md border border-slate-700/70 bg-slate-950/35 px-2.5 py-1 text-sm font-medium text-pink-200">
                    {typeof b === 'string' ? b : b.name}
                  </span>
                ))}
              </div>
            </ReviewBlock>
          )}
        </div>
      )}

      {/* Contributions */}
      {Array.isArray(data.contributions) && data.contributions.length > 0 && (
        <ReviewBlock icon={<Award size={14} />} title="主要贡献" meta={`${data.contributions.length}`}>
          <ol className="space-y-2.5">
            {data.contributions.map((c, i) => {
              const text = typeof c === 'string' ? c : (c.short || c.detail || JSON.stringify(c))
              return (
                <li key={i} className="flex gap-3 rounded-lg border border-slate-800/80 bg-slate-950/35 px-3 py-2.5">
                  <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-slate-700/70 bg-slate-900 text-xs font-semibold text-indigo-200">
                    {i + 1}
                  </span>
                  <p className="pt-0.5 leading-7 text-slate-200">{text}</p>
                </li>
              )
            })}
          </ol>
        </ReviewBlock>
      )}

      {/* Key findings */}
      {Array.isArray(data.key_findings) && data.key_findings.length > 0 && (
        <ReviewBlock icon={<Lightbulb size={14} />} title="关键发现" meta={`${data.key_findings.length}`}>
          <div className="space-y-2.5">
            {data.key_findings.map((f, i) => (
              <div
                key={i}
                className="rounded-lg border border-slate-800/80 bg-slate-950/35 px-3.5 py-3"
              >
                {typeof f === 'string' ? (
                  <p className="leading-7 text-slate-200">{f}</p>
                ) : (
                  <>
                    {f.short && <p className="font-medium leading-6 text-amber-100">{f.short}</p>}
                    {f.detail && <p className="mt-1.5 text-sm leading-7 text-slate-400">{f.detail}</p>}
                  </>
                )}
              </div>
            ))}
          </div>
        </ReviewBlock>
      )}

      {/* Generated nodes */}
      {detail.knowledge_nodes.length > 0 && (
        <ReviewBlock icon={<BookOpen size={14} />} title="生成的图谱节点" meta={`${detail.knowledge_nodes.length}`}>
          <div className="flex flex-wrap gap-2">
            {detail.knowledge_nodes.map(n => (
              <span
                key={n.id}
                className="inline-flex items-center gap-1 rounded-md border border-slate-700/70 bg-slate-950/35 px-2.5 py-1 text-sm text-slate-300"
                title={n.node_type}
              >
                {n.title}
                <span className="text-slate-500 ml-1">· {n.node_type}</span>
              </span>
            ))}
          </div>
        </ReviewBlock>
      )}
    </div>
  )
}

function ReviewBlock({
  icon, title, meta, children,
}: { icon: React.ReactNode; title: string; meta?: string; children: React.ReactNode }) {
  return (
    <section className="overflow-hidden rounded-xl border border-slate-800/80 bg-slate-900/35 shadow-[0_12px_28px_rgba(2,6,23,0.16)]">
      <div className="flex min-h-12 items-center gap-2.5 border-b border-slate-800/70 bg-slate-950/25 px-4 py-2.5">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-slate-700/70 bg-slate-900 text-slate-400">
          {icon}
        </span>
        <h3 className="text-sm font-semibold tracking-tight text-slate-100">{title}</h3>
        {meta && (
          <span className="ml-auto rounded-md border border-slate-700/70 bg-slate-900 px-2 py-0.5 text-xs tabular-nums text-slate-400">
            {meta}
          </span>
        )}
      </div>
      <div className="px-4 py-4 text-sm">{children}</div>
    </section>
  )
}
