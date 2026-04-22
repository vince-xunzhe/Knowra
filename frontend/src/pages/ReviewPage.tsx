import { useEffect, useState, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  CheckCircle2, XCircle, Clock, Search, ExternalLink,
  BookOpen, Users, Calendar, Sparkles, Target, Lightbulb,
  Wrench, Database, Swords, Award, Flag, Hash, FileText,
  Pencil, Save, RotateCw, Loader2, X, NotebookPen, Eye,
  Zap, Layers, GitBranch, History, AlertTriangle, Code2,
  Copy, Check, ArrowRight, Plus, Trash2,
} from 'lucide-react'
import {
  listPapers, getPaper, reprocessPaper, updatePaperResponse, updatePaperNotes,
  pdfFileUrl, firstPageUrl,
  type PaperRecord, type PaperDetail,
} from '../api/client'

type Filter = 'all' | 'processed' | 'pending' | 'failed'
type Technique = { name?: string; aliases?: string[]; role?: string; builds_on?: string[] }
type DatasetValue = string | { name?: string; purpose?: string; usage?: string }
type NamedValue = string | { name?: string }
type TextValue = string | { short?: string; detail?: string }

interface PrincipleBlock {
  analogy?: string
  architecture_flow?: string
  key_formulas?: { name?: string; plain?: string }[]
}

interface InnovationsBlock {
  previous_work?: string
  this_work?: string
  why_better?: string
}

interface HistoricalPosition {
  builds_on?: string
  inspired?: string
  overall?: string
}

interface PytorchSnippet {
  module_name?: string
  code?: string
  notes?: string
}

