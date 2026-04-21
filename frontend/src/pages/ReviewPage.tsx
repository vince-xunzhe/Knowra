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
        <section>
          <SectionHeader icon={<Sparkles size={14} />} label="摘要" />
          <div className="surface-card p-5">
            <p className="prose-reading">{data.abstract_summary}</p>
          </div>
        </section>
      )}

      {/* Problem + motivation side by side */}
      {(data.problem || data.motivation) && (
        <section className="grid md:grid-cols-2 gap-5">
          {data.problem && (
            <Card icon={<Target size={13} />} label="研究问题">
              <p className="text-slate-200 leading-relaxed">{data.problem}</p>
            </Card>
          )}
          {data.motivation && (
            <Card icon={<Lightbulb size={13} />} label="研究动机">
              <p className="text-slate-200 leading-relaxed">{data.motivation}</p>
            </Card>
          )}
        </section>
      )}

      {/* Problem area */}
      {data.problem_area && (
        <section>
          <SectionHeader icon={<Flag size={14} />} label="研究领域" />
          <span className="chip bg-cyan-500/10 text-cyan-300 border border-cyan-500/20 text-sm px-3 py-1">
            {data.problem_area}
          </span>
        </section>
      )}

      {/* Techniques */}
      {Array.isArray(data.techniques) && data.techniques.length > 0 && (
        <section>
          <SectionHeader icon={<Wrench size={14} />} label={`技术方法 · ${data.techniques.length}`} />
          <div className="grid md:grid-cols-2 gap-3">
            {data.techniques.map((t, i) => (
              <div
                key={i}
                className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 hover:border-emerald-500/30 transition-colors"
              >
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className="text-emerald-300 font-semibold text-base">{t.name}</span>
                  {t.role && <span className="text-xs text-slate-500">{t.role}</span>}
                </div>
                {Array.isArray(t.aliases) && t.aliases.length > 0 && (
                  <p className="text-xs text-slate-500 mt-1.5">
                    <span className="text-slate-600">别名 </span>
                    {t.aliases.join(' · ')}
                  </p>
                )}
                {Array.isArray(t.builds_on) && t.builds_on.length > 0 && (
                  <p className="text-xs text-slate-400 mt-2 flex items-center gap-1.5 flex-wrap">
                    <span className="text-slate-600">基于</span>
                    {t.builds_on.map((b, j) => (
                      <span key={j} className="chip bg-emerald-500/5 text-emerald-400/80 border border-emerald-500/15 text-[11px]">
                        {b}
                      </span>
                    ))}
                  </p>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Datasets + Baselines */}
      {((Array.isArray(data.datasets) && data.datasets.length) ||
        (Array.isArray(data.baselines) && data.baselines.length)) && (
        <section className="grid md:grid-cols-2 gap-5">
          {Array.isArray(data.datasets) && data.datasets.length > 0 && (
            <Card icon={<Database size={13} />} label={`数据集 · ${data.datasets.length}`}>
              <ul className="space-y-2">
                {data.datasets.map((d, i) => {
                  const name = typeof d === 'string' ? d : d.name
                  const purpose = typeof d === 'object' ? d.purpose || d.usage : null
                  return (
                    <li key={i} className="flex items-baseline gap-2 flex-wrap">
                      <span className="chip bg-amber-500/10 text-amber-300 border border-amber-500/20">
                        {name}
                      </span>
                      {purpose && <span className="text-xs text-slate-500">{purpose}</span>}
                    </li>
                  )
                })}
              </ul>
            </Card>
          )}
          {Array.isArray(data.baselines) && data.baselines.length > 0 && (
            <Card icon={<Swords size={13} />} label={`对比基线 · ${data.baselines.length}`}>
              <div className="flex flex-wrap gap-1.5">
                {data.baselines.map((b, i) => (
                  <span key={i} className="chip bg-pink-500/10 text-pink-300 border border-pink-500/20">
                    {typeof b === 'string' ? b : b.name}
                  </span>
                ))}
              </div>
            </Card>
          )}
        </section>
      )}

      {/* Contributions */}
      {Array.isArray(data.contributions) && data.contributions.length > 0 && (
        <section>
          <SectionHeader icon={<Award size={14} />} label="主要贡献" />
          <ol className="space-y-2.5">
            {data.contributions.map((c, i) => {
              const text = typeof c === 'string' ? c : (c.short || c.detail || JSON.stringify(c))
              return (
                <li key={i} className="flex gap-3">
                  <span className="shrink-0 w-6 h-6 rounded-full bg-indigo-500/15 text-indigo-300 text-xs font-semibold flex items-center justify-center mt-0.5">
                    {i + 1}
                  </span>
                  <p className="text-slate-200 leading-relaxed pt-0.5">{text}</p>
                </li>
              )
            })}
          </ol>
        </section>
      )}

      {/* Key findings */}
      {Array.isArray(data.key_findings) && data.key_findings.length > 0 && (
        <section>
          <SectionHeader icon={<Lightbulb size={14} />} label={`关键发现 · ${data.key_findings.length}`} />
          <div className="space-y-3">
            {data.key_findings.map((f, i) => (
              <div
                key={i}
                className="bg-gradient-to-br from-amber-500/5 to-transparent border border-amber-500/20 rounded-xl p-4"
              >
                {typeof f === 'string' ? (
                  <p className="text-slate-200 leading-relaxed">{f}</p>
                ) : (
                  <>
                    {f.short && <p className="text-amber-200 font-medium leading-snug">{f.short}</p>}
                    {f.detail && <p className="text-slate-400 text-sm mt-2 leading-relaxed">{f.detail}</p>}
                  </>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Generated nodes */}
      {detail.knowledge_nodes.length > 0 && (
        <section>
          <SectionHeader icon={<BookOpen size={14} />} label={`生成的图谱节点 · ${detail.knowledge_nodes.length}`} />
          <div className="flex flex-wrap gap-1.5">
            {detail.knowledge_nodes.map(n => (
              <span
                key={n.id}
                className="chip bg-slate-800/80 text-slate-300 border border-slate-700/40 text-xs"
                title={n.node_type}
              >
                {n.title}
                <span className="text-slate-500 ml-1">· {n.node_type}</span>
              </span>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

function SectionHeader({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-2 mb-3.5 text-slate-400">
      <span className="text-slate-500">{icon}</span>
      <h3 className="text-base font-semibold tracking-tight text-slate-100">{label}</h3>
    </div>
  )
}

function Card({
  icon, label, children,
}: { icon: React.ReactNode; label: string; children: React.ReactNode }) {
  return (
    <div className="surface-card p-4">
      <div className="flex items-center gap-1.5 mb-2.5 text-slate-500">
        {icon}
        <span className="panel-title">{label}</span>
      </div>
      <div className="text-sm">{children}</div>
    </div>
  )
}