interface PaperExtraction {
  title?: string
  authors?: string[]
  venue?: string
  year?: string | number
  abstract_summary?: string
  problem?: string
  motivation?: string
  problem_area?: string
  tech_stack_position?: string
  core_contribution?: string
  principle?: PrincipleBlock | string
  innovations?: InnovationsBlock
  experimental_gains?: string
  historical_position?: HistoricalPosition
  limitations?: string
  pytorch_snippet?: PytorchSnippet
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
  const [editingRaw, setEditingRaw] = useState(false)
  const [rawDraft, setRawDraft] = useState('')
  const [rawError, setRawError] = useState<string | null>(null)
  const [savingRaw, setSavingRaw] = useState(false)
  const [reprocessing, setReprocessing] = useState(false)

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
    // Backend already canonicalized the JSON (keys + nesting). Prefer that.
    if (visibleDetail?.extraction) return visibleDetail.extraction as PaperExtraction
    if (!rawResponse) return null
    return parseExtractionResponse(rawResponse)
  }, [visibleDetail, rawResponse])

  const selectPaper = (id: number) => {
    setSelectedId(id)
    setEditingRaw(false)
    setRawDraft('')
    setRawError(null)
  }

  const startRawEdit = () => {
    if (!visibleDetail) return
    setRawDraft(visibleDetail.raw_llm_response || '')
    setRawError(null)
    setEditingRaw(true)
  }

  const cancelRawEdit = () => {
    setEditingRaw(false)
    setRawDraft('')
    setRawError(null)
  }

  const saveRawEdit = async () => {
    if (!visibleDetail) return
    setSavingRaw(true)
    setRawError(null)
    try {
      const updated = await updatePaperResponse(visibleDetail.id, rawDraft)
      setDetail(updated)
      setPapers(prev => prev.map(p => p.id === updated.id ? updated : p))
      setEditingRaw(false)
      setRawDraft('')
    } catch (error) {
      setRawError(getApiErrorMessage(error))
    } finally {
      setSavingRaw(false)
    }
  }

  const handleReprocess = async () => {
    if (!visibleDetail) return
    const ok = confirm('确认重新处理这篇论文？现有抽取结果和图谱节点会被清空，并重新调用大模型。')
    if (!ok) return

    setReprocessing(true)
    try {
      await reprocessPaper(visibleDetail.id)
      const updated = {
        ...visibleDetail,
        processed: false,
        processed_at: null,
        raw_llm_response: null,
        error: null,
        knowledge_nodes: [],
      }
      setDetail(updated)
      setPapers(prev => prev.map(p => p.id === updated.id ? updated : p))
      cancelRawEdit()
    } catch (error) {
      setRawError(getApiErrorMessage(error))
    } finally {
      setReprocessing(false)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col text-slate-200 lg:flex-row">
      {/* Left list */}
      <aside className="flex h-[17.5rem] w-full shrink-0 flex-col overflow-hidden border-b border-slate-800/80 bg-[#0f1117] lg:h-auto lg:w-72 lg:border-b-0 lg:border-r xl:w-80">
        <div className="p-4 border-b border-slate-800/80 space-y-3">
          <div className="flex items-baseline justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-white tracking-tight">论文回顾</h2>
              <p className="text-sm text-slate-500 mt-1">按结构化字段阅读论文摘要、方法与结论。</p>
            </div>
            <span className="text-xs text-slate-500 tabular-nums">{filtered.length} / {papers.length}</span>
          </div>
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none" />
            <input
              type="text"
              placeholder="搜索标题、作者、文件名"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg text-sm leading-tight text-slate-200 pl-10 pr-3 py-1 focus:outline-none focus:border-indigo-500/60 focus:bg-slate-900 transition-colors placeholder:text-slate-500"
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

        <div className="min-h-0 flex-1 overflow-y-auto">
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
                      onClick={() => selectPaper(p.id)}
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
      <section className="min-h-0 flex-1 min-w-0 overflow-y-auto">
        {!visibleDetail ? (
          <div className="flex h-full min-h-[16rem] items-center justify-center text-slate-500">
            {papers.length === 0 ? '还没有论文' : '选择左侧论文查看详情'}
          </div>
        ) : (
          <article className="mx-auto w-full max-w-[112rem] px-4 py-6 fade-in sm:px-6 lg:px-7 lg:py-8 xl:px-8">
            {/* Paper header */}
            <header className="mb-6 lg:mb-8">
              <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge paper={visibleDetail} large />
                  {visibleDetail.processed_at && (
                    <span className="text-xs text-slate-500">
                      于 {new Date(visibleDetail.processed_at).toLocaleString()} 处理
                    </span>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                  {visibleDetail.raw_llm_response && (
                    <button
                      onClick={startRawEdit}
                      disabled={editingRaw || savingRaw}
                      className="inline-flex items-center gap-1 rounded-md border border-slate-800 bg-slate-950/30 px-2 py-1 text-[11px] leading-none text-slate-400 transition-colors hover:border-slate-700 hover:bg-slate-900 hover:text-slate-200 disabled:cursor-not-allowed disabled:text-slate-600"
                    >
                      <Pencil size={10} /> 编辑 Response
                    </button>
                  )}
                  <button
                    onClick={handleReprocess}
                    disabled={reprocessing}
                    className="inline-flex items-center gap-1 rounded-md border border-slate-800 bg-slate-950/30 px-2 py-1 text-[11px] leading-none text-slate-400 transition-colors hover:border-slate-700 hover:bg-slate-900 hover:text-slate-200 disabled:cursor-not-allowed disabled:text-slate-600"
                  >
                    {reprocessing ? <Loader2 size={10} className="animate-spin" /> : <RotateCw size={10} />}
                    重新处理
                  </button>
                  <a
                    href={pdfFileUrl(visibleDetail.id)}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1.5 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
                  >
                    <ExternalLink size={12} /> 打开 PDF
                  </a>
                </div>
              </div>

              <h1 className="text-xl font-semibold leading-tight tracking-tight text-white text-safe-wrap sm:text-2xl">
                {parsed?.title || visibleDetail.title || visibleDetail.filename}
              </h1>

              <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-sm text-slate-400">
                {Array.isArray(parsed?.authors) && parsed.authors.length > 0 && (
                  <span className="inline-flex min-w-0 max-w-full items-start gap-1.5 text-safe-wrap">
                    <Users size={13} className="text-slate-500" />
                    <span>{parsed.authors.join(', ')}</span>
                  </span>
                )}
                {(parsed?.venue || parsed?.year) && (
                  <span className="inline-flex min-w-0 max-w-full items-start gap-1.5 text-safe-wrap">
                    <Calendar size={13} className="text-slate-500" />
                    <span>{parsed.venue}{parsed.venue && parsed.year && ' · '}{parsed.year}</span>
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

            {editingRaw ? (
              <RawResponseEditor
                value={rawDraft}
                error={rawError}
                saving={savingRaw}
                onChange={setRawDraft}
                onCancel={cancelRawEdit}
                onSave={saveRawEdit}
              />
            ) : (
              <div
                className={`grid items-start gap-5 ${
                  visibleDetail.has_first_page_image
                    ? 'xl:grid-cols-[minmax(0,1fr)_minmax(17rem,22rem)] 2xl:grid-cols-[minmax(0,1fr)_minmax(18rem,23rem)_minmax(15rem,20rem)]'
                    : 'xl:grid-cols-[minmax(0,1fr)_minmax(18rem,24rem)]'
                }`}
              >
                <div className="min-w-0">
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
                      <button
                        onClick={startRawEdit}
                        className="mb-3 inline-flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200 transition-colors hover:bg-amber-500/15"
                      >
                        <Pencil size={13} /> 编辑并修复 Response
                      </button>
                      <pre className="text-xs text-slate-300 bg-slate-900/60 rounded-xl p-4 whitespace-pre-wrap break-words font-mono leading-relaxed">
                        {visibleDetail.raw_llm_response}
                      </pre>
                    </div>
                  )}
                </div>

                {/* Personal notes — markdown, side column */}
                <div className="min-w-0 space-y-5 xl:sticky xl:top-6 xl:max-h-[calc(100vh-7rem)] xl:overflow-y-auto xl:pr-1">
                  <NotesSection
                    key={visibleDetail.id}
                    paper={visibleDetail}
                    onUpdate={updated => {
                      setDetail(updated)
                      setPapers(prev => prev.map(p => p.id === updated.id ? updated : p))
                    }}
                  />
                </div>

                {visibleDetail.has_first_page_image && (
                  <div className="hidden min-w-0 2xl:block 2xl:sticky 2xl:top-6 2xl:max-h-[calc(100vh-7rem)] 2xl:overflow-y-auto 2xl:pr-1">
                    <FirstPagePreview
                      paper={visibleDetail}
                      keywords={Array.isArray(parsed?.keywords) ? parsed.keywords : []}
                    />
                  </div>
                )}
              </div>
            )}

            {/* Debug drawer */}
            {visibleDetail.raw_llm_response && (
              <details className="mt-10 border-t border-slate-800 pt-6" open={showRaw} onToggle={e => setShowRaw((e.target as HTMLDetailsElement).open)}>
                <summary className="text-xs text-slate-500 cursor-pointer hover:text-slate-300 select-none">
                  查看原始模型输出
                </summary>
                <div className="mt-4">
                  <p className="section-label mb-2">模型原文 JSON</p>
                  <pre className="text-[11px] text-slate-400 bg-slate-900/60 rounded-lg p-3 max-h-96 overflow-auto whitespace-pre-wrap break-words font-mono leading-relaxed">
                    {visibleDetail.raw_llm_response}
                  </pre>
                </div>
              </details>
            )}
          </article>
        )}
      </section>

    </div>
  )
}

function FirstPagePreview({
  paper, keywords,
}: {
  paper: PaperDetail
  keywords: string[]
}) {
  return (
    <ReviewBlock icon={<FileText size={14} />} title="首页预览">
      <div className="space-y-4">
        <a href={pdfFileUrl(paper.id)} target="_blank" rel="noreferrer" className="block">
          <img
            src={firstPageUrl(paper.id)}
            alt="first page"
            className="max-h-[44vh] w-full rounded-lg border border-slate-800 object-contain shadow-xl transition-colors hover:border-indigo-500/60"
          />
        </a>
        {keywords.length > 0 && (
          <div>
            <p className="section-label mb-2">关键词</p>
            <div className="flex flex-wrap gap-1">
              {keywords.map((k: string, i: number) => (
                <span key={i} className="chip border border-slate-700/40 bg-slate-800/80 text-slate-400">
                  <Hash size={10} />{k}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </ReviewBlock>
  )
}

function RawResponseEditor({
  value, error, saving, onChange, onCancel, onSave,
}: {
  value: string
  error: string | null
  saving: boolean
  onChange: (value: string) => void
  onCancel: () => void
  onSave: () => void
}) {
  return (
    <ReviewBlock icon={<Pencil size={14} />} title="编辑模型 Response">
      <div className="space-y-3">
        <p className="text-sm leading-6 text-slate-400">
          修正 JSON 格式或字段小错误后保存，系统会重新解析这份 response，并重建当前论文的图谱节点。
        </p>
        {error && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm leading-6 text-red-200">
            {error}
          </div>
        )}
        <textarea
          value={value}
          onChange={e => onChange(e.target.value)}
          spellCheck={false}
          className="min-h-[18rem] w-full resize-y rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-3 font-mono text-xs leading-6 text-slate-200 outline-none transition-colors focus:border-indigo-500/60 lg:min-h-[28rem]"
        />
        <div className="flex flex-wrap items-center justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={saving}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700/70 bg-slate-900 px-3 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-800 hover:text-white disabled:cursor-not-allowed disabled:text-slate-600"
          >
            <X size={14} /> 取消
          </button>
          <button
            onClick={onSave}
            disabled={saving || !value.trim()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            保存并重建
          </button>
        </div>
      </div>
    </ReviewBlock>
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
  const principle: PrincipleBlock | null = typeof data.principle === 'string'
    ? { analogy: data.principle }
    : data.principle || null

  return (
    <div className="space-y-8">
      {/* Core contribution — prominent TL;DR */}
      {data.core_contribution && (
        <section className="relative overflow-hidden rounded-xl border border-indigo-500/30 bg-gradient-to-br from-indigo-500/15 via-slate-900/40 to-slate-900/30 px-5 py-4 shadow-[0_18px_40px_rgba(49,46,129,0.2)]">
          <div className="flex items-center gap-2 text-[11px] font-semibold tracking-[0.18em] uppercase text-indigo-300">
            <Zap size={13} /> 核心贡献
          </div>
          <p className="mt-2.5 text-[15px] leading-8 text-slate-100">
            {data.core_contribution}
          </p>
        </section>
      )}

      {/* Problem area + tech stack position */}
      {(data.problem_area || data.tech_stack_position) && (
        <div className="flex flex-wrap items-center gap-2">
          {data.tech_stack_position && (
            <span className="inline-flex items-center gap-1.5 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 text-sm font-medium text-emerald-200">
              <Layers size={13} /> {data.tech_stack_position}
            </span>
          )}
          {data.problem_area && (
            <span className="inline-flex items-center gap-1.5 rounded-md border border-cyan-500/30 bg-cyan-500/10 px-2.5 py-1 text-sm font-medium text-cyan-200">
              <Flag size={13} /> {data.problem_area}
            </span>
          )}
        </div>
      )}

      {/* TL;DR / summary */}
      {data.abstract_summary && (
        <ReviewBlock icon={<Sparkles size={14} />} title="摘要">
          <p className="prose-reading text-[14px]">{data.abstract_summary}</p>
        </ReviewBlock>
      )}

      {/* Problem + motivation side by side */}
      {(data.problem || data.motivation) && (
        <div className="grid gap-4 lg:grid-cols-2">
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

      {/* Principle — Feynman-style analogy + data flow */}
      {principle && (principle.analogy || principle.architecture_flow || (principle.key_formulas?.length ?? 0) > 0) && (
        <ReviewBlock icon={<Lightbulb size={14} />} title="原理解析（费曼式）">
          <div className="space-y-4">
            {principle.analogy && (
              <div>
                <p className="section-label mb-1.5 text-amber-300/80">通俗比喻</p>
                <p className="prose-reading text-[14px] text-slate-200">{principle.analogy}</p>
              </div>
            )}
            {principle.architecture_flow && (
              <div>
                <p className="section-label mb-1.5 text-sky-300/80">数据流动</p>
                <p className="prose-reading text-[14px] text-slate-200">{principle.architecture_flow}</p>
              </div>
            )}
            {Array.isArray(principle.key_formulas) && principle.key_formulas.length > 0 && (
              <div>
                <p className="section-label mb-2 text-indigo-300/80">关键公式（白话）</p>
                <ul className="space-y-2">
                  {principle.key_formulas.map((f, i) => (
                    <li key={i} className="rounded-lg border border-slate-800/80 bg-slate-950/35 px-3 py-2.5">
                      {f.name && <p className="text-xs font-semibold text-indigo-200">{f.name}</p>}
                      {f.plain && <p className="mt-1 text-sm leading-7 text-slate-300">{f.plain}</p>}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </ReviewBlock>
      )}

      {/* Innovations — vs previous work */}
      {data.innovations && (data.innovations.previous_work || data.innovations.this_work || data.innovations.why_better) && (
        <ReviewBlock icon={<GitBranch size={14} />} title="关键创新点">
          <div className="responsive-card-grid">
            {data.innovations.previous_work && (
              <InnovationCard
                tone="slate"
                label="以前是怎么做的"
                text={data.innovations.previous_work}
              />
            )}
            {data.innovations.this_work && (
              <InnovationCard
                tone="indigo"
                label="这篇论文怎么做"
                text={data.innovations.this_work}
              />
            )}
            {data.innovations.why_better && (
              <InnovationCard
                tone="emerald"
                label="为什么更好"
                text={data.innovations.why_better}
              />
            )}
          </div>
          <div className="mt-3 hidden md:flex items-center justify-center gap-2 text-slate-600">
            <span className="text-[10px] tracking-widest uppercase">Previous</span>
            <ArrowRight size={12} />
            <span className="text-[10px] tracking-widest uppercase text-indigo-400/80">This Work</span>
            <ArrowRight size={12} />
            <span className="text-[10px] tracking-widest uppercase text-emerald-400/80">Better</span>
          </div>
        </ReviewBlock>
      )}

      {/* Experimental gains */}
      {data.experimental_gains && (
        <ReviewBlock icon={<Award size={14} />} title="实验效果比前人好在哪">
          <p className="prose-reading text-[14px]">{data.experimental_gains}</p>
        </ReviewBlock>
      )}

      {/* Techniques */}
      {Array.isArray(data.techniques) && data.techniques.length > 0 && (
        <ReviewBlock icon={<Wrench size={14} />} title="技术方法" meta={`${data.techniques.length}`}>
          <div className="responsive-card-grid">
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
        <div className="grid gap-4 lg:grid-cols-2">
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

      {/* Historical position */}
      {data.historical_position && (data.historical_position.builds_on || data.historical_position.inspired || data.historical_position.overall) && (
        <ReviewBlock icon={<History size={14} />} title="背景地位">
          <div className="space-y-3">
            {data.historical_position.overall && (
              <p className="prose-reading text-[14px] text-slate-200">{data.historical_position.overall}</p>
            )}
            <div className="grid gap-3 lg:grid-cols-2">
              {data.historical_position.builds_on && (
                <div className="rounded-lg border border-slate-800/80 bg-slate-950/35 px-3.5 py-3">
                  <p className="section-label mb-1.5 text-slate-400">站在谁的肩上</p>
                  <p className="text-sm leading-7 text-slate-300">{data.historical_position.builds_on}</p>
                </div>
              )}
              {data.historical_position.inspired && (
                <div className="rounded-lg border border-slate-800/80 bg-slate-950/35 px-3.5 py-3">
                  <p className="section-label mb-1.5 text-fuchsia-300/80">启发了谁</p>
                  <p className="text-sm leading-7 text-slate-300">{data.historical_position.inspired}</p>
                </div>
              )}
            </div>
          </div>
        </ReviewBlock>
      )}

      {/* Limitations */}
      {data.limitations && (
        <ReviewBlock icon={<AlertTriangle size={14} />} title="这里的坑（局限性）">
          <p className="prose-reading text-[14px] text-amber-100/90">{data.limitations}</p>
        </ReviewBlock>
      )}

      {/* PyTorch minimal implementation */}
      {data.pytorch_snippet && data.pytorch_snippet.code && (
        <ReviewBlock
          icon={<Code2 size={14} />}
          title="PyTorch 最简实现"
          meta={data.pytorch_snippet.module_name}
        >
          <div className="space-y-3">
            <CodeBlock code={data.pytorch_snippet.code} />
            {data.pytorch_snippet.notes && (
              <p className="text-xs leading-6 text-slate-500">
                <span className="text-slate-400">笔记：</span>
                {data.pytorch_snippet.notes}
              </p>
            )}
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

function InnovationCard({
  tone, label, text,
}: { tone: 'slate' | 'indigo' | 'emerald'; label: string; text: string }) {
  const styles = {
    slate:   'border-slate-700/70 bg-slate-950/40 text-slate-300',
    indigo:  'border-indigo-500/30 bg-indigo-500/10 text-indigo-100',
    emerald: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-100',
  }[tone]
  const labelTone = {
    slate:   'text-slate-500',
    indigo:  'text-indigo-300/90',
    emerald: 'text-emerald-300/90',
  }[tone]
  return (
    <div className={`rounded-lg border px-3.5 py-3 ${styles}`}>
      <p className={`section-label mb-1.5 ${labelTone}`}>{label}</p>
      <p className="text-sm leading-7">{text}</p>
    </div>
  )
}

function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // ignore
    }
  }
  return (
    <div className="relative">
      <button
        onClick={onCopy}
        className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-md border border-slate-800 bg-slate-950/80 px-2 py-1 text-[11px] leading-none text-slate-400 transition-colors hover:border-slate-700 hover:text-slate-200"
      >
        {copied ? <Check size={11} /> : <Copy size={11} />}
        {copied ? '已复制' : '复制'}
      </button>
      <pre className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-3 pt-10 font-mono text-[12px] leading-6 text-slate-200 sm:px-4 sm:pt-3 sm:pr-20">
        <code>{code}</code>
      </pre>
    </div>
  )
}

function ReviewBlock({
  icon, title, meta, children,
}: { icon: React.ReactNode; title: string; meta?: string; children: React.ReactNode }) {
  return (
    <section className="overflow-hidden rounded-xl border border-slate-800/80 bg-slate-900/35 shadow-[0_12px_28px_rgba(2,6,23,0.16)]">
      <div className="flex min-h-12 items-center gap-2.5 border-b border-slate-800/70 bg-slate-950/25 px-3 py-2.5 sm:px-4">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-slate-700/70 bg-slate-900 text-slate-400">
          {icon}
        </span>
        <h3 className="min-w-0 text-sm font-semibold tracking-tight text-slate-100 text-safe-wrap">{title}</h3>
        {meta && (
          <span className="ml-auto max-w-[45%] truncate rounded-md border border-slate-700/70 bg-slate-900 px-2 py-0.5 text-xs tabular-nums text-slate-400">
            {meta}
          </span>
        )}
      </div>
      <div className="px-3 py-4 text-sm sm:px-4">{children}</div>
    </section>
  )
}

function parseExtractionResponse(raw: string): PaperExtraction | null {
  let text = raw.replace(/【[^】]*?†[^】]*?】/g, '').trim()
  if (text.startsWith('```')) {
    const lines = text.split('\n')
    if (lines[0]?.startsWith('```')) lines.shift()
    if (lines.at(-1)?.startsWith('```')) lines.pop()
    text = lines.join('\n').trim()
  }

  try {
    return JSON.parse(text) as PaperExtraction
  } catch {
    const start = text.indexOf('{')
    const end = text.lastIndexOf('}')
    if (start >= 0 && end > start) {
      try {
        return JSON.parse(text.slice(start, end + 1)) as PaperExtraction
      } catch {
        return null
      }
    }
    return null
  }
}

type NoteBlockData = { id: string; title: string; content: string }

const NOTES_V2_MARKER = '<!--notes-v2-->'

function parseNoteBlocks(raw: string | null | undefined): NoteBlockData[] {
  const text = (raw || '').trim()
  if (!text) return []
  if (text.startsWith(NOTES_V2_MARKER)) {
    try {
      const parsed = JSON.parse(text.slice(NOTES_V2_MARKER.length).trim())
      if (Array.isArray(parsed)) {
        return parsed
          .filter((b): b is Record<string, unknown> => !!b && typeof b === 'object')
          .map((b, i) => ({
            id: typeof b.id === 'string' && b.id ? b.id : `b-${i}-${Date.now()}`,
            title: typeof b.title === 'string' ? b.title : '',
            content: typeof b.content === 'string' ? b.content : '',
          }))
      }
    } catch {
      // fall through to legacy
    }
  }
  return [{ id: 'legacy', title: '', content: raw || '' }]
}

function serializeNoteBlocks(blocks: NoteBlockData[]): string {
  if (blocks.length === 0) return ''
  return `${NOTES_V2_MARKER}\n${JSON.stringify(blocks)}`
}

function newBlockId(): string {
  return `b-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function NotesSection({
  paper, onUpdate,
}: {
  paper: PaperDetail
  onUpdate: (paper: PaperDetail) => void
}) {
  const [blocks, setBlocks] = useState<NoteBlockData[]>(() => parseNoteBlocks(paper.notes))
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draftTitle, setDraftTitle] = useState('')
  const [draftContent, setDraftContent] = useState('')
  const [isNewBlock, setIsNewBlock] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const persist = async (nextBlocks: NoteBlockData[]) => {
    setSaving(true)
    setError(null)
    try {
      const updated = await updatePaperNotes(paper.id, serializeNoteBlocks(nextBlocks))
      onUpdate(updated)
      setBlocks(nextBlocks)
      return true
    } catch (e) {
      setError(getApiErrorMessage(e))
      return false
    } finally {
      setSaving(false)
    }
  }

  const startEdit = (block: NoteBlockData) => {
    setEditingId(block.id)
    setDraftTitle(block.title)
    setDraftContent(block.content)
    setIsNewBlock(false)
    setError(null)
  }

  const startNew = () => {
    const id = newBlockId()
    setBlocks(prev => [...prev, { id, title: '', content: '' }])
    setEditingId(id)
    setDraftTitle('')
    setDraftContent('')
    setIsNewBlock(true)
    setError(null)
  }

  const cancelEdit = () => {
    if (isNewBlock && editingId) {
      setBlocks(prev => prev.filter(b => b.id !== editingId))
    }
    setEditingId(null)
    setIsNewBlock(false)
    setError(null)
  }

  const saveEdit = async () => {
    if (!editingId) return
    const next = blocks.map(b =>
      b.id === editingId ? { ...b, title: draftTitle.trim(), content: draftContent } : b
    )
    const ok = await persist(next)
    if (ok) {
      setEditingId(null)
      setIsNewBlock(false)
    }
  }

  const deleteBlock = async (id: string) => {
    if (!confirm('确定删除这个笔记块吗？')) return
    const next = blocks.filter(b => b.id !== id)
    const ok = await persist(next)
    if (ok && editingId === id) {
      setEditingId(null)
      setIsNewBlock(false)
    }
  }

  return (
    <ReviewBlock
      icon={<NotebookPen size={14} />}
      title="个人笔记"
      meta={blocks.length > 0 ? `${blocks.length}` : undefined}
    >
      <div className="space-y-3">
        {blocks.length === 0 && (
          <p className="inline-flex items-center gap-1.5 text-sm text-slate-500">
            <Eye size={13} /> 还没有笔记。可以按主题分块，每块只聚焦一个想法。
          </p>
        )}

        {blocks.map(block => (
          <NoteBlockCard
            key={block.id}
            block={block}
            editing={editingId === block.id}
            draftTitle={draftTitle}
            draftContent={draftContent}
            error={editingId === block.id ? error : null}
            saving={saving && editingId === block.id}
            locked={editingId !== null && editingId !== block.id}
            onDraftTitleChange={setDraftTitle}
            onDraftContentChange={setDraftContent}
            onStartEdit={() => startEdit(block)}
            onCancel={cancelEdit}
            onSave={saveEdit}
            onDelete={() => deleteBlock(block.id)}
          />
        ))}

        <button
          onClick={startNew}
          disabled={editingId !== null || saving}
          className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-slate-700/70 bg-slate-950/30 px-3 py-2 text-sm text-slate-400 transition-colors hover:border-indigo-500/50 hover:bg-indigo-500/5 hover:text-indigo-200 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-slate-700/70 disabled:hover:bg-slate-950/30 disabled:hover:text-slate-400"
        >
          <Plus size={13} /> 新增笔记块
        </button>
      </div>
    </ReviewBlock>
  )
}

function NoteBlockCard({
  block, editing, draftTitle, draftContent, error, saving, locked,
  onDraftTitleChange, onDraftContentChange,
  onStartEdit, onCancel, onSave, onDelete,
}: {
  block: NoteBlockData
  editing: boolean
  draftTitle: string
  draftContent: string
  error: string | null
  saving: boolean
  locked: boolean
  onDraftTitleChange: (value: string) => void
  onDraftContentChange: (value: string) => void
  onStartEdit: () => void
  onCancel: () => void
  onSave: () => void
  onDelete: () => void
}) {
  if (editing) {
    return (
      <div className="space-y-3 rounded-lg border border-indigo-500/30 bg-slate-950/40 px-3 py-3 shadow-[0_8px_24px_rgba(49,46,129,0.16)]">
        {error && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm leading-6 text-red-200">
            {error}
          </div>
        )}
        <input
          type="text"
          value={draftTitle}
          onChange={e => onDraftTitleChange(e.target.value)}
          placeholder="标题（可选）"
          className="w-full rounded-md border border-slate-800 bg-slate-950/60 px-2.5 py-1.5 text-sm text-slate-200 outline-none transition-colors focus:border-indigo-500/60 placeholder:text-slate-600"
        />
        <textarea
          value={draftContent}
          onChange={e => onDraftContentChange(e.target.value)}
          spellCheck={false}
          autoFocus
          placeholder="用 Markdown 写下这块笔记的内容…"
          className="min-h-[14rem] w-full resize-y rounded-md border border-slate-800 bg-slate-950/60 px-3 py-2.5 font-mono text-xs leading-6 text-slate-200 outline-none transition-colors focus:border-indigo-500/60 placeholder:text-slate-600"
        />
        <div className="flex flex-wrap items-center justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={saving}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700/70 bg-slate-900 px-3 py-1.5 text-sm text-slate-300 transition-colors hover:bg-slate-800 hover:text-white disabled:cursor-not-allowed disabled:text-slate-600"
          >
            <X size={13} /> 取消
          </button>
          <button
            onClick={onSave}
            disabled={saving}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-indigo-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
          >
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
            保存
          </button>
        </div>
      </div>
    )
  }

  const hasContent = (block.content || '').trim().length > 0

  return (
    <div className={`group rounded-lg border border-slate-800/80 bg-slate-950/35 px-3.5 py-3 transition-colors hover:border-slate-700/80 ${locked ? 'opacity-60' : ''}`}>
      <div className="mb-2 flex items-start gap-2">
        <div className="min-w-0 flex-1">
          {block.title ? (
            <h4 className="text-sm font-semibold leading-6 text-slate-100">{block.title}</h4>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100">
          <button
            onClick={onStartEdit}
            disabled={locked}
            title="编辑"
            className="rounded-md border border-slate-800 bg-slate-950/50 p-1 text-slate-400 transition-colors hover:border-slate-700 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Pencil size={11} />
          </button>
          <button
            onClick={onDelete}
            disabled={locked}
            title="删除"
            className="rounded-md border border-slate-800 bg-slate-950/50 p-1 text-slate-400 transition-colors hover:border-red-500/40 hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Trash2 size={11} />
          </button>
        </div>
      </div>
      {hasContent ? (
        <MarkdownView source={block.content} />
      ) : (
        <p className="text-sm italic text-slate-600">（空白笔记）</p>
      )}
    </div>
  )
}

function MarkdownView({ source }: { source: string }) {
  return (
    <div className="markdown-notes text-sm leading-7 text-slate-200">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{source}</ReactMarkdown>
    </div>
  )
}

function getApiErrorMessage(error: unknown): string {
  const apiError = error as { response?: { data?: { detail?: string } }; message?: string }
  return apiError.response?.data?.detail || apiError.message || '操作失败'
}
